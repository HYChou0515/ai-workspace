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
import contextlib
from collections.abc import AsyncIterator

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.events import AgentEvent
from workspace_app.api.turns import ChatTurnEngine, TurnMessage
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
    fast-path to find. The send registers itself as PREPARING before the
    preamble and hands that token to `enqueue`; a Stop marks whatever is
    preparing, and the worker declines to start it.
    """
    control = InMemoryTurnControl()
    runner = _HangingRunner()
    engine = ChatTurnEngine(runner, turn_control=control)
    key = "inv"
    try:
        # The message is persisted; the send registers before the preamble.
        pending = engine.preparing(key)

        # …the preamble runs, and the user hits Stop while it does.
        await engine.cancel_current(key)

        # …the preamble finishes and queues the turn it had been preparing.
        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=lambda _: None, pending=pending
        )

        # Bounded: without the fix the turn runs and hangs for 30s, so a lost
        # Stop is a timeout (a failure) rather than a test that waits it out.
        await asyncio.wait_for(fut, 2)
        assert not runner.started
    finally:
        await engine.forget(key)


async def test_a_turn_queued_after_the_stop_still_runs():
    """The other half, and the reason this keys on a per-send token rather than
    on "has anyone ever pressed Stop": the NEXT message must not inherit the
    last Stop.

    Its token is registered after the bump and was never marked, so the same
    check that declines the interrupted turn lets this one through — which is
    what keeps Stop from quietly becoming "this conversation is over".
    """
    control = InMemoryTurnControl()
    runner = _QuickRunner()
    engine = ChatTurnEngine(runner, turn_control=control)
    key = "inv"
    try:
        await engine.cancel_current(key)  # an earlier Stop
        pending = engine.preparing(key)  # a NEW message, registered after it

        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=lambda _: None, pending=pending
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


async def test_stop_cancels_interruptible_preparation():
    """P3: work a caller runs BEFORE the turn can still be reached by Stop, if it
    asks to be.

    Compaction is the case: it calls an LLM and can take many seconds, but it
    runs in `chat_send._send` rather than in a turn, and `cancel_current` only
    ever knew about `current_turn`. Running it through here gives Stop something
    to cancel without touching the rest of the preparation around it — which must
    NOT be cancelled, because it is not atomic (see P1).
    """
    engine = ChatTurnEngine(_QuickRunner())
    key = "inv"
    started = asyncio.Event()
    outcome: list[str] = []

    async def slow_preparation() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            outcome.append("cancelled")
            raise
        outcome.append("finished")  # pragma: no cover — only if the Stop is lost

    try:
        running = asyncio.create_task(engine.run_interruptible(key, slow_preparation()))
        await asyncio.wait_for(started.wait(), 2)

        await engine.cancel_current(key)

        # Bounded: an unreachable preparation hangs for 30s, so a lost Stop is a
        # timeout rather than a test that waits it out.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(running, 2)
        assert outcome == ["cancelled"]
    finally:
        await engine.forget(key)


async def test_preparation_that_finishes_is_forgotten():
    """The registry must not accumulate finished work — a chat left open all day
    would otherwise hold every preparation it ever ran, and a later Stop would
    spend its time cancelling tasks that ended hours ago."""
    engine = ChatTurnEngine(_QuickRunner())
    key = "inv"

    async def quick() -> str:
        return "done"

    try:
        assert await engine.run_interruptible(key, quick()) == "done"
        assert not engine._ws_session(key).preparing
    finally:
        await engine.forget(key)


async def test_a_stop_during_compaction_cancels_the_summariser():
    """P3 through the real send path: compaction is an LLM call standing between
    a person and their answer, and Stop must reach it.

    It runs in `chat_send._send`, before any turn exists, so `cancel_current`
    could not see it however long it took — the composer said the turn had
    stopped while a summariser kept going.

    Nothing is half-written when it is cancelled: the summary is inserted in one
    step after the call returns, so the store is either untouched or complete.
    That is what makes this safe to cancel where the rest of the preparation
    around it is not (P1).
    """
    from httpx import ASGITransport

    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import AsyncClient
    from .conftest import register_rca_item

    class _HangingCompactor:
        """The summariser hangs, so the send is unmistakably INSIDE compaction
        when the Stop lands."""

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            self.started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            yield RunDone()  # pragma: no cover — only if the Stop is lost

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _HangingCompactor()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "alice",
        context_limit=6_000,
    )
    rm = spec.get_resource_manager(Conversation)
    seeded = [Message(role="user", content=f"很久以前的第{i}個問題" * 40) for i in range(12)]
    conv = rm.create(Conversation(item_id=iid, created_ms=1, messages=seeded))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        post = asyncio.create_task(
            client.post(
                f"/a/rca/items/{iid}/chats/{conv.resource_id}/messages",
                json={"content": "接下來呢"},
            )
        )
        await asyncio.wait_for(runner.started.wait(), 5)

        # Asked rather than assumed: the DEFAULT chat keys on the item id, not on
        # its own, so a Stop aimed at `conv.resource_id` would quietly land on a
        # session nobody is using and the test would report the defect it is
        # meant to catch (`engine_key`, manual §3).
        key = app.state.chat_send._locator.engine_key(iid, conv.resource_id)
        await app.state.turn_engine.cancel_current(key)

        # Bounded: unreachable, the summariser runs its full 30s, so a lost Stop
        # is a timeout rather than a test that waits it out.
        async def let_go() -> None:
            while not runner.cancelled:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(let_go(), 5)

        # What the POST does AFTERWARDS is P1's subject, not this one's: the send
        # runs on to the end and the epoch stamp stands the turn down. Asserting
        # both here would make one failure look like the other.
        post.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await post

    assert runner.cancelled
    after = rm.get(conv.resource_id).data
    assert isinstance(after, Conversation)
    assert "summary" not in [m.role for m in after.messages], (
        "a cancelled summariser must leave the thread exactly as it found it"
    )


class _HangsThenRuns:
    """The first turn hangs — it is the one a Stop is aimed at. Later turns
    finish, so a message queued behind it can be seen to run."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.seen.append(prompt)
        if len(self.seen) == 1:
            await asyncio.sleep(30)
        yield RunDone()


async def test_a_stop_does_not_discard_messages_already_queued_behind_it():
    """Stop ends the RUNNING turn. It has never meant "and throw away what is
    waiting" — `cancel_current`'s own docstring says queued messages are
    untouched, and `chat_routes` promises the same to the person pressing it.

    Keying on "was anything preparing when Stop landed" rather than on a shared
    clock is what keeps that true: a message that reached the queue is no longer
    preparing, so a later Stop cannot reach back and drop it. On a shared item
    the alternative is one person's Stop silently discarding another's question;
    on your own it is the follow-up you typed while the answer streamed.
    """
    runner = _HangsThenRuns()
    engine = ChatTurnEngine(runner, turn_control=InMemoryTurnControl())
    key = "inv"
    try:
        first = engine.preparing(key)
        engine.enqueue(key, "M1", AgentToolContext(), on_complete=lambda _: None, pending=first)
        while not runner.seen:  # M1 is running
            await asyncio.sleep(0.01)

        second = engine.preparing(key)
        queued = engine.enqueue(
            key, "M2", AgentToolContext(), on_complete=lambda _: None, pending=second
        )

        await engine.cancel_current(key)  # aimed at M1

        # Bounded: a discarded M2 never resolves its future, so the regression
        # is a timeout rather than a test that waits it out.
        await asyncio.wait_for(queued, 3)
        assert runner.seen == ["M1", "M2"]
    finally:
        await engine.forget(key)


async def test_a_turn_declined_before_it_started_still_records_that_it_was_cancelled():
    """A turn that never starts must still END — on the record, not only live.

    `agentLog` derives "a reply is on its way" from the persisted thread, and
    says why that is sound: a turn always ends in SOMETHING persisted, so the
    waiting state cannot stick. Declining to start without writing anything
    broke that: on reload — or for a viewer who never saw the live event — the
    thread showed a question with no answer and no explanation, and the composer
    waited half an hour for a reply nobody was going to send.
    """
    runner = _QuickRunner()
    engine = ChatTurnEngine(runner, turn_control=InMemoryTurnControl())
    key = "inv"
    persisted: list[list[TurnMessage]] = []
    try:
        pending = engine.preparing(key)
        await engine.cancel_current(key)
        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=persisted.append, pending=pending
        )

        await asyncio.wait_for(fut, 2)
        assert not runner.started
        assert [m.error_kind for batch in persisted for m in batch] == ["cancelled"]
    finally:
        await engine.forget(key)


async def test_forget_also_cancels_work_that_was_still_preparing():
    """Deleting a chat tears down its turn; the preparation behind it is part of
    that. `forget` cancelled the worker and the in-flight turn but not the
    `preparing` set, so a summariser kept running against a conversation the
    route deletes on the very next line, and then wrote to it.
    """
    engine = ChatTurnEngine(_QuickRunner(), turn_control=InMemoryTurnControl())
    key = "inv"
    started = asyncio.Event()
    outcome: list[str] = []

    async def slow_preparation() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            outcome.append("cancelled")
            raise
        outcome.append("finished")  # pragma: no cover — only if forget misses it

    running = asyncio.create_task(engine.run_interruptible(key, slow_preparation()))
    await asyncio.wait_for(started.wait(), 2)

    await engine.forget(key)

    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(running, 2)
    assert outcome == ["cancelled"]


async def test_a_stop_does_not_discard_a_queued_message_through_the_real_send_path():
    """The same invariant through `POST /messages`, because that is the path that
    had it wrong: the engine-level guard that was supposed to cover this
    (`test_turn_queue`'s cancel test) calls `enqueue` WITHOUT a token — the
    calling convention production no longer uses — so it went on passing while
    the real path dropped messages.

    Two sends, the first still running, then a Stop. The second must still be
    answered: `cancel_current` promises exactly that, and on a shared item the
    alternative is one person's Stop discarding another's question.
    """
    from httpx import ASGITransport

    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import AsyncClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _HangsThenRuns()
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    url = f"/a/rca/items/{iid}/messages"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        first = asyncio.create_task(client.post(url, json={"content": "slow"}))
        while not runner.seen:  # the first turn is running
            await asyncio.sleep(0.01)
        second = asyncio.create_task(client.post(url, json={"content": "fast"}))
        # Let it get all the way into the queue before the Stop lands.
        while len(app.state.turn_engine._ws_session(iid).queue._queue) == 0:  # noqa: SLF001
            await asyncio.sleep(0.01)

        await app.state.turn_engine.cancel_current(iid)

        # Bounded: a discarded message never runs, so the regression is a
        # timeout rather than a test that waits it out.
        async def answered() -> None:
            while "fast" not in runner.seen:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(answered(), 5)

        for task in (first, second):
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


async def test_a_declined_turn_tells_the_live_stream_too():
    """The marker on the record is for a viewer who reloads; this is for the one
    watching right now.

    `useChatSession` holds `stopping` until a TERMINAL event arrives. Without
    this publish the composer would sit disabled until the store poll noticed —
    seconds at best, and on a thread whose last message is the user's, the poll
    reads "a reply is on its way" and waits half an hour.
    """
    engine = ChatTurnEngine(_QuickRunner(), turn_control=InMemoryTurnControl())
    key = "inv"
    seen: list[str] = []
    sub = engine.subscribe(key)

    async def collect() -> None:
        async for ev in sub:
            seen.append(type(ev).__name__)
            if type(ev).__name__ == "RunCancelled":
                return

    collector = asyncio.create_task(collect())
    try:
        pending = engine.preparing(key)
        await engine.cancel_current(key)
        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=lambda _: None, pending=pending
        )
        await asyncio.wait_for(fut, 2)

        # Bounded: without the publish nothing ever arrives, so a missing
        # terminal is a timeout rather than a test that waits it out.
        await asyncio.wait_for(collector, 2)
        assert "RunCancelled" in seen
    finally:
        collector.cancel()
        with contextlib.suppress(BaseException):
            await collector
        await engine.forget(key)


async def test_a_stop_during_compaction_leaves_the_rest_of_the_send_alone():
    """Cancelling the summariser must not take the send down with it.

    `run_interruptible` cancels a task it owns, so the `CancelledError` arrives
    in `compact` as that task's RESULT — this coroutine was never cancelled and
    must carry on. Letting it propagate would abort the preparation still to
    come, and that preparation is the thing P1 established must be allowed to
    finish: it is not atomic, and a cold sandbox restore abandoned halfway
    leaves a sandbox nothing on the read path can tell is incomplete.

    Observable end to end: the POST answers 202, and the turn that preparation
    was building is DECLINED rather than never reaching the queue at all — which
    is what leaves the marker behind.
    """
    from httpx import ASGITransport

    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import AsyncClient
    from .conftest import register_rca_item

    class _HangingCompactor:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            self.started.set()
            await asyncio.sleep(30)
            yield RunDone()  # pragma: no cover — only if the Stop is lost

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _HangingCompactor()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "alice",
        context_limit=6_000,
    )
    rm = spec.get_resource_manager(Conversation)
    seeded = [Message(role="user", content=f"很久以前的第{i}個問題" * 40) for i in range(12)]
    conv = rm.create(Conversation(item_id=iid, created_ms=1, messages=seeded))
    key = app.state.chat_send._locator.engine_key(iid, conv.resource_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        post = asyncio.create_task(
            client.post(
                f"/a/rca/items/{iid}/chats/{conv.resource_id}/messages",
                json={"content": "接下來呢"},
            )
        )
        await asyncio.wait_for(runner.started.wait(), 5)
        await app.state.turn_engine.cancel_current(key)

        # Bounded: if the cancellation escaped `compact`, the send dies and this
        # never returns 202.
        resp = await asyncio.wait_for(post, 10)

    assert resp.status_code == 202
    after = rm.get(conv.resource_id).data
    assert isinstance(after, Conversation)
    assert "summary" not in [m.role for m in after.messages], (
        "a cancelled summariser must leave the thread exactly as it found it"
    )
    assert [m.error_kind for m in after.messages if m.role == "error"] == ["cancelled"], (
        "the declined turn must still say, on the record, that it was stopped"
    )


async def test_a_stopped_manual_compaction_says_stopped_not_failed():
    """The one place the outcome is visible to a person: `POST …/compact`.

    Reporting a Stop as `failed` is what the wording exists to prevent — nothing
    went wrong, they stopped it, and a control that calls your own press a
    failure is one you stop trusting. Nothing held that: changing `stopped` back
    to `failed` left every test green, and the composer then showed the fallback
    「這段對話還沒有需要壓縮的內容」 — a stopped compaction reporting that there
    was nothing to compact, over a thread full of history.
    """
    from httpx import ASGITransport

    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import AsyncClient
    from .conftest import register_rca_item

    class _HangingCompactor:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            self.started.set()
            await asyncio.sleep(30)
            yield RunDone()  # pragma: no cover — only if the Stop is lost

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _HangingCompactor()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "alice",
        context_limit=6_000,
    )
    rm = spec.get_resource_manager(Conversation)
    seeded = [Message(role="user", content=f"很久以前的第{i}個問題" * 40) for i in range(12)]
    conv = rm.create(Conversation(item_id=iid, created_ms=1, messages=seeded))
    key = app.state.chat_send._locator.engine_key(iid, conv.resource_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        asking = asyncio.create_task(
            client.post(f"/a/rca/items/{iid}/chats/{conv.resource_id}/compact")
        )
        await asyncio.wait_for(runner.started.wait(), 5)
        await app.state.turn_engine.cancel_current(key)
        resp = await asyncio.wait_for(asking, 10)

    assert resp.status_code == 200
    assert resp.json() == {"compacted": False, "reason": "stopped"}
