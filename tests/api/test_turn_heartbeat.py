"""A turn says it is alive while it runs, whether or not it is producing.

The heartbeat is what lets a viewer on another pod tell "working, nothing to
show yet" from "nobody is coming". It has to be driven by a TIMER: the turns
this question is asked about are precisely the ones emitting nothing — a long
tool call, a slow first token — so a bump hung off the event stream goes quiet
exactly when the answer matters.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.turn_activity import ITurnActivityStore
from workspace_app.api.turns import ChatTurnEngine


class _SilentRunner:
    """One turn that produces nothing for a while, then finishes — the shape a
    long tool call has on the wire."""

    def __init__(self, quiet_s: float) -> None:
        self._quiet_s = quiet_s

    async def run(self, content: str, ctx: AgentToolContext):  # noqa: ANN201
        await asyncio.sleep(self._quiet_s)
        yield MessageDelta(text="finally")
        yield RunDone()


class _FakeActivity(ITurnActivityStore):
    """Implements the interface, so the compiler checks this double against the
    contract it stands in for — the failure mode this branch kept hitting was a
    double the real caller could never have used."""

    def __init__(self) -> None:
        self.beats: list[str] = []
        self.ended: list[str] = []

    async def bump(self, key: str) -> None:
        self.beats.append(key)

    async def finished(self, key: str) -> None:
        self.ended.append(key)

    async def alive(self, key: str, *, stale_after_ms: int = 0) -> bool:  # pragma: no cover
        return False


async def _run_one(engine: ChatTurnEngine, key: str) -> list:
    produced: list = []
    await engine.enqueue(
        key,
        "go",
        AgentToolContext(investigation_id="ws-1"),
        on_complete=produced.extend,
    )
    return produced


async def test_a_turn_producing_nothing_still_beats():
    activity = _FakeActivity()
    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=0.25),  # ty: ignore[invalid-argument-type]
        turn_activity=activity,
        heartbeat_s=0.05,
    )

    await _run_one(engine, "chat-1")

    # Several beats across a quarter second in which the turn emitted nothing —
    # the case the whole signal exists for.
    assert len(activity.beats) >= 3, activity.beats
    assert set(activity.beats) == {"chat-1"}


async def test_the_beat_stops_when_the_turn_does():
    activity = _FakeActivity()
    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=0.05),  # ty: ignore[invalid-argument-type]
        turn_activity=activity,
        heartbeat_s=0.01,
    )

    await _run_one(engine, "chat-1")
    settled = len(activity.beats)
    await asyncio.sleep(0.08)

    assert activity.ended == ["chat-1"]
    assert len(activity.beats) == settled, "the heartbeat outlived its turn"


async def test_a_cancelled_turn_stops_beating():
    # Stop is the most-travelled way a turn ends. A beat that outlived it would
    # keep answering "someone is working on this" for a turn the user just
    # killed — the same lie as the notices this branch spent itself removing,
    # only now with a server-side fact behind it, which is worse.
    activity = _FakeActivity()
    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=5),  # ty: ignore[invalid-argument-type]
        turn_activity=activity,
        heartbeat_s=0.01,
    )
    fut = engine.enqueue(
        "chat-1",
        "go",
        AgentToolContext(investigation_id="ws-1"),
        on_complete=lambda _msgs: None,
    )
    await asyncio.sleep(0.05)

    await engine.cancel_current("chat-1")
    with contextlib.suppress(asyncio.CancelledError):
        await fut
    await asyncio.sleep(0.05)
    settled = len(activity.beats)
    await asyncio.sleep(0.05)

    assert len(activity.beats) == settled, "the heartbeat outlived a cancelled turn"
    # …and the turn is recorded as OVER, not merely left to age out. Stop is the
    # ending a person is most likely to be watching, so thirty seconds of "maybe"
    # after it is thirty seconds of exactly the doubt this signal removes.
    assert activity.ended == ["chat-1"]


async def test_no_store_configured_is_simply_no_signal():
    # The single-pod / test default: nothing to record into, and a turn that runs
    # exactly as it did before.
    engine = ChatTurnEngine(_SilentRunner(quiet_s=0.01))  # ty: ignore[invalid-argument-type]

    produced = await _run_one(engine, "chat-1")

    assert any(m.role == "assistant" for m in produced)


async def test_a_failing_store_never_takes_the_turn_with_it():
    class _Broken(_FakeActivity):
        async def bump(self, key: str) -> None:
            raise RuntimeError("store unreachable")

    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=0.05),  # ty: ignore[invalid-argument-type]
        turn_activity=_Broken(),
        heartbeat_s=0.01,
    )

    produced = await _run_one(engine, "chat-1")

    assert any(m.role == "assistant" for m in produced)


class _RestoringActivity(ITurnActivityStore):
    """Models the real store's two awkward properties, because a double without
    them goes green on the defect this pins.

    1. A write is OFF-LOOP (`asyncio.to_thread`) and therefore uncancellable:
       cancelling the await abandons the wait, not the write.
    2. A bump RESTORES a row that `finished` soft-deleted — deliberately, so a
       second turn on the same chat reads as alive again.

    Together those mean a beat still in flight when the turn ends can undo the
    `finished` that follows it.
    """

    def __init__(self, write_s: float) -> None:
        self.alive_now = False
        self.ended: list[str] = []
        self._write_s = write_s

    def _write(self, _key: str) -> None:
        time.sleep(self._write_s)
        self.alive_now = True

    async def bump(self, key: str) -> None:
        await asyncio.to_thread(self._write, key)

    async def finished(self, key: str) -> None:
        self.ended.append(key)
        self.alive_now = False

    async def alive(self, key: str, *, stale_after_ms: int = 0) -> bool:
        return self.alive_now


async def test_a_beat_still_in_flight_cannot_outlive_its_turn():
    # The write is on a worker thread, so cancelling the beat does not stop it.
    # Landing after `finished`, it restores the row — and the turn reads as alive
    # for a full staleness window after it ended, which is precisely the thirty
    # seconds of "maybe" that `finished` exists to remove.
    activity = _RestoringActivity(write_s=0.2)
    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=0.05),  # ty: ignore[invalid-argument-type]
        turn_activity=activity,
        heartbeat_s=0.01,
    )

    await _run_one(engine, "chat-1")
    # Long enough that any abandoned write would have landed by now.
    await asyncio.sleep(0.4)

    assert activity.ended == ["chat-1"]
    assert activity.alive_now is False, "a stray beat resurrected a finished turn"


async def test_the_streamed_kb_turn_beats_too():
    # The commit's claim is that BOTH turn shapes carry the signal, because one a
    # caller can only trust on some surfaces is one they cannot trust. The KB
    # chat takes the other entry point (`stream`, not `enqueue`), and until this
    # test the whole streamed half could be deleted with nothing going red.
    activity = _FakeActivity()
    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=0.2),  # ty: ignore[invalid-argument-type]
        turn_activity=activity,
        heartbeat_s=0.02,
    )

    resp = await engine.stream(
        "kbchat-1",
        "go",
        AgentToolContext(investigation_id="kb-1"),
        on_complete=lambda _msgs: None,
    )
    # Drain the SSE body so the turn runs to completion, as a real client does.
    async for _chunk in resp.body_iterator:
        pass
    # The stream closes when the turn's events run out; the turn task's own
    # teardown (which records the end) lands a tick later.
    await asyncio.sleep(0.05)

    assert len(activity.beats) >= 3, activity.beats
    assert set(activity.beats) == {"kbchat-1"}
    assert activity.ended == ["kbchat-1"]


class _SlowFinishActivity(ITurnActivityStore):
    """A store whose beat write takes long enough that the turn's teardown has to
    wait on it — the case that decides whether waiting is safe."""

    def __init__(self, write_s: float) -> None:
        self.write_s = write_s
        self.ended: list[str] = []

    async def bump(self, key: str) -> None:
        await asyncio.sleep(self.write_s)

    async def finished(self, key: str) -> None:
        self.ended.append(key)

    async def alive(self, key: str, *, stale_after_ms: int = 0) -> bool:
        return key not in self.ended


async def test_a_stop_records_the_end_even_when_a_beat_write_is_slow():
    # Stop is the ending a person is most likely to be watching, and it arrives as
    # a `CancelledError` — which no `suppress(Exception)` catches and which must
    # not be swallowed. So anything the teardown waits for while still ON the
    # turn's own task is in the cancellation's path: the wait is abandoned, the
    # end goes unrecorded, and the stopped turn reads ALIVE for a whole staleness
    # window. That is the "maybe" after an ended turn this signal exists to
    # remove, now with a server-side fact behind it.
    # Measured trigger: a beat write slower than `poll_interval`, which is left at
    # its PRODUCTION default here on purpose — #349's watcher then fires its own
    # cancel at the turn while the teardown is still waiting, and that second
    # cancellation is what the wait cannot survive. A fast store never sees it,
    # which is why every earlier probe of this path came back clean.
    activity = _SlowFinishActivity(write_s=0.6)
    engine = ChatTurnEngine(
        _SilentRunner(quiet_s=5),  # ty: ignore[invalid-argument-type]
        turn_activity=activity,
        heartbeat_s=0.01,
    )
    fut = engine.enqueue(
        "chat-1",
        "go",
        AgentToolContext(investigation_id="ws-1"),
        on_complete=lambda _msgs: None,
    )
    await asyncio.sleep(0.05)

    await engine.cancel_current("chat-1")
    with contextlib.suppress(asyncio.CancelledError):
        await fut
    # Well past the in-flight write and any drain.
    await asyncio.sleep(1.0)

    assert activity.ended == ["chat-1"], "a Stop left the ended turn reading ALIVE"
