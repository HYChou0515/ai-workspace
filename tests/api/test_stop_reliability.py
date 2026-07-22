"""Stop must reliably reach the running turn — the #349 epoch stamp had a race.

The workspace worker stamped ``my_epoch = await current(key)`` and only set
``session.current_turn`` AFTER that await. A Stop landing inside the read window
found ``current_turn`` still ``None`` (its same-pod fast-path a no-op) AND its
``advance()`` had already bumped the epoch the stamp then read back — so the
watcher's ``> my_epoch`` never tripped and the Stop was silently lost. This is
the intermittent "Stop sometimes does nothing" report.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.events import AgentEvent
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.turn_control import InMemoryTurnControl


class _GatedTurnControl(InMemoryTurnControl):
    """``current()`` blocks on a gate the first time it is called, so a test can
    drive a Stop into the exact window between the worker reading the epoch and
    the turn becoming cancellable."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self._gate = asyncio.Event()

    async def current(self, key: str) -> int:
        self.entered.set()
        await self._gate.wait()
        return await super().current(key)

    def release(self) -> None:
        self._gate.set()


class _HangingRunner:
    """Streams one delta then hangs, so the turn is unmistakably RUNNING until it
    is cancelled. ``completed`` flips only if the turn is NOT cancelled."""

    def __init__(self) -> None:
        self.completed = False

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="working")
        await asyncio.sleep(30)
        self.completed = True  # pragma: no cover — reached only if the Stop is lost
        yield RunDone()


async def test_the_turn_is_cancellable_before_the_epoch_is_stamped():
    """The fix in one invariant: by the time the worker reads the epoch, the turn
    must ALREADY be running and recorded as `current_turn`, so a Stop landing in
    that read window finds it via the same-pod fast-path. Before the fix,
    `current_turn` was still None throughout the read."""
    control = _GatedTurnControl()
    engine = ChatTurnEngine(_HangingRunner(), turn_control=control)
    key = "inv"
    try:
        engine.enqueue(key, "go", AgentToolContext(), on_complete=lambda _: None)
        await asyncio.wait_for(control.entered.wait(), 2)  # worker is inside current()
        session = engine._ws_session(key)
        assert session.current_turn is not None
        assert not session.current_turn.done()
    finally:
        await engine.forget(key)


async def test_a_stop_in_the_stamp_window_finds_and_cancels_the_turn():
    """Behaviourally: a Stop arriving while the worker is still stamping the epoch
    is honoured — the same-pod fast-path finds the running turn and cancels it.
    Before the fix `current_turn` was None here, so the fast-path was a no-op and
    the Stop was lost. The gate stays CLOSED for the whole test, so the worker is
    pinned in the stamp window and no turn is left hanging."""
    control = _GatedTurnControl()
    engine = ChatTurnEngine(_HangingRunner(), turn_control=control)
    key = "inv"
    try:
        engine.enqueue(key, "go", AgentToolContext(), on_complete=lambda _: None)
        await asyncio.wait_for(control.entered.wait(), 2)  # worker pinned inside current()

        await engine.cancel_current(key)  # Stop, driven into the stamp window

        turn = engine._ws_session(key).current_turn
        assert turn is not None  # the turn existed to be found (the race fix)
        assert turn.done()  # and the fast-path cancelled it
    finally:
        await engine.forget(key)
