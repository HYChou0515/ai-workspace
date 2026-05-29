"""POST /investigations/{id}/promote-to-kb + close-hook auto-promotion.

P2 chat → knowledge endpoint and its background companion (close →
extract insights). Uses a scripted fake LLM (no live model) wired into
`create_app(kb_chat_pipeline=...)`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from specstar import QB, SpecStar

from workspace_app.api import (
    AgentEvent,
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    create_app,
)
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.li_pipeline import build_chat_pipeline
from workspace_app.kb.llm import ILlm
from workspace_app.resources import Conversation, Investigation, Message
from workspace_app.resources.kb import EMBED_DIM, Collection, SourceDoc
from workspace_app.sandbox.mock import MockSandbox


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


def _build_harness(llm_response: str) -> tuple[TestClient, SpecStar, str]:
    """create_app with a chat pipeline using the given fake LLM response.
    Returns (client, spec, insights_collection_id)."""
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    embedder = HashEmbedder(dim=EMBED_DIM)
    events: list[AgentEvent] = [MessageDelta(text="ok"), RunDone()]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner(events),
        kb_embedder=embedder,
        kb_chat_pipeline=build_chat_pipeline(llm=_FakeLlm(llm_response), embedder=embedder),
    )
    client = TestClient(app)
    # The insights collection is auto-ensured at boot; fish out its id.
    rm = spec.get_resource_manager(Collection)
    [insights] = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources(QB.all())  # type: ignore[arg-type]
        if r.data.name == "Investigations Knowledge"
    ]
    return client, spec, insights


def _create_investigation(spec: SpecStar) -> str:
    inv_rm = spec.get_resource_manager(Investigation)
    res = inv_rm.create(
        Investigation(
            title="MX-7 voids",
            owner="alice",
            description="lots flagged",
        )
    )
    rid = res.resource_id
    # Conversation too — close hook reads from it.
    conv_rm = spec.get_resource_manager(Conversation)
    conv_rm.create(
        Conversation(
            investigation_id=rid,
            messages=[
                Message(role="user", content="AOI flagged voids on lot 25-W14"),
                Message(role="assistant", content="Checked zone temps; thermocouple drift."),
            ],
        )
    )
    return rid


def test_promote_endpoint_writes_insights_to_kb():
    """POST /promote-to-kb runs the chat pipeline + writes one SourceDoc
    per insight in the "Investigations Knowledge" collection."""
    client, spec, insights_cid = _build_harness(
        '{"insights": ['
        '  {"kind": "root_cause", "title": "Zone-3 drift",'
        '   "markdown": "# Root cause: Zone-3 drift\\n\\n'
        'Thermocouple miscalibration on lot 25-W14."}'
        "]}"
    )
    inv_id = _create_investigation(spec)
    resp = client.post(f"/investigations/{inv_id}/promote-to-kb")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["insight_ids"]) == 1
    # SourceDoc is in the insights collection.
    [doc_id] = body["insight_ids"]
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert doc.collection_id == insights_cid
    assert doc.path == f"{inv_id}/insight-0.md"


def test_promote_endpoint_returns_empty_for_inconclusive_chat():
    """If the LLM finds nothing to extract → empty list, no KB writes."""
    client, spec, _ = _build_harness('{"insights": []}')
    inv_id = _create_investigation(spec)
    resp = client.post(f"/investigations/{inv_id}/promote-to-kb")
    assert resp.status_code == 200
    assert resp.json() == {"insight_ids": []}


def test_promote_endpoint_404s_on_unknown_investigation():
    client, _, _ = _build_harness('{"insights": []}')
    resp = client.post("/investigations/does-not-exist/promote-to-kb")
    assert resp.status_code == 404


def test_promote_endpoint_returns_empty_when_no_chat_pipeline():
    """No KB LLM → no chat pipeline → endpoint cleanly returns []."""
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    events: list[AgentEvent] = [MessageDelta(text="ok"), RunDone()]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner(events),
        # kb_chat_pipeline NOT passed → None
    )
    client = TestClient(app)
    inv_id = _create_investigation(spec)
    resp = client.post(f"/investigations/{inv_id}/promote-to-kb")
    assert resp.status_code == 200
    assert resp.json() == {"insight_ids": []}


def test_promote_endpoint_swallows_ingest_chat_failure(monkeypatch):
    """The promote helper is best-effort: if `ingest_chat` raises (e.g. LLM
    timeout, JSON parse blew up internally), the endpoint still returns 200
    with empty insight_ids — never propagates to the FE as a 500."""
    client, spec, _ = _build_harness('{"insights": []}')
    inv_id = _create_investigation(spec)

    from workspace_app.kb.ingest import Ingestor

    def _explode(*_a, **_kw):
        raise RuntimeError("model went away")

    monkeypatch.setattr(Ingestor, "ingest_chat", _explode)
    resp = client.post(f"/investigations/{inv_id}/promote-to-kb")
    assert resp.status_code == 200
    assert resp.json() == {"insight_ids": []}


def test_ensure_insights_collection_reuses_existing(spec_factory):
    """Calling the helper twice on the same spec returns the same id and
    doesn't create a duplicate Collection — exercises the 'already exists'
    branch."""
    from workspace_app.api.app import _ensure_insights_collection

    spec = spec_factory()
    first = _ensure_insights_collection(spec, "MyCollection")
    second = _ensure_insights_collection(spec, "MyCollection")
    assert first == second
    cols = [
        c
        for c in spec.get_resource_manager(Collection).list_resources(QB.all())  # type: ignore[arg-type]
        if c.data.name == "MyCollection"  # ty: ignore[unresolved-attribute]
    ]
    assert len(cols) == 1


def test_get_chat_pipeline_factory_constructs_when_llm_present():
    """`get_chat_pipeline(settings, embedder, llm)` returns a pipeline when
    an LLM is wired (covers the non-None branch of the factory)."""
    from workspace_app.factories import Settings, get_chat_pipeline

    llm = _FakeLlm('{"insights": []}')
    pipeline = get_chat_pipeline(Settings(), HashEmbedder(dim=EMBED_DIM), llm)
    assert pipeline is not None


def test_get_chat_pipeline_factory_returns_none_without_llm():
    """No KB LLM wired → factory returns None and the chat path degrades
    gracefully (the close hook + endpoint check for None)."""
    from workspace_app.factories import Settings, get_chat_pipeline

    assert get_chat_pipeline(Settings(), HashEmbedder(dim=EMBED_DIM), None) is None


def test_ensure_insights_collection_skips_unrelated_collections(spec_factory):
    """The lookup walks every Collection looking for one with the target
    name. Exercise the iterate-past-non-matching branch by seeding a few
    unrelated collections first."""
    from workspace_app.api.app import _ensure_insights_collection

    spec = spec_factory()
    rm = spec.get_resource_manager(Collection)
    rm.create(Collection(name="Reflow SOPs"))
    rm.create(Collection(name="MX-7 Manuals"))
    cid = _ensure_insights_collection(spec, "Investigations Knowledge")
    # Got a fresh id (the target name didn't pre-exist) but iterated past
    # the others. Re-call returns the same id.
    assert _ensure_insights_collection(spec, "Investigations Knowledge") == cid


def test_ingest_chat_skips_unchanged_insight_bytes(spec_factory):
    """When `_store_file` returns None (same bytes as the existing insight),
    `ingest_chat` skips the re-chunk step — exercises the "unchanged" branch."""
    from workspace_app.kb.ingest import Ingestor as _Ingestor
    from workspace_app.kb.li_pipeline import build_chat_pipeline as _build

    spec = spec_factory()
    embedder = HashEmbedder(dim=EMBED_DIM)
    rm = spec.get_resource_manager(Collection)
    cid = rm.create(Collection(name="insights")).resource_id
    # Same insight twice — second run hits the "unchanged bytes" continue.
    canned = (
        '{"insights": [{"kind": "lesson_learned", "title": "t",'
        ' "markdown": "# t\\n\\nbody"}]}'
    )
    p1 = _build(llm=_FakeLlm(canned), embedder=embedder)
    ing = _Ingestor(spec, chat_pipeline=p1, embedder=embedder)
    first = ing.ingest_chat(
        collection_id=cid,
        user="u",
        investigation_id="inv-q",
        investigation_title="t",
        messages=[{"role": "user", "content": "x"}],
    )
    assert first  # first run wrote something
    second = ing.ingest_chat(
        collection_id=cid,
        user="u",
        investigation_id="inv-q",
        investigation_title="t",
        messages=[{"role": "user", "content": "x"}],
    )
    # Second run hit the unchanged-bytes guard for every insight.
    assert second == []


@pytest.fixture
def spec_factory():
    """SpecStar with the workspace's resources registered (kb collection +
    sourcedoc + docchunk). Used by tests that build state outside the API."""
    from workspace_app.resources import register_all

    def _make() -> SpecStar:
        s = SpecStar()
        s.configure(default_user="u", default_now=lambda: datetime.now(UTC))
        register_all(s)
        return s

    return _make


@pytest.mark.asyncio
async def test_close_investigation_schedules_background_promote():
    """Closing an investigation as resolved/abandoned fires the chat→KB
    extraction in the background (doesn't block the 204). After a short
    await for the task to run, the insight SourceDoc exists."""
    client, spec, insights_cid = _build_harness(
        '{"insights": [{"kind": "lesson_learned", "title": "t",'
        ' "markdown": "# Lesson\\n\\nbody."}]}'
    )
    inv_id = _create_investigation(spec)
    resp = client.post(f"/investigations/{inv_id}/close", json={"status": "resolved"})
    assert resp.status_code == 204
    # The promote runs as an asyncio.create_task; give the loop a slice.
    for _ in range(20):
        ids = [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in spec.get_resource_manager(SourceDoc).list_resources(
                (QB["collection_id"] == insights_cid).build()  # type: ignore[arg-type]
            )
        ]
        if ids:
            break
        await asyncio.sleep(0.05)
    assert ids, "expected the background promote to have written an insight"
