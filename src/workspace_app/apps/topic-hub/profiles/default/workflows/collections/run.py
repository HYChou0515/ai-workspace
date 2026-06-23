"""``→collections`` workflow (topic-hub §12) — the canonical produce → review → commit,
with the review *content* living in files.

PRODUCE: an agent node classifies each upload into one of the Hub's collections
(``collections.json``, §5), writes a digest, and collects the unknown domain terms a
newcomer wouldn't know → ``plan/<f>.json`` (gated by ``choice_in`` — the agent never
holds a side-effecting tool). An agent node then writes those terms into a fill-in
``glossary.todo.md``.

REVIEW: a simple yes/no ``human_gate``. The *questions* are in ``glossary.todo.md`` —
the human fills the definitions in the IDE (or opens a sibling chat to have the LLM
help; shared FileStore, §3.1), then approves.

COMMIT (deterministic, idempotent): ``ingest_to_collection`` files each upload and
``upsert_context_card`` (§8, #111) authors a context card for each filled glossary
entry — create-or-update by key, so re-classifying a term updates its card instead of
duplicating it. Re-run replays completed steps (§9); nothing reaches a collection
before approval.

Loaded by file path (hyphenated slug) → absolute imports only.
"""

from __future__ import annotations

from typing import Any

from workspace_app.filestore.protocol import FileNotFound
from workspace_app.workflow import (
    agent_step,
    choice_in,
    collection_has,
    file_nonempty,
    human_gate,
)
from workspace_app.workflow.handle import WorkflowHandle

_GLOSSARY = "glossary.todo.md"

# Human-readable reasons for the "did nothing" outcomes (#100 observability). A
# no-op used to return a bare status token the UI showed as raw JSON; these are
# surfaced as the run's message so the user sees WHY nothing happened, and how to
# fix it. zh-TW per the app's UI language; refer to collections.json by the name
# the user edits it under (README §5).
_MSG_NO_COLLECTIONS = (
    "這個 Hub 還沒有設定任何知識庫，沒有可歸檔的目標。"
    "請先在知識庫清單（collections.json）加入至少一個知識庫，再重新執行。"
)
_MSG_MALFORMED_COLLECTIONS = (
    "知識庫清單（collections.json）有內容，但格式不正確、讀不到任何知識庫。"
    '每一項應為物件，例如 [{"id": "…", "name": "…"}]。請修正後再重新執行。'
)
_MSG_NO_FILES = "沒有找到要歸檔的檔案，已跳過。請把要歸檔的檔案放進 inputs/ 後再執行。"


async def _no_collections_result(wf: WorkflowHandle) -> dict[str, Any]:
    """Why did the collection set come back empty? Distinguish "no list yet"
    (empty / missing) from "list present but unparseable" (malformed) so the user
    gets a fixable reason instead of a silent no-op (#100)."""
    raw: Any = None
    malformed = False
    if await wf.exists("collections.json"):
        try:
            raw = await wf.read_json("collections.json")
        except ValueError:
            malformed = True  # not even valid JSON
    # A non-empty list that _read_collections still parsed to zero means every
    # entry was the wrong shape (e.g. bare strings) → malformed, not empty.
    if malformed or (isinstance(raw, list) and len(raw) > 0):
        return {"status": "malformed_collections", "message": _MSG_MALFORMED_COLLECTIONS}
    return {"status": "no_collections", "message": _MSG_NO_COLLECTIONS}


def _plan_path(f: str) -> str:
    return "plan/" + f.lstrip("/").replace("/", "_") + ".json"


async def _read_collections(wf: WorkflowHandle) -> list[str]:
    """The Hub's collection NAMES (the agent picks among these; ingest / card-author
    resolve a name → id). Tolerant of an absent / malformed hand-edited file."""
    try:
        data = await wf.read_json("collections.json")
    except (FileNotFound, ValueError):
        return []
    out: list[str] = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("id")
                if isinstance(name, str) and name:
                    out.append(name)
    return out


def _parse_glossary(text: str) -> list[tuple[str, str]]:
    """Parse the human-filled ``glossary.todo.md`` into ``(term, definition)`` pairs —
    only ``## <term>`` sections whose body the human actually filled in (non-blank)."""
    entries: list[tuple[str, str]] = []
    term: str | None = None
    body: list[str] = []

    def _flush() -> None:
        if term is not None and (filled := "\n".join(body).strip()):
            entries.append((term, filled))

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            term, body = line[3:].strip(), []
        elif term is not None:
            body.append(line)
    _flush()
    return entries


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    collections = await _read_collections(wf)
    if not collections:
        return await _no_collections_result(wf)
    files = await wf.glob(
        inputs.get("files", ["inputs/*"]),
        exclude=inputs.get("except", ["inputs/input.json"]),
    )
    if not files:
        return {"status": "empty", "files": 0, "message": _MSG_NO_FILES}

    # Phase 1 — CLASSIFY: pick a collection + digest + collect unknown terms, as data.
    plan: dict[str, Any] = {}
    for f in files:
        out = _plan_path(f)
        await agent_step(
            wf,
            phase="classify",
            name="classify_" + f.lstrip("/").replace("/", "_"),
            prompt=(
                f"Read the file {f}. Choose the single best collection for it from "
                f"{collections}. Write a one-line digest. List the domain terms or "
                f"abbreviations in it that a newcomer would not know. Then write a JSON "
                f'object {{"collection": <one of {collections}>, "digest": <text>, '
                f'"terms": [<term>, ...]}} to {out} with write_file (use edit_file if {out} '
                f"already exists). Output nothing else."
            ),
            tools=["read_file", "write_file", "edit_file"],
            check=choice_in(out, key="collection", allowed=collections),
            retries=2,
        )
        plan[f] = await wf.read_json(out)

    # Phase 2 — GLOSSARY: collect the unknown terms into a fill-in file for a human.
    terms = sorted(
        {
            t.strip()
            for f in files
            for t in (plan[f].get("terms") or [])
            if isinstance(t, str) and t.strip()
        }
    )
    await agent_step(
        wf,
        phase="glossary",
        name="glossary",
        prompt=(
            f"These domain terms were collected while classifying: {terms}. Write a fill-in "
            f"glossary to {_GLOSSARY} — one '## <term>' section per term with an empty line "
            f"under it for a human to write the definition. Use write_file (or edit_file if "
            f"{_GLOSSARY} already exists). Output nothing else."
        ),
        tools=["read_file", "write_file", "edit_file"],
        check=file_nonempty(_GLOSSARY),
        retries=2,
    )

    # Phase 3 — REVIEW: a simple yes/no; the questions live in glossary.todo.md (§12).
    decision = await human_gate(
        wf,
        phase="review",
        title="Filled in the glossary? Continue to commit?",
        summary={f: plan[f].get("collection") for f in files},
        allow=["approve", "reject"],
    )
    if decision.choice == "reject":
        return {"status": "rejected", "files": len(files)}

    # Phase 4 — COMMIT: ingest each upload + author a card per filled glossary entry.
    term_collection: dict[str, str] = {}
    for f in files:
        coll = plan[f].get("collection")
        for t in plan[f].get("terms") or []:
            if isinstance(t, str) and t.strip():
                term_collection.setdefault(t.strip(), coll)

    ingested = 0
    for f in files:
        coll = plan[f]["collection"]
        await wf.ingest_to_collection(coll, f, phase="commit")
        if (await collection_has(coll, f)(wf, None)).ok:
            ingested += 1

    cards = 0
    for term, body in _parse_glossary(await wf.read_text(_GLOSSARY)):
        await wf.upsert_context_card(
            term_collection.get(term, collections[0]),
            [term],
            title=term,
            body=body,
            phase="commit",
        )
        cards += 1

    return {"status": "approved", "ingested": ingested, "cards": cards}
