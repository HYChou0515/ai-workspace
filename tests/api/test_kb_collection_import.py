"""Issue #101: collection import — round-trip an exported zip back into a
collection (new or existing). The manifest restores collection settings +
context cards; members are stored verbatim at their paths and re-indexed.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator

from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _client_with_spec() -> tuple[TestClient, SpecStar]:
    """A client plus the spec behind it, for the few assertions about stored fields the
    read API deliberately doesn't expose (e.g. a card's `reference_doc_ids`)."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app), spec


def _client() -> TestClient:
    return _client_with_spec()[0]


def _export_zip(client: TestClient, cid: str) -> bytes:
    did = client.post(f"/kb/collections/{cid}/download/prepare").json()["download_id"]
    return client.get(f"/kb/collections/{cid}/download/{did}").content


def _blob(client: TestClient, doc: dict) -> bytes:
    return client.get(f"/source-doc/{doc['resource_id']}/blobs/{doc['file_id']}").content


def _make_zip(files: dict[str, bytes], manifest: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
        if manifest is not None:
            zf.writestr(".kb-collection/manifest.json", json.dumps(manifest))
    return buf.getvalue()


def _docs_by_path(client: TestClient, cid: str) -> dict[str, dict]:
    return {d["path"]: d for d in client.get(f"/kb/collections/{cid}/documents").json()["items"]}


def _import_new(client: TestClient, zip_bytes: bytes, filename: str = "u.zip") -> str:
    r = client.post(
        "/kb/collections/import",
        files={"file": (filename, zip_bytes, "application/zip")},
    )
    assert r.status_code == 200, r.text
    return r.json()["collection_id"]


def test_round_trip_import_creates_a_new_collection():
    client = _client()
    cid = client.post(
        "/kb/collections",
        json={"name": "Specs", "description": "team", "icon": "book"},
    ).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"# Guide\nhello", "text/markdown")},
    )
    client.post(
        "/context-card/author",
        json={"collection_id": cid, "keys": ["M4"], "title": "M4", "body": "metal 4"},
    )

    zip_bytes = _export_zip(client, cid)
    r = client.post(
        "/kb/collections/import",
        files={"file": ("Specs.zip", zip_bytes, "application/zip")},
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["collection_id"] and new["collection_id"] != cid
    assert new["status"] == "indexing"
    new_cid = new["collection_id"]

    docs = client.get(f"/kb/collections/{new_cid}/documents").json()["items"]
    guide = next(d for d in docs if d["path"] == "guide.md")
    assert _blob(client, guide) == b"# Guide\nhello"

    card = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == new_cid)
    assert card["name"] == "Specs"
    assert card["description"] == "team"
    assert card["icon"] == "book"

    hits = client.post(
        f"/kb/collections/{new_cid}/context-cards/lookup",
        json={"terms": ["M4"]},
    ).json()
    assert hits["results"]["M4"][0]["body"] == "metal 4"


def test_import_into_existing_overwrites_on_overwrite_mode():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "C"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("x.md", b"old", "text/markdown")},
    )
    zbytes = _make_zip({"x.md": b"new", "y.md": b"brand"})

    r = client.post(
        f"/kb/collections/{cid}/import?mode=overwrite",
        files={"file": ("c.zip", zbytes, "application/zip")},
    )
    assert r.status_code == 200, r.text

    docs = _docs_by_path(client, cid)
    assert _blob(client, docs["x.md"]) == b"new"  # collided path overwritten
    assert _blob(client, docs["y.md"]) == b"brand"  # new path added


def test_import_into_existing_keeps_existing_on_skip_mode():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "C"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("x.md", b"old", "text/markdown")},
    )
    zbytes = _make_zip({"x.md": b"new", "y.md": b"brand"})

    r = client.post(
        f"/kb/collections/{cid}/import?mode=skip",
        files={"file": ("c.zip", zbytes, "application/zip")},
    )
    assert r.status_code == 200, r.text

    docs = _docs_by_path(client, cid)
    assert _blob(client, docs["x.md"]) == b"old"  # collided path kept
    assert _blob(client, docs["y.md"]) == b"brand"  # new path still added


def test_manifestless_zip_imports_docs_only_named_after_the_file():
    client = _client()
    zbytes = _make_zip({"notes/a.md": b"hi", "b.md": b"yo"})

    new_cid = _import_new(client, zbytes, filename="MyArchive.zip")

    card = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == new_cid)
    assert card["name"] == "MyArchive"  # named after the uploaded file
    assert set(_docs_by_path(client, new_cid)) == {"notes/a.md", "b.md"}


def test_import_stamps_the_importer_not_the_manifest_uploader():
    client = _client()
    manifest = {
        "version": 1,
        "collection": {"name": "Ghosted"},
        "documents": [{"path": "a.md", "created_by": "ghost"}],
        "context_cards": [],
    }
    zbytes = _make_zip({"a.md": b"hi"}, manifest=manifest)

    new_cid = _import_new(client, zbytes)

    doc = client.get(f"/kb/collections/{new_cid}/documents").json()["items"][0]
    owner = next(c for c in client.get("/kb/collections").json() if c["resource_id"] == new_cid)[
        "owner"
    ]
    assert doc["created_by"] != "ghost"  # manifest uploader is informational only
    assert doc["created_by"] == owner  # stamped as the importing user


def test_import_drops_path_escaping_members():
    client = _client()
    zbytes = _make_zip({"../evil.md": b"x", "ok.md": b"y"})

    new_cid = _import_new(client, zbytes)

    # the zip-slip member is dropped (canonical_path rejects it); the rest import
    assert set(_docs_by_path(client, new_cid)) == {"ok.md"}


def test_import_into_existing_rejects_bad_mode_and_unknown_collection():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "C"}).json()["resource_id"]
    zbytes = _make_zip({"a.md": b"hi"})

    bad_mode = client.post(
        f"/kb/collections/{cid}/import?mode=bogus",
        files={"file": ("z.zip", zbytes, "application/zip")},
    )
    assert bad_mode.status_code == 400

    unknown = client.post(
        "/kb/collections/does-not-exist/import",
        files={"file": ("z.zip", zbytes, "application/zip")},
    )
    assert unknown.status_code == 404


def test_import_skips_directory_entries_and_dot_members():
    client = _client()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sub/", b"")  # an explicit directory entry → not a document
        zf.writestr(".", b"junk")  # canonicalises to empty → dropped
        zf.writestr("real.md", b"hello")

    new_cid = _import_new(client, buf.getvalue())

    assert set(_docs_by_path(client, new_cid)) == {"real.md"}


def test_overwrite_identical_bytes_is_a_noop():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "C"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("x.md", b"same", "text/markdown")},
    )
    zbytes = _make_zip({"x.md": b"same"})  # byte-identical to the existing doc

    r = client.post(
        f"/kb/collections/{cid}/import?mode=overwrite",
        files={"file": ("c.zip", zbytes, "application/zip")},
    )
    assert r.status_code == 200
    # identical bytes → no new revision, so nothing is reported as (re)imported
    assert r.json()["document_ids"] == []


def test_import_remints_card_links_against_the_target_collection():
    """#518: the manifest carries a card's links as PATHS, so importing re-mints them as
    ids in the collection being imported INTO. Without this a round-trip would restore
    links pointing at the source collection's documents — dangling on arrival."""
    from workspace_app.kb.doc_id import encode_doc_id
    from workspace_app.resources.kb import ContextCard

    client, spec = _client_with_spec()
    manifest = {
        "version": 1,
        "collection": {"name": "G"},
        "documents": [{"path": "spec.md"}],
        "context_cards": [
            {"keys": ["M4"], "title": "M4", "body": "b", "reference_paths": ["spec.md"]}
        ],
    }
    new_cid = _import_new(client, _make_zip({"spec.md": b"the metal 4 spec"}, manifest=manifest))

    rm = spec.get_resource_manager(ContextCard)
    (card,) = [r.data for r in rm.list_resources((QB["collection_id"] == new_cid).build())]
    assert isinstance(card, ContextCard)  # narrow Struct|Unset for ty
    assert card.reference_doc_ids == [encode_doc_id(new_cid, "spec.md")]


def test_import_restores_only_keyable_cards():
    client = _client()
    manifest = {
        "version": 1,
        "collection": {"name": "G"},
        "documents": [],
        "context_cards": [
            {"keys": [], "title": "Reflow Zone", "body": "b1"},  # keyed by its title
            {"keys": [], "title": "", "body": "b2"},  # nothing to key on → dropped
            {"keys": ["M4"], "title": "M4", "body": "b3"},
        ],
    }
    zbytes = _make_zip({}, manifest=manifest)

    new_cid = _import_new(client, zbytes)

    hits = client.post(
        f"/kb/collections/{new_cid}/context-cards/lookup",
        json={"terms": ["Reflow Zone", "M4"]},
    ).json()
    assert hits["results"]["Reflow Zone"][0]["body"] == "b1"  # title-fallback card kept
    assert hits["results"]["M4"][0]["body"] == "b3"
