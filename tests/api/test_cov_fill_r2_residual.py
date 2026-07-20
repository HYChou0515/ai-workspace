"""Residual cov-fill for the post-#54 split modules.

Four reachable branches the 100% gate flags as missing once their only prior
coverage came from flaky LLM-path / timing integration tests:

1. ``capability_routes.py`` — the VALID-token success path ``actor = claims.user``
   in BOTH ``capability_ingest`` and ``capability_context_card`` (the 401
   invalid-token path is already covered). We capture the app's ``CredentialBroker``
   (the SAME instance the routes resolve against) by monkeypatching the symbol
   ``create_app`` imports, mint a run-scoped token bound to the item, and POST the
   two capabilities with a valid ``X-Workflow-Token`` → 200.
2. ``lifecycle.py`` — the index-sweeper loop body
   (``asyncio.to_thread(index_coordinator.sweep_stuck_runs, ...)``). It sleeps
   ``INDEX_SWEEP_INTERVAL_S`` (300 s), so it never ticks in a test; we monkeypatch
   the interval tiny and spy the coordinator instance's ``sweep_stuck_runs``, then
   run the app under ``LifespanManager`` (mirrors ``test_blob_gc_sweeper``).
3. ``replay_loaders.py`` — (a) ``load_turn`` returns ``None`` when
   ``resolve_agent_config`` is ``None`` for an rca thread that HAS a Conversation
   (the ``if config is None: return None`` branch); (b) ``load_doc`` returns
   ``None`` on ``ResourceIDNotFoundError`` for an unknown ``document_id``.
4. ``workflow_routes.py`` — ``stream_workflow_run``'s False side of
   ``if isinstance(run, WorkflowRun) and run.chat_id``: a run that resolves but
   carries an empty ``chat_id`` keeps ``key = investigation_id``.

All wiring is deterministic: ScriptedAgentRunner / MockSandbox /
SpecstarFileStore|MemoryFileStore / HashEmbedder — no real LLM / docker.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from specstar import SpecStar

import workspace_app.api.app as app_mod
import workspace_app.api.locator as locator_mod
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.health.replay import ReplayService
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import Conversation, Message, make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.run import WorkflowRun

from ._client import TestClient


def _playground_item(spec: SpecStar, profile: str = "echo") -> str:
    return (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile=profile))
        .resource_id
    )


def _route(app: FastAPI, path: str):
    return next(
        r.endpoint  # ty: ignore[unresolved-attribute]
        for r in app.routes
        if getattr(r, "path", None) in (path, "/api" + path)
    )


# ── 1) capability_routes.py: VALID token → actor = claims.user → 200 ──────────


def test_capabilities_with_a_valid_token_act_as_the_claims_user(monkeypatch):
    """A valid ``X-Workflow-Token`` (scoped to the item) takes the
    ``actor = claims.user`` branch in BOTH capability endpoints and the call
    succeeds (200). We capture the broker ``create_app`` wires and mint a token
    against it, so the route resolves the same in-memory claims."""
    spec = make_spec(default_user="u")
    captured: dict[str, Any] = {}
    real_broker = app_mod.CredentialBroker

    def _capture_broker(*a, **kw):
        broker = real_broker(*a, **kw)
        captured["broker"] = broker
        return broker

    monkeypatch.setattr(app_mod, "CredentialBroker", _capture_broker)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
    )
    item_id = _playground_item(spec)
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    broker = captured["broker"]
    # require_item returns the item_id, so the token must be scoped to it.
    token = broker.mint(run_id="r1", user="alice", item_id=item_id, ttl_ms=600_000)
    base = f"/a/playground/items/{item_id}"

    with TestClient(app) as client:
        assert (
            client.put(f"{base}/files/digest/a.md", content=b"# A\nhello content").status_code
            == 204
        )
        r = client.post(
            f"{base}/capabilities/ingest",
            json={"collection": cid, "path": "digest/a.md"},
            headers={"X-Workflow-Token": token},
        )
        assert r.status_code == 200 and r.json()["doc_id"]

        r = client.post(
            f"{base}/capabilities/context-card",
            json={"collection": cid, "keys": ["M4", "Metal 4"], "title": "Metal 4", "body": "L4"},
            headers={"X-Workflow-Token": token},
        )
        assert r.status_code == 200 and r.json()["card_id"]


# ── 2) lifecycle.py: the index-sweeper loop body ticks ────────────────────────


async def test_index_sweeper_ticks_sweep_stuck_runs(monkeypatch):
    """With ``INDEX_SWEEP_INTERVAL_S`` shrunk to a tick, the lifespan's
    ``index_sweeper`` runs its loop body — ``asyncio.to_thread(...sweep_stuck_runs)``
    — which we spy on the coordinator instance (mirrors ``test_blob_gc_sweeper``)."""
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    swept = threading.Event()
    monkeypatch.setattr(
        app.state.index_coordinator,
        "sweep_stuck_runs",
        lambda **kw: swept.set(),
    )
    monkeypatch.setattr("workspace_app.api.lifecycle.INDEX_SWEEP_INTERVAL_S", 0.01)
    async with LifespanManager(app):
        for _ in range(100):
            if swept.is_set():
                break
            await asyncio.sleep(0.05)
    assert swept.is_set()


async def test_index_sweeper_also_ticks_sweep_stuck_docs(monkeypatch):
    """#573: the run-keyed sweep only recovers fan-outs, so a doc abandoned with no
    ``IndexRun`` (single-job path, or a worker killed before the run was seeded)
    needed the doc-keyed sweep — which is worthless unless the same tick calls it."""
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    swept = threading.Event()
    monkeypatch.setattr(
        app.state.index_coordinator,
        "sweep_stuck_docs",
        lambda **kw: swept.set(),
    )
    monkeypatch.setattr("workspace_app.api.lifecycle.INDEX_SWEEP_INTERVAL_S", 0.01)
    async with LifespanManager(app):
        for _ in range(100):
            if swept.is_set():
                break
            await asyncio.sleep(0.05)
    assert swept.is_set()


# ── 3) replay_loaders.py: load_turn (config None) + load_doc (unknown id) ──────


def _replay_client(spec: SpecStar) -> TestClient:
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        replay_service=ReplayService(completion=lambda **kw: iter([])),
    )
    return TestClient(app)


def test_replay_turn_none_when_rca_thread_has_conv_but_no_config(monkeypatch):
    """An rca thread that HAS a Conversation but whose ``resolve_agent_config`` is
    ``None`` (gone / unregistered App) hits ``load_turn``'s ``if config is None:
    return None`` branch → the route answers 404 (no turn to replay)."""
    spec = make_spec()
    spec.get_resource_manager(Conversation).create(
        Conversation(item_id="thread-x", messages=[Message(role="user", content="hi")])
    )
    monkeypatch.setattr(locator_mod.ItemLocator, "resolve_agent_config", lambda self, item_id: None)
    client = _replay_client(spec)
    r = client.post(
        "/health/replay/turn",
        json={"source": "rca", "thread_id": "thread-x", "message_index": 0},
    )
    assert r.status_code == 404


def test_replay_doc_none_for_unknown_document_id():
    """An unknown ``document_id`` makes ``doc_rm.get`` raise
    ``ResourceIDNotFoundError`` → ``load_doc`` returns ``None`` → 404."""
    client = _replay_client(make_spec())
    r = client.post("/health/replay/doc", json={"document_id": "no-such-doc"})
    assert r.status_code == 404


def test_replay_turn_none_for_unknown_kb_thread():
    """A ``source="kb"`` thread id that doesn't exist makes ``kb_rm.get`` raise
    ``ResourceIDNotFoundError`` → ``load_turn`` returns ``None`` → 404."""
    client = _replay_client(make_spec())
    r = client.post(
        "/health/replay/turn",
        json={"source": "kb", "thread_id": "no-such-kb-thread", "message_index": 0},
    )
    assert r.status_code == 404


# ── 4) workflow_routes.py: stream falls back to item key when chat_id empty ────


async def test_stream_keys_on_item_when_resolved_run_has_no_chat_id():
    """A ``WorkflowRun`` that resolves but carries an empty ``chat_id`` takes the
    False side of ``isinstance(run, WorkflowRun) and run.chat_id`` — the SSE keeps
    ``key = investigation_id`` (the item broadcast). Invoke the handler directly so
    the never-ending stream body isn't consumed."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()]),
    )
    item_id = _playground_item(spec)
    # chat_id defaults to "" — a resolvable run with no workflow chat.
    run_id = (
        spec.get_resource_manager(WorkflowRun)
        .create(WorkflowRun(item_id=item_id, captured_user="u"))
        .resource_id
    )
    resp = await _route(app, "/a/{slug}/items/{item_id}/runs/{run_id}/stream")(
        slug="playground", item_id=item_id, run_id=run_id
    )
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
