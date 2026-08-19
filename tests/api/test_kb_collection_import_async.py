"""#715: the asynchronous import routes.

The synchronous routes stay — they are the right shape for restoring a backup you
exported yourself. These are the contract for the other caller: a machine pushing
a prepared archive, which needs an answer now and the outcome later.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator

from specstar import SpecStar

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


def _archive(members: dict[str, bytes], cards: list[dict] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in members.items():
            zf.writestr(path, data)
        zf.writestr(
            ".kb-collection/manifest.json",
            json.dumps(
                {"version": 1, "collection": {"name": "Pushed"}, "context_cards": cards or []},
                ensure_ascii=False,
            ),
        )
    return buf.getvalue()


def _post(client: TestClient, url: str, zip_bytes: bytes):
    return client.post(url, files={"file": ("archive.zip", zip_bytes, "application/zip")})


def test_starting_an_import_answers_with_both_ids_before_the_work_is_done():
    """The caller needs the collection id straight away — the collection should
    appear in the list, empty and filling, not spring into existence ten minutes
    later — and a run id to ask about the outcome."""
    client, _ = _client_with_spec()

    r = _post(client, "/kb/collections/imports", _archive({"a.md": b"alpha"}))

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["collection_id"].startswith("collection:")
    assert body["import_id"]
    assert body["status"] == "queued"
    # The collection is already listed, so the caller can see where its data will land.
    names = [c["name"] for c in client.get("/kb/collections").json()]
    assert "Pushed" in names


def test_the_run_can_be_polled_for_its_outcome():
    client, _ = _client_with_spec()
    started = _post(client, "/kb/collections/imports", _archive({"a.md": b"alpha"})).json()

    r = client.get(f"/kb/collections/imports/{started['import_id']}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["collection_id"] == started["collection_id"]
    assert body["members"] == 1  # documents the archive holds
    assert "written" in body and "errors" in body and "finished" in body


def test_polling_an_unknown_run_is_a_404():
    client, _ = _client_with_spec()
    assert client.get("/kb/collections/imports/import-run:nope").status_code == 404


def test_merging_into_an_existing_collection_keeps_that_collection():
    client, _ = _client_with_spec()
    cid = client.post("/kb/collections", json={"name": "Existing"}).json()["resource_id"]

    r = _post(client, f"/kb/collections/{cid}/imports?mode=skip", _archive({"a.md": b"alpha"}))

    assert r.status_code == 202, r.text
    assert r.json()["collection_id"] == cid


def test_merging_rejects_a_mode_the_importer_does_not_have():
    """Same guard as the synchronous route: an unknown mode must not reach the
    worker, where it would silently mean overwrite."""
    client, _ = _client_with_spec()
    cid = client.post("/kb/collections", json={"name": "Existing"}).json()["resource_id"]

    r = _post(client, f"/kb/collections/{cid}/imports?mode=merge", _archive({}))

    assert r.status_code == 400, r.text


def test_merging_into_an_unknown_collection_is_a_404():
    client, _ = _client_with_spec()
    r = _post(client, "/kb/collections/collection:nope/imports", _archive({}))
    assert r.status_code == 404
