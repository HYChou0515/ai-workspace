"""#230: the /help endpoint — exposes the platform Help collection id (so the FE
can scope its KB chat) + the collection's documents (so the FE can link each to
the existing KB document viewer)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.help_collection import seed_help_collection
from workspace_app.kb.ingest import Ingestor
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _app() -> tuple[TestClient, SpecStar]:
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


def _seed(spec: SpecStar) -> str:
    ing = Ingestor(
        spec,
        chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        embedder=HashEmbedder(dim=EMBED_DIM),
    )
    return seed_help_collection(spec, ing)


def test_help_returns_collection_and_its_documents():
    client, spec = _app()
    cid = _seed(spec)

    r = client.get("/help")

    assert r.status_code == 200
    body = r.json()
    assert body["collection_id"] == cid
    paths = {d["path"] for d in body["documents"]}
    assert "CHANGELOG.md" in paths
    assert "getting-started.md" in paths


def test_help_tags_release_notes_vs_guides():
    client, spec = _app()
    _seed(spec)

    docs = {d["path"]: d for d in client.get("/help").json()["documents"]}

    assert docs["CHANGELOG.md"]["kind"] == "release_notes"
    assert docs["getting-started.md"]["kind"] == "guide"
    # each doc carries the opaque id the KB document viewer takes
    assert all(d["id"] for d in docs.values())


def test_help_releases_returns_structured_versions():
    # #441: the /help/releases view reads the packaged CHANGELOG.md (git-cliff
    # output) and returns it as structured releases. Needs no seed — it reads the
    # packaged file, not the KB collection.
    client, _ = _app()

    r = client.get("/help/releases")

    assert r.status_code == 200
    releases = r.json()["releases"]
    assert isinstance(releases, list)
    # Every release carries the version + grouped sections shape the FE renders.
    for rel in releases:
        assert rel["version"]
        assert isinstance(rel["unreleased"], bool)
        for sec in rel["sections"]:
            assert sec["group"]
            assert isinstance(sec["items"], list)


def test_help_works_before_anything_is_seeded():
    # No boot seed in this harness; the route still returns a usable collection
    # id (created on demand) and an empty document list — never a 404.
    client, _ = _app()

    body = client.get("/help").json()

    assert body["collection_id"]
    assert body["documents"] == []
