"""Issue #247: KB folder/root raw-file download — two-step prepare→stream.

Unlike the #101 collection export (round-trippable, with a manifest), the folder
download is a plain ZIP of the ORIGINAL bytes of every document under a path
prefix, entries rooted at the selected folder. `prefix=""` zips the whole
collection's raw files. No manifest — it's "get the files out", not a backup.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator

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


def _upload(client: TestClient, cid: str, path: str, body: bytes) -> None:
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": (path, body, "text/markdown")},
    )


def test_folder_download_zips_raw_files_relative_to_prefix():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Specs"}).json()["resource_id"]
    _upload(client, cid, "img/logo.md", b"# Logo")
    _upload(client, cid, "img/sub/a.txt", b"alpha")
    _upload(client, cid, "readme.md", b"top-level, excluded")

    prep = client.post(f"/kb/collections/{cid}/folder-download/prepare?prefix=img")
    assert prep.status_code == 200
    body = prep.json()
    assert body["download_id"]
    assert body["filename"] == "img.zip"
    assert body["size"] > 0

    dl = client.get(f"/kb/collections/{cid}/folder-download/{body['download_id']}?prefix=img")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/zip")
    assert "img.zip" in dl.headers["content-disposition"]

    zf = _zip(dl.content)
    names = set(zf.namelist())
    # entries are relative to the selected folder; the sibling top-level doc and
    # any manifest are NOT present.
    assert names == {"logo.md", "sub/a.txt"}
    assert zf.read("logo.md") == b"# Logo"
    assert zf.read("sub/a.txt") == b"alpha"


def test_root_prefix_zips_whole_collection_without_manifest():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Whole Set"}).json()["resource_id"]
    _upload(client, cid, "a.md", b"AA")
    _upload(client, cid, "img/b.md", b"BB")

    prep = client.post(f"/kb/collections/{cid}/folder-download/prepare").json()
    assert prep["filename"] == "Whole Set.zip"  # root → collection name
    zf = _zip(client.get(f"/kb/collections/{cid}/folder-download/{prep['download_id']}").content)

    names = set(zf.namelist())
    assert names == {"a.md", "img/b.md"}  # full tree, paths from the root
    # raw export, NOT the round-trippable collection backup
    assert ".kb-collection/manifest.json" not in names


def test_gitkeep_placeholders_are_skipped():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "K"}).json()["resource_id"]
    _upload(client, cid, "docs/.gitkeep", b"\n")
    _upload(client, cid, "docs/real.md", b"hi")

    prep = client.post(f"/kb/collections/{cid}/folder-download/prepare?prefix=docs").json()
    zf = _zip(
        client.get(
            f"/kb/collections/{cid}/folder-download/{prep['download_id']}?prefix=docs"
        ).content
    )
    assert zf.namelist() == ["real.md"]  # the placeholder is not user content


def test_empty_subtree_yields_an_empty_zip():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "E"}).json()["resource_id"]
    _upload(client, cid, "a.md", b"a")
    prep = client.post(f"/kb/collections/{cid}/folder-download/prepare?prefix=nope").json()
    zf = _zip(
        client.get(
            f"/kb/collections/{cid}/folder-download/{prep['download_id']}?prefix=nope"
        ).content
    )
    assert zf.namelist() == []


def test_streaming_consumes_the_prepared_folder_download():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Once"}).json()["resource_id"]
    _upload(client, cid, "a.md", b"a")
    did = client.post(f"/kb/collections/{cid}/folder-download/prepare").json()["download_id"]
    assert client.get(f"/kb/collections/{cid}/folder-download/{did}").status_code == 200
    # The temp file is deleted after the first send — a re-fetch 404s.
    assert client.get(f"/kb/collections/{cid}/folder-download/{did}").status_code == 404


def test_prepare_unknown_collection_is_404():
    client = _client()
    assert client.post("/kb/collections/nope/folder-download/prepare").status_code == 404


def test_stream_rejects_malformed_or_unknown_download_id():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "X"}).json()["resource_id"]
    assert client.get(f"/kb/collections/{cid}/folder-download/not-a-token").status_code == 404
    assert client.get(f"/kb/collections/{cid}/folder-download/{'0' * 32}").status_code == 404


def test_stream_404_when_collection_deleted_after_prepare():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "Gone"}).json()["resource_id"]
    _upload(client, cid, "a.md", b"a")
    did = client.post(f"/kb/collections/{cid}/folder-download/prepare").json()["download_id"]
    client.delete(f"/collection/{cid}/permanently")
    assert client.get(f"/kb/collections/{cid}/folder-download/{did}").status_code == 404
