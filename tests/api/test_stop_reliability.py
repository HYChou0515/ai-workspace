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
    is cancelled. ``completed`` flips only if the turn is NOT cancelled;
    ``started`` flips the moment the turn reaches the model at all."""

    def __init__(self) -> None:
        self.completed = False
        self.started = False

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.started = True
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


class _QuickRunner:
    """Reaches the model and finishes. For the cases that assert a turn DID run:
    a runner that hangs would leave the turn to be torn down at teardown, which
    is a second thing for the test to be about."""

    def __init__(self) -> None:
        self.started = False

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.started = True
        yield RunDone()


async def test_a_stop_during_the_preamble_is_not_lost():
    """P1: a Stop pressed BEFORE the turn is queued must still stop it.

    `chat_send` persists the user's message and then spends real time preparing
    the turn — compaction (which may call an LLM), a cold sandbox wake, context
    and skill file reads, the `/tokenize` probe. Seconds, on a bad day, and it is
    exactly the window in which someone gives up and presses Stop.

    Nothing is queued yet, so there is no `current_turn` for the same-pod
    fast-path to find, and the epoch bump is the ONLY record that the Stop
    happened. Stamping the epoch when the turn is finally dequeued reads that
    already-bumped value back, so `> my_epoch` never trips and the whole turn
    then runs as though Stop had never been pressed. Stamping it when the
    MESSAGE IS PERSISTED — before the preamble — is what makes the bump mean
    something.
    """
    control = InMemoryTurnControl()
    runner = _HangingRunner()
    engine = ChatTurnEngine(runner, turn_control=control)
    key = "inv"
    try:
        # The message is persisted; the caller stamps here, before the preamble.
        stamped = await engine.cancel_epoch(key)

        # …the preamble runs, and the user hits Stop while it does.
        await engine.cancel_current(key)

        # …the preamble finishes and queues the turn it had been preparing.
        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=lambda _: None, epoch=stamped
        )

        # Bounded: without the fix the turn runs and hangs for 30s, so a lost
        # Stop is a timeout (a failure) rather than a test that waits it out.
        await asyncio.wait_for(fut, 2)
        assert not runner.started
    finally:
        await engine.forget(key)


async def test_a_turn_queued_after_the_stop_still_runs():
    """The other half, and the reason this keys on the stamp rather than on "has
    anyone ever pressed Stop": the NEXT message must not inherit the last Stop.

    Its stamp is taken after the bump, so the same comparison that stands the
    interrupted turn down lets this one through — which is what keeps Stop from
    quietly becoming "this conversation is over".
    """
    control = InMemoryTurnControl()
    runner = _QuickRunner()
    engine = ChatTurnEngine(runner, turn_control=control)
    key = "inv"
    try:
        await engine.cancel_current(key)  # an earlier Stop
        stamped = await engine.cancel_epoch(key)  # a NEW message, stamped after it

        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=lambda _: None, epoch=stamped
        )

        await asyncio.wait_for(fut, 2)
        assert runner.started
    finally:
        await engine.forget(key)


async def test_a_stop_during_the_preamble_is_not_lost_through_the_real_send_path():
    """The same invariant, entered through `POST /messages` instead of the engine.

    The engine can only honour a stamp somebody hands it, and for a long time
    nobody did — an engine-level test would have passed against a `chat_send`
    that never passed one. Patching `build_chat_turn` puts the Stop exactly where
    the report puts it: inside the preparation, before any turn exists to cancel.
    """
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _QuickRunner()
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    engine = app.state.turn_engine
    builder = app.state.chat_send._turn_ctx
    real_build = builder.build_chat_turn

    async def stop_while_preparing(*a, **kw):
        await engine.cancel_current(iid)
        return await real_build(*a, **kw)

    builder.build_chat_turn = stop_while_preparing

    TestClient(app).post(f"/a/rca/items/{iid}/messages", json={"content": "yo"})

    assert not runner.started
