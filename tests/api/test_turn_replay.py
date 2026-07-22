"""Same-pod reconnect must be lossless.

The `#43` broadcast used to fan each event out only to the subscribers attached
at that instant, with no buffer — so events emitted while a viewer's SSE stream
was briefly dropped were lost forever, even when the client reconnected to the
SAME pod still running the turn. An in-pod per-session monotonic ``seq`` + a
bounded ring buffer let a reconnect ask ``?since=<seq>`` and get exactly the
events it missed replayed, in order, before the live stream resumes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from workspace_app.api import MessageDelta, create_app
from workspace_app.api.events import Presence
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.config.schema import ServerSettings
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


class _Runner:
    """Never invoked here — these tests drive `publish`/`subscribe_sse` directly."""

    async def run(self, content, ctx):  # noqa: ANN001, ANN201 — pragma: no cover
        if False:  # pragma: no cover
            yield None


def _engine(buffer: int = 2000) -> ChatTurnEngine:
    return ChatTurnEngine(_Runner(), replay_buffer_events=buffer)  # ty: ignore[invalid-argument-type]


async def _read_frame(gen: Any) -> dict[str, Any]:
    """The next real SSE data frame (skipping `: heartbeat` comments), parsed."""
    while True:
        frame = await asyncio.wait_for(gen.__anext__(), 3)
        if frame.startswith(":"):
            continue
        assert frame.startswith("data: "), frame
        return json.loads(frame[len("data: ") :].strip())


async def test_reconnect_with_since_replays_events_missed_during_the_gap():
    engine = _engine()
    key = "inv"

    # A viewer attaches (creating the session), sees two events, then its stream
    # drops. The session — and its buffer — persist across the disconnect.
    sub1 = engine.subscribe_sse(key, heartbeat_interval=5.0)
    engine.publish(key, MessageDelta(text="a"))  # seq 1
    engine.publish(key, MessageDelta(text="b"))  # seq 2
    await sub1.aclose()

    # Two more events fire while NOBODY is attached — a zero-buffer broadcast
    # would lose these forever.
    engine.publish(key, MessageDelta(text="c"))  # seq 3
    engine.publish(key, MessageDelta(text="d"))  # seq 4

    # Reconnect, telling the server the last seq we saw (2). It must replay only
    # what we missed, in order.
    sub2 = engine.subscribe_sse(key, heartbeat_interval=5.0, since=2)
    f1 = await _read_frame(sub2)
    f2 = await _read_frame(sub2)
    assert (f1["text"], f1["seq"]) == ("c", 3)
    assert (f2["text"], f2["seq"]) == ("d", 4)

    await sub2.aclose()
    await engine.forget(key)


async def test_a_fresh_connect_without_since_replays_nothing():
    # First-time connect (no prior stream) must behave exactly as before: no
    # replay, only live events from here on. Otherwise every page load would dump
    # the whole buffer.
    engine = _engine()
    key = "inv"

    warm = engine.subscribe_sse(key, heartbeat_interval=5.0)  # create the session
    engine.publish(key, MessageDelta(text="a"))
    engine.publish(key, MessageDelta(text="b"))
    await warm.aclose()

    sub = engine.subscribe_sse(key, heartbeat_interval=0.05)  # no `since`
    # Nothing live is flowing, so the first thing it sees is a heartbeat — NOT a
    # replayed `data:` frame for a/b.
    frame = await asyncio.wait_for(sub.__anext__(), 3)
    assert frame.startswith(":"), frame

    await sub.aclose()
    await engine.forget(key)


async def test_presence_is_neither_buffered_nor_seq_stamped():
    # Presence is an ephemeral roster snapshot, re-broadcast on every join. It must
    # not take a seq (or it would push the data events' seqs around) and must not be
    # replayed (a stale roster is misleading).
    engine = _engine()
    key = "inv"

    warm = engine.subscribe_sse(key, heartbeat_interval=5.0)
    engine.publish(key, MessageDelta(text="a"))  # seq 1
    engine.publish(key, Presence(users=["alice"]))  # ephemeral — no seq, not buffered
    engine.publish(key, MessageDelta(text="b"))  # seq 2, NOT 3 — presence took no seq
    await warm.aclose()

    sub = engine.subscribe_sse(key, heartbeat_interval=0.05, since=1)
    f = await _read_frame(sub)
    assert (f["text"], f["seq"]) == ("b", 2)
    # Nothing else replayed — no stale Presence frame follows, only a heartbeat.
    nxt = await asyncio.wait_for(sub.__anext__(), 3)
    assert nxt.startswith(":"), nxt

    await sub.aclose()
    await engine.forget(key)


async def test_a_gap_larger_than_the_buffer_replays_only_what_survived():
    # With a tiny ring, the oldest events are evicted. A client that missed more
    # than the ring holds cannot get it all back — the replay starts at the oldest
    # survivor, whose seq exceeds `since + 1`, which is exactly how the FE learns a
    # piece is genuinely lost and keeps the "少了一段" banner.
    engine = _engine(buffer=2)
    key = "inv"

    warm = engine.subscribe_sse(key, heartbeat_interval=5.0)
    for text in "abcd":
        engine.publish(key, MessageDelta(text=text))  # seq 1..4; ring keeps only 3,4
    await warm.aclose()

    sub = engine.subscribe_sse(key, heartbeat_interval=5.0, since=1)
    f = await _read_frame(sub)
    assert f["seq"] == 3  # seq 2 was evicted → the replay cannot start at since+1
    assert f["seq"] > 1 + 1  # first replayed seq exceeds since+1 ⇒ a real gap

    await sub.aclose()
    await engine.forget(key)


async def test_a_zero_length_buffer_disables_replay():
    # `replay_buffer_events=0` ⇒ an always-empty ring: seq still advances but
    # nothing is retained, so a reconnect replays nothing and degrades to today's
    # re-hydrate path.
    engine = _engine(buffer=0)
    key = "inv"

    warm = engine.subscribe_sse(key, heartbeat_interval=5.0)
    engine.publish(key, MessageDelta(text="a"))
    engine.publish(key, MessageDelta(text="b"))
    await warm.aclose()

    sub = engine.subscribe_sse(key, heartbeat_interval=0.05, since=1)
    frame = await asyncio.wait_for(sub.__anext__(), 3)
    assert frame.startswith(":"), frame  # heartbeat, nothing replayed

    await sub.aclose()
    await engine.forget(key)


def test_replay_buffer_size_defaults_to_2000():
    # The dataclass default is the single source of truth for the knob.
    assert ServerSettings().turn_replay_buffer_events == 2000


def test_create_app_wires_the_replay_buffer_size_into_the_engine():
    # A size accepted in config but never read would be a dead knob — prove it
    # reaches the engine that owns the broadcast sessions.
    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),  # ty: ignore[invalid-argument-type]
        turn_replay_buffer_events=7,
    )
    assert app.state.turn_engine._replay_buffer_events == 7
