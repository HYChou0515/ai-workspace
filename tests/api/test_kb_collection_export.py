"""Issue #101: collection export — two-step prepare→stream download.

The export bundles a collection's source documents (original bytes at their
relative paths) plus a `.kb-collection/manifest.json` describing the collection
settings, documents, and context cards, so the archive round-trips through the
import endpoint.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from collections.abc import AsyncIterator

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.collection_export import (
    collection_zip_filename,
    downloads_dir,
    sweep_stale_downloads,
)
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _client() -> TestClient:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def _zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def test_prepare_then_stream_returns_zip_with_docs_and_manifest():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Specs"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("guide.md", b"# Guide\nhello", "text/markdown")},
    )

    prep = client.post(f"/kb/collections/{cid}/download/prepare")
    assert prep.status_code == 200
    body = prep.json()
    assert body["download_id"]
    assert body["filename"] == "Specs.zip"
    assert body["size"] > 0

    dl = client.get(f"/kb/collections/{cid}/download/{body['download_id']}")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/zip")
    assert "attachment" in dl.headers["content-disposition"]
    assert "Specs.zip" in dl.headers["content-disposition"]

    zf = _zip(dl.content)
    names = set(zf.namelist())
    assert "guide.md" in names
    assert zf.read("guide.md") == b"# Guide\nhello"

    manifest = json.loads(zf.read(".kb-collection/manifest.json"))
    assert manifest["version"] == 1
    assert manifest["collection"]["name"] == "Specs"
    assert "guide.md" in [d["path"] for d in manifest["documents"]]


def test_streaming_consumes_the_prepared_download():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Specs"}).json()["resource_id"]
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("a.md", b"hi", "text/markdown")},
    )
    did = client.post(f"/kb/collections/{cid}/download/prepare").json()["download_id"]

    assert client.get(f"/kb/collections/{cid}/download/{did}").status_code == 200
    # The temp file is deleted after the first send — a re-fetch 404s.
    assert client.get(f"/kb/collections/{cid}/download/{did}").status_code == 404


def test_manifest_carries_collection_settings_and_bytes_verbatim():
    client = _client()
    cid = client.post(
        "/kb/collections",
        json={
            "name": "Docs",
            "description": "team kb",
            "icon": "book",
            "use_rag": False,
            "use_wiki": True,
        },
    ).json()["resource_id"]
    client.patch(
        f"/collection/{cid}",
        json={"wiki_maintainer_guidance": "by zone", "wiki_reader_guidance": "tldr"},
    )
    # A nested path must be preserved and the stored bytes returned verbatim.
    body = b"# Logo notes\nline one\nline two\n"
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("img/logo.md", body, "text/markdown")},
    )

    did = client.post(f"/kb/collections/{cid}/download/prepare").json()["download_id"]
    zf = _zip(client.get(f"/kb/collections/{cid}/download/{did}").content)

    assert zf.read("img/logo.md") == body  # nested path preserved, bytes verbatim
    coll = json.loads(zf.read(".kb-collection/manifest.json"))["collection"]
    assert coll["description"] == "team kb"
    assert coll["icon"] == "book"
    assert coll["use_rag"] is False and coll["use_wiki"] is True
    assert coll["wiki_maintainer_guidance"] == "by zone"
    assert coll["wiki_reader_guidance"] == "tldr"


def test_manifest_carries_context_cards():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Glossary"}).json()["resource_id"]
    client.post(
        "/context-card/author",
        json={
            "collection_id": cid,
            "keys": ["M4", "Metal 4"],
            "title": "M4",
            "body": "the 4th metal layer",
        },
    )

    did = client.post(f"/kb/collections/{cid}/download/prepare").json()["download_id"]
    zf = _zip(client.get(f"/kb/collections/{cid}/download/{did}").content)

    cards = json.loads(zf.read(".kb-collection/manifest.json"))["context_cards"]
    assert len(cards) == 1
    assert cards[0]["keys"] == ["M4", "Metal 4"]
    assert cards[0]["title"] == "M4"
    assert cards[0]["body"] == "the 4th metal layer"


def test_prepare_unknown_collection_is_404():
    client = _client()
    assert client.post("/kb/collections/does-not-exist/download/prepare").status_code == 404


def test_stream_rejects_malformed_or_unknown_download_id():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "X"}).json()["resource_id"]
    # non-hex / wrong length → rejected by the guard without touching the fs
    assert client.get(f"/kb/collections/{cid}/download/not-a-token").status_code == 404
    assert client.get(f"/kb/collections/{cid}/download/deadbeef").status_code == 404
    # well-formed but no such prepared file → 404
    assert client.get(f"/kb/collections/{cid}/download/{'0' * 32}").status_code == 404


def test_empty_collection_exports_manifest_only():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Empty"}).json()["resource_id"]
    did = client.post(f"/kb/collections/{cid}/download/prepare").json()["download_id"]
    zf = _zip(client.get(f"/kb/collections/{cid}/download/{did}").content)

    assert zf.namelist() == [".kb-collection/manifest.json"]
    manifest = json.loads(zf.read(".kb-collection/manifest.json"))
    assert manifest["documents"] == []
    assert manifest["context_cards"] == []


def test_stream_404_when_collection_deleted_after_prepare():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Gone"}).json()["resource_id"]
    did = client.post(f"/kb/collections/{cid}/download/prepare").json()["download_id"]
    # The collection vanishes between prepare and stream → the stream 404s even
    # though the prepared file is still on disk.
    client.delete(f"/collection/{cid}/permanently")
    assert client.get(f"/kb/collections/{cid}/download/{did}").status_code == 404


def test_sweep_removes_stale_but_keeps_fresh_downloads():
    d = downloads_dir()
    stale = d / "stale1234567890abcdef1234567890ab.zip"
    fresh = d / "fresh1234567890abcdef1234567890ab.zip"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old = time.time() - 10_000
    os.utime(stale, (old, old))

    sweep_stale_downloads(ttl_seconds=3600)

    assert not stale.exists()  # older than the TTL → reaped
    assert fresh.exists()  # within the TTL → kept
    fresh.unlink()


def test_collection_zip_filename_sanitizes_and_falls_back():
    assert collection_zip_filename("My Report") == "My Report.zip"
    assert collection_zip_filename("a/b:c") == "a_b_c.zip"  # path/illegal chars → _
    assert collection_zip_filename("   ") == "collection.zip"  # blank → fallback name
