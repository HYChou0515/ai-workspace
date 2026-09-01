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

from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.api.turn_activity import SpecstarTurnActivityStore, register_turn_activity
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


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
