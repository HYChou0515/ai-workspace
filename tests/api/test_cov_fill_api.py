"""Characterization tests filling coverage gaps in the API layer.

Targets (uncovered before this file):
  - app.py: _bubble_kb_citations implicit-synthesis with no sub-agent (163) and
    dedup `continue` (169).
  - kb_chat_routes.py: register raises on empty kb_agent_configs (244).
  - kb_routes.py: wiki_status / rebuild_wiki when no coordinator (468), rebuild
    of an unknown collection 404 (489-490), and a real documents page so the
    per-doc chunk-count loop runs (553->547 / 554).
  - litellm_runner.py: _agent_for wraps the model in DecideThenActModel when the
    WORKSPACE_AGENT_DECIDE_THEN_ACT toggle is on (317-319).
  - turns.py: subscribe_sse drains a finite event iterator to exhaustion
    (the loop-exit branch, 458->exit).

All wiring uses ScriptedAgentRunner / MockSandbox / HashEmbedder — no real LLM.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone, to_sse
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.conversation import Citation
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient, TestClient

# ── app.py: _bubble_kb_citations ─────────────────────────────────────


def _cit(marker: int, doc: str = "d1", start: int = 0) -> Citation:
    return Citation(
        marker=marker,
        collection_id="c1",
        document_id=doc,
        filename="f.md",
        start=start,
        end=start + 10,
        source_chunk_ids=[],
    )


def test_bubble_kb_citations_implicit_synthesis_with_no_subagent_returns_empty():
    """line 163: content has no [N] markers (implicit synthesis) but no
    sub-agent ran → nothing to bubble."""
    from workspace_app.api.rca_messages import bubble_kb_citations as _bubble_kb_citations

    assert _bubble_kb_citations("a plain answer, no markers", []) == []


def test_bubble_kb_citations_implicit_synthesis_dedupes_by_chunk():
    """line 169: implicit synthesis returns the latest call's citations deduped
    by (document_id, start) — a repeated key hits the `continue`."""
    from workspace_app.api.rca_messages import bubble_kb_citations as _bubble_kb_citations

    latest = [
        _cit(1, doc="d1", start=0),
        _cit(2, doc="d1", start=0),  # SAME (document_id, start) → deduped (continue)
        _cit(3, doc="d2", start=5),
    ]
    out = _bubble_kb_citations("synthesized prose without markers", [latest])
    keys = {(c.document_id, c.start) for c in out}
    assert keys == {("d1", 0), ("d2", 5)}  # the duplicate was dropped
    assert len(out) == 2


# ── kb_chat_routes.py: empty configs guard ───────────────────────────


def test_register_kb_chat_routes_rejects_empty_configs():
    """line 244: an empty kb_agent_configs is a misconfiguration (the FE picker
    would be empty) — fail loud at registration."""
    from workspace_app.api.kb_chat_routes import register_kb_chat_routes
    from workspace_app.users import MockUserDirectory

    spec = make_spec(default_user="u")
    engine = ChatTurnEngine(_NoopRunner())
    with pytest.raises(ValueError, match="kb_agent_configs must be non-empty"):
        register_kb_chat_routes(
            FastAPI(),
            spec,
            engine,
            _NoopRetriever(),  # ty: ignore[invalid-argument-type]
            lambda: "u",
            MockUserDirectory(),
            kb_agent_configs=[],
        )


class _NoopRunner:
    """A do-nothing AgentRunner: these tests never drive a turn (they hit
    registration guards / GET routes), so `run` is never invoked."""

    async def run(self, prompt: str, ctx: object) -> AsyncIterator[AgentEvent]:
        if False:  # pragma: no cover — never invoked
            yield RunDone()


class _NoopRetriever:
    pass


# ── kb_routes.py: wiki coordinator absent ────────────────────────────


def _bare_kb_app(spec) -> FastAPI:
    """Register the KB routes directly with NO wiki coordinator, so wiki_status
    and rebuild take the `wiki_coordinator is None` paths."""
    from workspace_app.api.kb_routes import register_kb_routes
    from workspace_app.kb.index_coordinator import IndexCoordinator
    from workspace_app.kb.ingest import Ingestor

    ingestor = Ingestor(
        spec,
        chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
        embedder=HashEmbedder(dim=EMBED_DIM),
    )
    index_coordinator = IndexCoordinator(spec, ingestor)
    app = FastAPI()
    register_kb_routes(
        app,
        spec,
        ingestor,
        None,  # ← no wiki coordinator
        index_coordinator=index_coordinator,
        get_user_id=lambda: "u",
    )
    return app


def test_wiki_status_without_coordinator_reports_not_building():
    """line 468: with no wiki coordinator wired, status is a static
    'not building / 0 of 0' instead of querying a coordinator."""
    spec = make_spec(default_user="u")
    client = TestClient(_bare_kb_app(spec))
    cid = client.post("/kb/collections", json={"name": "c", "use_wiki": True}).json()["resource_id"]
    st = client.get(f"/kb/collections/{cid}/wiki/status").json()
    assert st["building"] is False
    assert st["total"] == 0
    assert st["done"] == 0


def test_rebuild_wiki_without_coordinator_is_disabled():
    """The `wiki_coordinator is None` half of the rebuild guard (491) — a
    no-coordinator deploy reports the wiki path is disabled."""
    spec = make_spec(default_user="u")
    client = TestClient(_bare_kb_app(spec))
    cid = client.post("/kb/collections", json={"name": "c", "use_wiki": True}).json()["resource_id"]
    r = client.post(f"/kb/collections/{cid}/wiki/rebuild")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_rebuild_wiki_unknown_collection_404():
    """lines 489-490: rebuilding a collection that doesn't exist 404s via the
    ResourceIDNotFoundError handler."""
    spec = make_spec(default_user="u")
    client = TestClient(_bare_kb_app(spec))
    r = client.post("/kb/collections/does-not-exist/wiki/rebuild")
    assert r.status_code == 404


# ── kb_routes.py: documents page exercises the chunk-count loop ───────


def _full_app(spec):
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_NoopRunner(),
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
    )


async def test_documents_page_counts_chunks_per_doc():
    """553->547 / 554: a real indexed doc produces DocChunks whose indexed
    source_doc_id (always a str) is bucketed into the page's per-doc chunk
    count."""
    spec = make_spec(default_user="u")
    app = _full_app(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "c"})).json()["resource_id"]
        # First an empty page — `if ids:` is False, so the chunk-count loop is
        # skipped (the `546->559` / empty-collection branch).
        empty = (await c.get(f"/kb/collections/{cid}/documents")).json()
        assert empty["total"] == 0 and empty["items"] == []
        await c.post(
            f"/kb/collections/{cid}/documents",
            files={
                "file": (
                    "note.md",
                    b"Reflow zone three runs at 245C for the full bake cycle window.",
                    "text/markdown",
                )
            },
        )
        # Drain indexing so DocChunks exist for the count loop.
        await app.state.index_coordinator.aclose()

        page = (await c.get(f"/kb/collections/{cid}/documents")).json()
        assert page["total"] == 1
        (row,) = page["items"]
        # The chunk-count loop ran and bucketed at least one chunk for this doc.
        assert row["chunks"] >= 1


# ── litellm_runner.py: decide-then-act toggle ────────────────────────


def test_agent_for_uses_decide_then_act_model_when_toggled(monkeypatch):
    """lines 317-319: with WORKSPACE_AGENT_DECIDE_THEN_ACT on, _agent_for wraps
    the model in DecideThenActModel instead of the default RepairingModel."""
    monkeypatch.setenv("WORKSPACE_AGENT_DECIDE_THEN_ACT", "1")
    from workspace_app.agent.decide_then_act import DecideThenActModel
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources import AgentConfig

    agent = _agent_for(AgentConfig(name="ws"))
    assert isinstance(agent.model, DecideThenActModel)


# ── turns.py: subscribe_sse drains a finite stream ───────────────────


async def test_subscribe_sse_exits_when_event_stream_exhausts(monkeypatch):
    """branch 458->exit: subscribe_sse's frame loop ends cleanly when the
    underlying event iterator is exhausted (vs. the infinite live queue)."""
    engine = ChatTurnEngine(_NoopRunner())

    async def finite_events(_key: str) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="hi")
        yield RunDone()
        # generator returns here → the for-loop in _frames hits its exit branch

    monkeypatch.setattr(engine, "subscribe", finite_events)
    frames = [f async for f in engine.subscribe_sse("inv")]
    assert frames == [to_sse(MessageDelta(text="hi")), to_sse(RunDone())]
    assert all(f.startswith("data: ") for f in frames)
