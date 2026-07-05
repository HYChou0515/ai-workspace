"""#455 P4: the item stream keeps a live presence roster — the distinct users
currently subscribed. A join or leave broadcasts the updated roster (`Presence`)
to every viewer, so an open board shows who else is here. Per-pod + ephemeral
(a viewer on another pod isn't counted), consistent with the SSE broadcast. The
engine is exercised directly (ASGITransport can't read an infinite response).
"""

from __future__ import annotations

import asyncio

from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from .conftest import register_rca_item


def _app():
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    return app, register_rca_item(spec)


async def _next(gen):
    return await asyncio.wait_for(gen.__anext__(), 3)


async def test_roster_grows_on_join_and_shrinks_on_leave():
    app, iid = _app()
    engine = app.state.turn_engine

    alice = engine.subscribe(iid, "alice")
    assert (await _next(alice)).users == ["alice"]  # alice's own join

    bob = engine.subscribe(iid, "bob")
    assert (await _next(alice)).users == ["alice", "bob"]  # bob joined
    assert (await _next(bob)).users == ["alice", "bob"]  # starts bob's generator

    await bob.aclose()  # bob suspended at the queue → finally runs the leave broadcast
    assert (await _next(alice)).users == ["alice"]  # bob left

    await alice.aclose()


async def test_dedupes_a_users_multiple_tabs():
    app, iid = _app()
    engine = app.state.turn_engine

    alice1 = engine.subscribe(iid, "alice")
    assert (await _next(alice1)).users == ["alice"]

    alice2 = engine.subscribe(iid, "alice")  # a second tab, same user
    assert (await _next(alice1)).users == ["alice"]  # still one distinct viewer

    await alice2.aclose()
    await alice1.aclose()


async def test_anonymous_stream_does_not_touch_presence():
    """A per-chat / workflow stream (no user id) subscribes without emitting any
    presence event, so it never perturbs the roster."""
    app, iid = _app()
    engine = app.state.turn_engine

    anon = engine.subscribe(iid)  # user_id="" → no presence broadcast
    # A real join now broadcasts the roster (which excludes the anonymous sub).
    alice = engine.subscribe(iid, "alice")
    assert (await _next(anon)).users == ["alice"]
    assert (await _next(alice)).users == ["alice"]

    await alice.aclose()
    await anon.aclose()
