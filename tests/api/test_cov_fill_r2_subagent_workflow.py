"""Deterministic cov-fill (#54 app.py decomposition).

Two branches that were previously only hit by flaky LLM-path integration tests:

1. ``SubagentBridge.run`` raising ``ValueError`` when neither the catalog's
   ``default_for(purpose)`` nor the bundled ``purpose_fallbacks`` resolve an
   ``AgentConfig`` — the guard returns BEFORE any runner/retriever is touched.
2. ``stream_workflow_run``'s happy path: the run id resolves to a ``WorkflowRun``
   carrying a ``chat_id``, so the SSE keys on the run's chat (not the item
   broadcast fallback). Driven through the real workflow wiring (scripted runner /
   MockSandbox) against the bundled ``playground/echo`` workflow profile.
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from specstar import SpecStar

from tests.api._client import TestClient
from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.api.subagent_bridge import SubagentBridge
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

# ── Target 1: SubagentBridge.run with no AgentConfig for the purpose ──────────


async def test_subagent_bridge_raises_when_no_config_for_purpose():
    """An empty catalog + empty fallbacks → no AgentConfig resolves → ValueError,
    raised before any runner/retriever/spec access (so stand-ins suffice)."""
    bridge = SubagentBridge(
        spec=None,  # ty: ignore[invalid-argument-type]
        runner=None,  # ty: ignore[invalid-argument-type]
        kb_runner=None,  # ty: ignore[invalid-argument-type]
        retriever=None,  # ty: ignore[invalid-argument-type]
        catalog=AgentConfigCatalog(),  # default_for(...) → None, purposes() → []
        purpose_fallbacks={},  # no bundled fallback either
        get_user_id=lambda: "u",
        max_searches=None,
    )
    with pytest.raises(ValueError, match="no AgentConfig registered for sub-agent purpose"):
        await bridge.run("no-such-purpose", "payload")


# ── Target 2: stream_workflow_run keys on the resolved run's chat_id ──────────


def _app(profile: str = "echo") -> tuple[FastAPI, SpecStar, str]:
    spec = make_spec()
    runner = ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()])
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile=profile))
        .resource_id
    )
    return app, spec, item_id


def _route(app: FastAPI, path: str):
    return next(
        r.endpoint  # ty: ignore[unresolved-attribute]
        for r in app.routes
        if getattr(r, "path", None) in (path, "/api" + path)
    )


def _base(item_id: str) -> str:
    return f"/a/playground/items/{item_id}"


async def test_stream_keys_on_resolved_runs_chat_id():
    """A real run produces a ``WorkflowRun`` with a ``chat_id``; invoking the stream
    handler with that run id takes the ``run.chat_id`` branch (not the item fallback)."""
    app, spec, item_id = _app()
    with TestClient(app) as client:
        r = client.put(f"{_base(item_id)}/files/uploads/input.json", content='{"n": 1}')
        assert r.status_code == 204
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        # Poll to done so the WorkflowRun is fully persisted with its chat_id.
        chat_id = ""
        for _ in range(200):
            data = client.get(f"{_base(item_id)}/runs/{run_id}").json()
            chat_id = data.get("chat_id") or ""
            if data["status"] == "done":
                break
            time.sleep(0.02)
        assert chat_id  # the run carries its workflow chat

    resp = await _route(app, "/a/{slug}/items/{item_id}/runs/{run_id}/stream")(
        slug="playground", item_id=item_id, run_id=run_id
    )
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
