#!/usr/bin/env python
"""Live check: the collection archive round-trip, end to end against a running server.

A collection archive is the one supported way to seed a whole collection at once —
documents, context cards, and the links between them — in a single upload. It is the
same zip the export produces, so "restore a backup", "move a collection between
deployments" and "bulk-load a knowledge base an external system generated" are all
the same operation.

This walks the flow a real caller walks and asserts on what comes back:

    1. build an archive          (the format, demonstrated rather than described)
    2. import as a new collection
    3. wait for indexing         (documents are async; cards are immediate)
    4. look a term up            (exact key, no model in the loop)
    5. check the card's links resolved to the documents that shipped with it
    6. import the SAME archive again — cards must UPDATE, not duplicate
    7. import it ASYNCHRONOUSLY (#715) — the request must answer at once, and the
       run must report what landed

Step 6 is the one worth running twice: a re-import is how anyone corrects a typo in a
generated archive, and until #701 it doubled every card in the collection. Step 7 is
the path a machine uses — the synchronous one holds the request open for as long as
the archive takes to write, which is how a 207 MB upload earned a 504 (#715).

The optional final step asks a question with an image attached — the platform describes it
with a VLM and searches on that description. It needs `kb.vlm_llm` configured, so it is
opt-in via --ask and skipped (loudly) when the server rejects the image.

Usage:

    uv run python scripts/check_collection_archive.py --base-url http://127.0.0.1:8000
    uv run python scripts/check_collection_archive.py --ask          # + the image turn
    uv run python scripts/check_collection_archive.py --keep         # leave the zip on disk

Exits non-zero on the first failed expectation, so it doubles as a smoke test.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import zipfile

import httpx

# The archive's manifest lives at a reserved dot-path so it can never collide with a
# real document; every other member of the zip IS a document, stored at its path.
MANIFEST_PATH = ".kb-collection/manifest.json"

# A 1×1 PNG. Real bytes rather than a placeholder string: the ingest path sniffs the
# content, so a fake would be filed as something else and the image branch never run.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def build_archive(*, code: str = "M4", body: str = "Edge chipping.") -> bytes:
    """One collection archive, in memory.

    The shape is the whole point of this script, so it is written out literally:

        <zip>
        ├── .kb-collection/manifest.json     collection settings + context cards
        ├── M4/description.md                a document — anything not the manifest is one
        ├── M4/raw-01.png                    another document
        └── M4/annotated-01.png              …and another

    A card names its documents by PATH, never by id: an id encodes the collection it
    came from, so replaying ids would leave every link dangling the moment the archive
    is imported anywhere else. The importer re-mints them against the target.
    """
    description = f"# {code}\n\n代號:{code}\n\n## 長什麼樣\n邊緣呈鋸齒狀崩落,集中在外圈。\n"
    members = {
        f"{code}/description.md": description.encode(),
        f"{code}/raw-01.png": _PNG_1X1,
        f"{code}/annotated-01.png": _PNG_1X1,
    }
    manifest = {
        "version": 1,
        "collection": {"name": f"archive-check-{code}", "use_rag": True},
        "context_cards": [
            {
                "keys": [code],
                "title": f"{code} — 邊緣崩角",
                "body": body,
                # Omit this field entirely and a re-import KEEPS whatever links the card
                # already has; an explicit [] clears them. Silence is not a claim.
                "reference_paths": [f"{code}/description.md", f"{code}/annotated-01.png"],
            }
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in members.items():
            zf.writestr(path, data)
        zf.writestr(MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()


def _say(step: str, detail: str = "") -> None:
    print(f"  {step:<34} {detail}", file=sys.stderr)


def _expect(ok: bool, what: str) -> None:
    if not ok:
        print(f"\nFAILED: {what}", file=sys.stderr)
        raise SystemExit(1)


def import_new(client: httpx.Client, zip_bytes: bytes) -> str:
    r = client.post(
        "/kb/collections/import", files={"file": ("archive.zip", zip_bytes, "application/zip")}
    )
    _expect(r.status_code == 200, f"import returned {r.status_code}: {r.text[:300]}")
    return r.json()["collection_id"]


def import_into(client: httpx.Client, cid: str, zip_bytes: bytes, mode: str) -> None:
    r = client.post(
        f"/kb/collections/{cid}/import",
        params={"mode": mode},
        files={"file": ("archive.zip", zip_bytes, "application/zip")},
    )
    _expect(r.status_code == 200, f"re-import ({mode}) returned {r.status_code}: {r.text[:300]}")


def import_async(client: httpx.Client, zip_bytes: bytes) -> dict:
    """Start an asynchronous import and return the accepted response.

    The point of the 202 is that it comes back before the documents exist, so the
    check below asserts on WHAT it says (both ids, the document count) rather than
    timing it — a wall-clock assertion would be flaky on a loaded machine and
    would not prove the contract anyway."""
    r = client.post(
        "/kb/collections/imports", files={"file": ("archive.zip", zip_bytes, "application/zip")}
    )
    _expect(r.status_code == 202, f"async import returned {r.status_code}: {r.text[:300]}")
    return r.json()


def await_import(client: httpx.Client, import_id: str, *, timeout_s: float = 120.0) -> dict:
    """Poll a run until it finishes. Reports what the run says rather than assuming
    success: a finished run can still carry per-document errors, which is the whole
    reason it reports them."""
    deadline = time.monotonic() + timeout_s
    while True:
        r = client.get(f"/kb/collections/imports/{import_id}")
        _expect(r.status_code == 200, f"polling the import returned {r.status_code}")
        run = r.json()
        if run.get("finished"):
            return run
        if time.monotonic() > deadline:
            _expect(False, f"import {import_id} still unfinished after {timeout_s:.0f}s: {run}")
        time.sleep(0.5)


def documents(client: httpx.Client, cid: str) -> list[dict]:
    r = client.get(f"/kb/collections/{cid}/documents")
    _expect(r.status_code == 200, f"listing documents returned {r.status_code}")
    return r.json()["items"]


def await_indexing(client: httpx.Client, cid: str, *, timeout_s: float = 120.0) -> list[dict]:
    """Documents index asynchronously; cards do not. Poll until nothing is `indexing`.

    Reports the terminal status of each document rather than only "done": a doc that
    ends in `error` still leaves the poll loop, and treating that as success is how a
    seeding script quietly produces an empty collection.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        docs = documents(client, cid)
        pending = [d for d in docs if d.get("status") == "indexing"]
        if not pending:
            return docs
        if time.monotonic() > deadline:
            _expect(False, f"still indexing after {timeout_s:.0f}s: {[d['path'] for d in pending]}")
        time.sleep(1.0)


def lookup(client: httpx.Client, cid: str, term: str) -> list[dict]:
    r = client.post(f"/kb/collections/{cid}/context-cards/lookup", json={"terms": [term]})
    _expect(r.status_code == 200, f"lookup returned {r.status_code}")
    return r.json()["results"][term]


def cards_in(client: httpx.Client, cid: str) -> list[dict]:
    """Every card in the collection, through specstar's generated list route."""
    r = client.get("/context-card", params={"qb": f"QB['collection_id'] == '{cid}'"})
    _expect(r.status_code == 200, f"listing cards returned {r.status_code}")
    return [row["data"] for row in r.json()]


def ask_with_image(client: httpx.Client, cid: str, question: str) -> str | None:
    """Attach a throwaway image to a question. Returns None when no VLM is configured.

    The image is never stored: the platform describes it, searches on the description,
    and discards it. So this is a question with a picture, not an upload.
    """
    r = client.post("/kb/chats", json={"title": "archive-check", "collection_ids": [cid]})
    _expect(r.status_code in (200, 201), f"creating a chat returned {r.status_code}")
    chat_id = r.json()["resource_id"] if "resource_id" in r.json() else r.json()["id"]

    payload = {
        "content": question,
        "image": {"data": base64.b64encode(_PNG_1X1).decode(), "mime": "image/png"},
    }
    with client.stream("POST", f"/kb/chats/{chat_id}/messages", json=payload) as resp:
        if resp.status_code == 400:
            resp.read()
            return None  # no VLM wired — the server says so rather than guessing
        _expect(resp.status_code == 200, f"asking returned {resp.status_code}")
        return "".join(line for line in resp.iter_lines())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="server ROOT, e.g. http://127.0.0.1:8000 — every backend route lives "
        "under its /api prefix, which this script appends for you",
    )
    ap.add_argument("--code", default="M4", help="the term the seeded card is keyed under")
    ap.add_argument("--ask", action="store_true", help="also run the image question (needs a VLM)")
    ap.add_argument("--keep", metavar="PATH", help="also write the archive here, to inspect it")
    ap.add_argument(
        "--archive-only",
        action="store_true",
        help="write --keep and stop — no server needed. The fastest way to see the "
        "format is to unzip a real one rather than read a description of it.",
    )
    args = ap.parse_args()
    if args.archive_only and not args.keep:
        ap.error("--archive-only needs --keep PATH to write to")

    zip_bytes = build_archive(code=args.code)
    if args.keep:
        with open(args.keep, "wb") as fh:
            fh.write(zip_bytes)
        _say("archive written", args.keep)
        if args.archive_only:
            return 0

    # Every backend route is mounted under `/api`; only the SPA is served at the root.
    api_root = args.base_url.rstrip("/") + "/api"
    with httpx.Client(base_url=api_root, timeout=60.0) as client:
        print("collection archive round-trip", file=sys.stderr)

        cid = import_new(client, zip_bytes)
        _say("1. imported", cid)

        docs = await_indexing(client, cid)
        by_status: dict[str, int] = {}
        for d in docs:
            by_status[d.get("status", "?")] = by_status.get(d.get("status", "?"), 0) + 1
        _say("2. documents", f"{len(docs)} — {by_status}")
        _expect(len(docs) == 3, f"expected 3 documents, got {len(docs)}")

        # The archive round-trip is what this checks; whether a deployment can READ an
        # image is a separate capability. An image document only indexes when a VLM is
        # reachable, so its failure is reported here but is fatal only under --ask,
        # which is the caller asserting this deployment has one. The markdown document
        # needs nothing but the text embedder, so it is always fatal.
        text_doc = next(d for d in docs if d["path"].endswith(".md"))
        _expect(
            text_doc.get("status") == "ready",
            f"the text document failed to index: {text_doc.get('status')}",
        )
        images = [d for d in docs if not d["path"].endswith(".md")]
        broken = [d["path"] for d in images if d.get("status") != "ready"]
        if broken:
            _say("", f"images not indexed: {broken} — needs a reachable VLM (kb.vlm_llm)")
            _expect(not args.ask, f"--ask asserts a working VLM, but {broken} failed to index")

        hits = lookup(client, cid, args.code)
        _say("3. lookup", f"{args.code} → {len(hits)} card(s)")
        _expect(len(hits) == 1, f"expected exactly one card for {args.code}, got {len(hits)}")

        (card,) = cards_in(client, cid)
        linked = card.get("reference_doc_ids", [])
        _say("4. card links", f"{len(linked)} document(s)")
        _expect(len(linked) == 2, f"expected 2 linked documents, got {len(linked)}")
        live = {d["resource_id"] for d in docs}
        _expect(set(linked) <= live, "a card links a document that is not in this collection")

        import_into(client, cid, zip_bytes, "overwrite")
        after = cards_in(client, cid)
        _say("5. re-import", f"{len(after)} card(s) after a second import")
        _expect(len(after) == 1, f"re-importing duplicated cards: {len(after)} (#701)")
        _expect(
            after[0].get("reference_doc_ids") == linked,
            "re-importing changed the card's links",
        )

        # 7. the asynchronous path (#715) — the one a machine pushing an archive uses.
        started = import_async(client, zip_bytes)
        _say("6. async import", f"202 → {started['members']} documents queued")
        _expect(started["status"] == "queued", f"expected status=queued, got {started['status']}")
        _expect(bool(started["collection_id"]), "the 202 did not carry a collection id")
        _expect(started["members"] == 3, f"expected 3 members, got {started['members']}")

        run = await_import(client, started["import_id"])
        _say("7. async finished", f"{run['written']}/{run['members']} written")
        _expect(
            run["written"] == run["members"],
            f"documents did not all land: {run['written']}/{run['members']} — {run['errors']}",
        )
        async_cards = cards_in(client, started["collection_id"])
        _expect(len(async_cards) == 1, f"expected 1 card, got {len(async_cards)}")

        if args.ask:
            answer = ask_with_image(client, cid, f"這張圖是哪一種?可能是 {args.code} 嗎?")
            if answer is None:
                _say("8. image question", "SKIPPED — no kb.vlm_llm configured on this server")
            else:
                _say("8. image question", f"{len(answer)} bytes of stream")
                _expect(bool(answer.strip()), "the image turn produced an empty stream")

        print(f"\nOK — collection {cid} at {args.base_url}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
