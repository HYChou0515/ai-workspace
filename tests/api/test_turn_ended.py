"""Every turn that ends tells the ITEM's stream so, whichever chat ran it.

The file tree refreshed only on the default chat's `streaming` true→false edge,
seen by the open tab. A turn in any other chat (a second chat, a workflow chat)
streams on its own key, so the item's stream never saw it, and the tree stayed
stale until the user pressed refresh. A queued follow-up, a turn longer than the
"awaiting reply" window and a turn started elsewhere had no edge either.

The fix is a fact the backend states once per turn: `TurnEnded(chat_id)` on the
item's key, after the turn-end flush. The engine stream is read directly, as in
`test_file_broadcast.py` (ASGITransport cannot read an infinite response).
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport

import workspace_app.api.app as app_mod
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.api.event_bus import InMemoryEventBus
from workspace_app.api.events import TurnEnded
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient
from .conftest import register_rca_item


def _app():
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        get_user_id=lambda: "alice",
    )
    return app, register_rca_item(spec)


def _await_turn_ended(engine: ChatTurnEngine, key: str) -> asyncio.Task[TurnEnded]:
    """Subscribe NOW (before the turn), and resolve with the first TurnEnded."""
    sub = engine.subscribe(key)

    async def collect() -> TurnEnded:
        async for ev in sub:
            if isinstance(ev, TurnEnded):
                return ev
        raise AssertionError("stream ended without a TurnEnded")  # pragma: no cover

    return asyncio.create_task(collect())


async def _send(app, iid: str, chat: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/a/rca/items/{iid}/chats/{chat}/messages", json={"content": "go"})
        assert r.status_code in (200, 202), r.text


async def _chats(app, iid: str, *titles: str) -> list[str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return [
            (await c.post(f"/a/rca/items/{iid}/chats", json={"title": t})).json()["chat_id"]
            for t in titles
        ]


async def test_a_turn_in_a_second_chat_tells_the_item_stream_it_ended():
    app, iid = _app()
    _first, second = await _chats(app, iid, "first", "second")
    assert app.state.chat_send._locator.engine_key(iid, second) == second  # streams on its own key
    ended = _await_turn_ended(app.state.turn_engine, iid)

    await _send(app, iid, second)

    ev = await asyncio.wait_for(ended, 5)
    assert ev.chat_id == second


async def test_a_turn_in_the_default_chat_tells_it_too():
    app, iid = _app()
    (first,) = await _chats(app, iid, "first")
    assert app.state.chat_send._locator.engine_key(iid, first) == iid
    ended = _await_turn_ended(app.state.turn_engine, iid)

    await _send(app, iid, first)

    assert (await asyncio.wait_for(ended, 5)).chat_id == first


# The flush is best-effort durability; the announcement is what the viewer
# refreshes on, so a flush that fails must not swallow it.
async def test_a_flush_that_fails_still_announces_the_end(monkeypatch):
    app, iid = _app()
    (first,) = await _chats(app, iid, "first")

    async def broken(_item: str) -> None:
        raise RuntimeError("flush failed")

    monkeypatch.setattr(app.state.chat_send, "_flush_item", broken)
    ended = _await_turn_ended(app.state.turn_engine, iid)

    await _send(app, iid, first)

    assert (await asyncio.wait_for(ended, 5)).chat_id == first


class _Hangs:
    """Streams one delta, then waits to be stopped."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, prompt, ctx):  # noqa: ANN001, ANN201
        self.started.set()
        yield MessageDelta(text="working")
        await asyncio.sleep(30)
        yield RunDone()  # pragma: no cover — reached only if the Stop is lost


# A stopped turn may already have written files; Stop ends it like any other.
async def test_a_stopped_turn_announces_its_end_too():
    spec = make_spec(default_user="u")
    runner = _Hangs()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
        get_user_id=lambda: "alice",
        send_await_timeout=0.1,
    )
    iid = register_rca_item(spec)
    _first, second = await _chats(app, iid, "first", "second")
    ended = _await_turn_ended(app.state.turn_engine, iid)
    await _send(app, iid, second)
    await asyncio.wait_for(runner.started.wait(), 5)

    await app.state.turn_engine.cancel_current(second)

    assert (await asyncio.wait_for(ended, 5)).chat_id == second


async def test_a_workflow_turn_tells_the_item_stream_it_ended(monkeypatch):
    spec = make_spec()
    captured: dict[str, object] = {}
    real = app_mod.WorkflowExecutor

    def _capture(**kw):
        captured["ex"] = ex = real(**kw)
        return ex

    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    item = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    executor = captured["ex"]
    ended = _await_turn_ended(app.state.turn_engine, item)

    await executor.drive_turn(item, "no-such-chat", "u", "hello", None)  # ty: ignore[unresolved-attribute]

    rid, _conv = executor._locator.conversation_for(item)  # ty: ignore[unresolved-attribute]
    assert (await asyncio.wait_for(ended, 5)).chat_id == rid


# The item's stream may be watched only on ANOTHER pod: this pod runs the turn
# but nobody here opened the item, so it holds no session for the item's key.
# The announcement still has to cross the bus — dropping it there is the same
# stale tree, one pod over. `file_changed` (a human save) goes the same way.
async def test_publishing_with_no_viewer_on_this_pod_still_reaches_another_pod():
    bus = InMemoryEventBus()
    turn_pod = ChatTurnEngine(ScriptedAgentRunner([]), event_bus=bus, pod_id="A")
    viewer_pod = ChatTurnEngine(ScriptedAgentRunner([]), event_bus=bus, pod_id="B")
    ended = _await_turn_ended(viewer_pod, "item-1")
    await asyncio.sleep(0)  # let the subscriber register

    turn_pod.publish("item-1", TurnEnded(chat_id="c1"))

    assert (await asyncio.wait_for(ended, 3)).chat_id == "c1"
    await turn_pod.forget("item-1")
    await viewer_pod.forget("item-1")


@pytest.mark.parametrize("event", [TurnEnded(chat_id="c1")])
def test_turn_ended_crosses_the_bus_as_itself(event):
    from dataclasses import asdict

    from workspace_app.api.events import event_from_dict

    assert event_from_dict(asdict(event)) == event
