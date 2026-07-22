"""Deterministic cov-fill for api/chat_routes.py (#54 decomposition).

Two reachable-but-previously-flaky-covered branches:

1. The SSE stream route BODIES — ``stream_investigation`` (GET
   ``/a/rca/items/{id}/stream``) and ``stream_chat`` (GET
   ``.../chats/{chat_id}/stream``). Merely dispatching the request runs the
   handler body (``require_item`` + ``return StreamingResponse(...)``); we open
   the stream (headers received ⇒ the endpoint already returned) but never
   consume the infinite live queue.

2. The defensive ``if title is None: raise HTTPException(404, "unknown item …")``
   guards in ``mention_users`` / ``promote_to_kb`` / ``export_chat``. These are
   reachable only when ``title_of`` returns ``None`` *after* ``require_item``
   passed — forced deterministically by monkeypatching
   ``ItemLocator.title_of`` (``require_item`` uses ``find_work_item`` directly,
   so it still resolves the freshly-created item).

All wiring is ScriptedAgentRunner / MockSandbox / HashEmbedder — no real LLM.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

import workspace_app.api.locator as locator_mod
from workspace_app.api import (
    AgentEvent,
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    create_app,
)
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.li_pipeline import build_chat_pipeline
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _FakeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ('{"insights": []}', False)


def _harness() -> TestClient:
    """create_app WITH a kb_chat_pipeline so promote-to-kb reaches the title
    check (it short-circuits to ``[]`` when the pipeline is None)."""
    spec = make_spec()
    embedder = HashEmbedder(dim=EMBED_DIM)
    events: list[AgentEvent] = [MessageDelta(text="ok"), RunDone()]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner(events),
        kb_embedder=embedder,
        kb_chat_pipeline=build_chat_pipeline(llm=_FakeLlm(), embedder=embedder),
    )
    return TestClient(app)


def _new_item(client: TestClient) -> str:
    return client.post("/a/rca/items", json={"title": "Oven drift"}).json()["resource_id"]


# ── 1) the SSE stream route bodies ───────────────────────────────────


@pytest.fixture
def _finite_sse(monkeypatch):
    """Replace ``subscribe_sse`` with a finite (empty) async iterator so a plain
    ``client.get`` on the stream route terminates instead of blocking on the
    real infinite live queue. The handler body (``require_item`` /
    ``require_chat`` + ``return StreamingResponse(subscribe_sse(...))``) still
    runs in full — which is the chat_routes.py coverage we're after.

    Yields the list of calls made to ``subscribe_sse`` (each a dict of the args
    the route passed) so a test can assert the route forwarded ``?since=``."""
    calls: list[dict] = []

    def _fake_subscribe_sse(
        self,
        key: str,
        user_id: str = "",
        *,
        since: int | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[str]:
        calls.append({"key": key, "user_id": user_id, "since": since})

        async def _frames() -> AsyncIterator[str]:
            return
            yield  # pragma: no cover — marks this an async generator

        return _frames()

    monkeypatch.setattr(ChatTurnEngine, "subscribe_sse", _fake_subscribe_sse)
    return calls


def test_stream_investigation_route_body_runs(_finite_sse):
    """GET /a/rca/items/{id}/stream dispatches the handler (require_item +
    return StreamingResponse(subscribe_sse(...)))."""
    client = _harness()
    item_id = _new_item(client)
    r = client.get(f"/a/rca/items/{item_id}/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")


def test_stream_chat_route_body_runs(_finite_sse):
    """GET /a/rca/items/{id}/chats/{chat_id}/stream — same, but for a named
    chat (also exercises require_chat + engine_key in the handler body)."""
    client = _harness()
    item_id = _new_item(client)
    chat_id = client.post(f"/a/rca/items/{item_id}/chats", json={"title": "side"}).json()["chat_id"]
    r = client.get(f"/a/rca/items/{item_id}/chats/{chat_id}/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")


def test_stream_investigation_forwards_since(_finite_sse):
    """A reconnecting client's `?since=<seq>` reaches subscribe_sse so it can
    replay the events missed during the gap."""
    client = _harness()
    item_id = _new_item(client)
    r = client.get(f"/a/rca/items/{item_id}/stream?since=7")
    assert r.status_code == 200
    assert _finite_sse[-1]["since"] == 7


def test_stream_investigation_defaults_since_to_none(_finite_sse):
    """A fresh connect (no `?since=`) forwards `since=None` — no replay, today's
    behavior."""
    client = _harness()
    item_id = _new_item(client)
    r = client.get(f"/a/rca/items/{item_id}/stream")
    assert r.status_code == 200
    assert _finite_sse[-1]["since"] is None


def test_stream_chat_forwards_since(_finite_sse):
    """The per-chat stream forwards `?since=` too."""
    client = _harness()
    item_id = _new_item(client)
    chat_id = client.post(f"/a/rca/items/{item_id}/chats", json={"title": "side"}).json()["chat_id"]
    r = client.get(f"/a/rca/items/{item_id}/chats/{chat_id}/stream?since=4")
    assert r.status_code == 200
    assert _finite_sse[-1]["since"] == 4


# ── 2) the defensive `title is None` 404 guards ──────────────────────


@pytest.fixture
def _title_of_returns_none(monkeypatch):
    """Force ItemLocator.title_of → None while require_item still resolves the
    item (it uses find_work_item directly, not title_of)."""
    monkeypatch.setattr(locator_mod.ItemLocator, "title_of", lambda self, item_id: None)


def test_mention_users_404_when_title_missing(_title_of_returns_none):
    client = _harness()
    item_id = _new_item(client)
    r = client.post(f"/a/rca/items/{item_id}/mentions", json={"user_ids": ["bob"], "note": "look"})
    assert r.status_code == 404
    assert r.json()["detail"].startswith("unknown item")


def test_promote_to_kb_404_when_title_missing(_title_of_returns_none):
    """promote_to_kb only reaches the title check when a kb_chat_pipeline is
    wired (else it returns [] first) — the harness wires one."""
    client = _harness()
    item_id = _new_item(client)
    r = client.post(f"/a/rca/items/{item_id}/promote-to-kb")
    assert r.status_code == 404
    assert r.json()["detail"].startswith("unknown item")


def test_export_chat_404_when_title_missing(_title_of_returns_none):
    client = _harness()
    item_id = _new_item(client)
    r = client.get(f"/a/rca/items/{item_id}/export-chat")
    assert r.status_code == 404
    assert r.json()["detail"].startswith("unknown item")
