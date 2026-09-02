"""Asking whether anyone is driving this chat's turn.

The screen asks this once, at the moment it would otherwise have to guess: after
minutes of silence, is this a turn working quietly on another pod, or is nobody
coming? Everything before this signal answered by inference — elapsed time,
broadcast sequence numbers, whether the store had caught up — and each inference
was wrong in one direction or the other.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from httpx import ASGITransport

from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.api.turn_activity import SpecstarTurnActivityStore, register_turn_activity
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient


def _app_and_item():
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return app, spec, item_id


def test_a_chat_with_no_turn_running_says_so():
    app, _spec, item_id = _app_and_item()
    with TestClient(app) as client:
        resp = client.get(f"/api/a/playground/items/{item_id}/turn-alive")

    assert resp.status_code == 200
    assert resp.json() == {"alive": False}


def test_a_chat_whose_turn_is_beating_says_so():
    app, spec, item_id = _app_and_item()
    register_turn_activity(spec)
    store = SpecstarTurnActivityStore(spec)

    # Beat through the same store a driving pod writes with, so this asserts the
    # route reads a real record rather than a fake returning what it was told.
    asyncio.run(store.bump(item_id))

    with TestClient(app) as client:
        resp = client.get(f"/api/a/playground/items/{item_id}/turn-alive")

    assert resp.json() == {"alive": True}


def test_an_unknown_item_is_not_readable():
    app, _spec, _item_id = _app_and_item()
    with TestClient(app) as client:
        resp = client.get("/api/a/playground/items/nope/turn-alive")

    assert resp.status_code == 404


class _GatedRunner:
    """A turn that cannot finish until the test releases it — so "still running"
    is a fact about the engine, not a race the test hopes to win."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def run(self, content, ctx):  # noqa: ANN001, ANN201
        await self._gate.wait()
        yield RunDone()


def _gated_app_and_item(gate: asyncio.Event):
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_GatedRunner(gate),  # ty: ignore[invalid-argument-type]
        # Short enough that the still-gated turn detaches instead of hanging the POST.
        send_await_timeout=0.3,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return app, item_id


async def test_a_turn_on_a_non_default_chat_is_visible_to_that_chat():
    # The engine keys every non-default chat on its OWN id (`locator.engine_key`),
    # and EVERY workflow chat is a non-default chat — which is precisely where the
    # long silent turns this signal exists for happen. Asking about the item id
    # would answer about a different chat entirely.
    gate = asyncio.Event()
    app, item_id = _gated_app_and_item(gate)
    base = f"/a/playground/items/{item_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        first = (await c.post(f"{base}/chats", json={"title": "a"})).json()["chat_id"]
        second = (await c.post(f"{base}/chats", json={"title": "b"})).json()["chat_id"]
        assert first != second
        await c.post(f"{base}/chats/{second}/messages", json={"content": "go"})

        resp = await c.get(f"{base}/chats/{second}/turn-alive")

        assert resp.status_code == 200
        assert resp.json() == {"alive": True}, "a turn IS being driven on this chat"

    # A non-default chat's engine key IS its chat id. Release + drain so the turn
    # does not outlive the test on a shared loop.
    gate.set()
    await app.state.turn_engine.forget(second)


async def test_a_busy_default_chat_does_not_make_another_chat_look_alive():
    # The other direction of the same mistake: the item-keyed answer would tell a
    # viewer sitting on an idle chat "還在跑,只是這段時間沒有輸出" — and offer them
    # no retry — because some OTHER chat is busy.
    gate = asyncio.Event()
    app, item_id = _gated_app_and_item(gate)
    base = f"/a/playground/items/{item_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # Order matters: the default chat is the EARLIEST-born free chat, so the
        # busy one has to be created first, or the "idle" chat becomes the default
        # and really is the one running.
        await c.post(f"{base}/messages", json={"content": "go"})
        other = (await c.post(f"{base}/chats", json={"title": "idle"})).json()["chat_id"]
        chats = (await c.get(f"{base}/chats")).json()
        default = [ch for ch in chats if ch["is_default"]]
        assert default and default[0]["chat_id"] != other, "the busy chat is the default"

        resp = await c.get(f"{base}/chats/{other}/turn-alive")

        assert resp.json() == {"alive": False}, "nothing is running on the chat on screen"

    gate.set()
    await app.state.turn_engine.forget(item_id)
