"""Stop must reach the turn it is aimed at — and nothing else.

Three separate ways it did not, kept together because each was found by the one
before it failing to hold: the #349 epoch stamp had a race (below); stamping an
epoch per message let a Stop stand down every message stamped before it,
including ones already queued; and a send that never reached the queue at all —
stopped, or simply failing mid-preparation — left the thread with a question and
no ending.

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
from collections.abc import AsyncIterator, Iterator

import pytest
from specstar import QB

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import MessageDelta, RunDone
from workspace_app.api.events import AgentEvent
from workspace_app.api.turns import ChatTurnEngine, TurnMessage
from workspace_app.kb.llm import ILlm
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
        # The message is persisted; the send owns the window from here.
        async with engine.preparing(key) as pending:
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
        async with engine.preparing(key) as pending:  # a NEW message, after it
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
        # runs on to the end and the pending token stands the turn down. Asserting
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
        async with engine.preparing(key) as first:
            engine.enqueue(key, "M1", AgentToolContext(), on_complete=lambda _: None, pending=first)
        while not runner.seen:  # M1 is running
            await asyncio.sleep(0.01)

        async with engine.preparing(key) as second:
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
        async with engine.preparing(key) as pending:
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
        async with engine.preparing(key) as pending:
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


async def test_preparation_that_fails_still_ends_the_thread():
    """The window between persisting the message and queueing the turn has to
    have an owner, and the failure path is where it shows.

    `_send` persists the user's message and then does two hundred lines of I/O
    to build a turn — the sandbox acquire, tool discovery, skills, the context
    block. Any of it can raise. When it did, the message was in the thread and
    nothing else was: no turn, no marker, nothing to end it. `agentLog` reads "a
    reply is on its way" off exactly that shape and waits half an hour.

    Which is the defect the declined-turn path was written to remove, alive on
    the sibling path — the half nobody had looked at.
    """
    from httpx import ASGITransport

    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import AsyncClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
    )

    async def explode(*a: object, **k: object) -> None:
        raise RuntimeError("the sandbox would not wake")

    app.state.chat_send._turn_ctx.build_chat_turn = explode

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})

    # 202, because the message WAS accepted — it is in the thread. A preparation
    # that fails afterwards is reported the way a turn's own failure is, on the
    # stream and in the thread, and not by failing the request. That is what lets
    # a non-2xx from this endpoint mean "nothing was written" and nothing else.
    assert resp.status_code == 202
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    kinds = [m.error_kind for m in conv.messages if m.role == "error"]
    assert kinds, "a send that died mid-preparation left the thread with no ending"


async def test_a_preparation_that_fails_does_not_leak_its_token():
    """`preparing` is paired with a release, so a send that never reaches
    `enqueue` does not leave its token behind.

    The set is keyed to the engine key, and a default chat's key is the item id —
    it lives as long as the item does. `run_interruptible` right above already
    had the `finally`; the asymmetry was the tell.
    """
    engine = ChatTurnEngine(_QuickRunner(), turn_control=InMemoryTurnControl())
    key = "inv"
    try:
        for _ in range(5):
            with contextlib.suppress(RuntimeError):
                async with engine.preparing(key):
                    raise RuntimeError("preparation failed")
        assert engine._ws_session(key).pending_turns == set()
    finally:
        await engine.forget(key)


async def test_one_persons_stop_does_not_decline_another_persons_preparation():
    """The collateral this whole mechanism exists to stop, in the one window it
    survived in.

    Stop reaches what is preparing — but a Stop is pressed BY somebody, and it
    means "not the thing I asked for". Marking every send in preparation put
    Alice's Stop back on Bob's question: narrower than before (seconds, not the
    whole queue) but the same wrong. An unattributed Stop still marks
    everything, which is the old behaviour for callers that cannot say who.
    """
    engine = ChatTurnEngine(_QuickRunner(), turn_control=InMemoryTurnControl())
    key = "inv"
    try:
        async with (
            engine.preparing(key, author="alice") as mine,
            engine.preparing(key, author="bob") as theirs,
        ):
            await engine.cancel_current(key, by="alice")
            assert mine.cancelled
            assert not theirs.cancelled, "Bob's question was not Alice's to stop"
    finally:
        await engine.forget(key)


async def test_forget_declines_a_turn_that_was_still_being_prepared():
    """Deleting a chat must not leave a send about to start a turn for it.

    `forget` cancelled the preparing TASKS and, separately, had to mark the
    pending sends — a rule that could be deleted with every test still green
    until this one. The route deletes the conversation on the next line.
    """
    runner = _QuickRunner()
    engine = ChatTurnEngine(runner, turn_control=InMemoryTurnControl())
    key = "inv"
    async with engine.preparing(key) as pending:
        await engine.forget(key)
        fut = engine.enqueue(
            key, "go", AgentToolContext(), on_complete=lambda _: None, pending=pending
        )
        await asyncio.wait_for(fut, 2)
    assert not runner.started
    await engine.forget(key)


async def test_a_failed_preparation_tells_the_live_stream_too():
    """Written on the record AND said out loud, like every other ending.

    The declined-turn path has both halves and a test for each. This one had the
    persisted row tested and the live event not — so it could be deleted with
    every suite green, which is the third time in this branch that a rule was
    claimed as checked-by-deletion without being checked.
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
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
    )

    async def explode(*a: object, **k: object) -> None:
        raise RuntimeError("the sandbox would not wake")

    app.state.chat_send._turn_ctx.build_chat_turn = explode
    seen: list[str] = []
    sub = app.state.turn_engine.subscribe(iid)

    async def collect() -> None:
        async for ev in sub:
            seen.append(type(ev).__name__)
            if type(ev).__name__ == "RunError":
                return

    collector = asyncio.create_task(collect())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})
        # Asserted, not suppressed: the sibling case below was updated to check
        # this and this one was not, so it could no longer tell a 202 from the
        # 500 it used to get — the difference the whole change is about.
        assert resp.status_code == 202
        await asyncio.wait_for(collector, 3)
        assert "RunError" in seen
    finally:
        collector.cancel()
        with contextlib.suppress(BaseException):
            await collector


async def test_a_declined_turn_that_cannot_be_recorded_says_so():
    """The one ending that must never be silent is the one that failed to write.

    If persisting the marker throws, the person is back in the state this whole
    path exists to prevent — a question with no ending — so saying nothing is the
    worst available option. `_run_turn` has said so in a comment for a long time;
    the declined path swallowed it.
    """
    engine = ChatTurnEngine(_QuickRunner(), turn_control=InMemoryTurnControl())
    key = "inv"
    seen: list[str] = []
    sub = engine.subscribe(key)

    def refuse(_messages: list[TurnMessage]) -> None:
        raise RuntimeError("the store said no")

    async def collect() -> None:
        async for ev in sub:
            seen.append(type(ev).__name__)
            if type(ev).__name__ == "RunCancelled":
                return

    collector = asyncio.create_task(collect())
    try:
        async with engine.preparing(key) as pending:
            await engine.cancel_current(key)
            fut = engine.enqueue(key, "go", AgentToolContext(), on_complete=refuse, pending=pending)
        await asyncio.wait_for(fut, 2)
        await asyncio.wait_for(collector, 2)
        assert "RunError" in seen, "a marker that could not be written must still be announced"
    finally:
        collector.cancel()
        with contextlib.suppress(BaseException):
            await collector
        await engine.forget(key)


async def test_anyones_stop_reaches_a_round_the_system_is_driving():
    """Scoping a Stop to its presser is right for a person's own question and
    wrong for a round nobody asked for.

    A goal follow-up and an off-hours round are stamped with whoever SET the
    goal, so attributing them made a bystander's Stop miss an agent that is
    running on a shared item — which #43 says anyone may stop. Driver rounds are
    left unattributed, so they behave as they did before Stop learned who
    pressed it.
    """
    engine = ChatTurnEngine(_QuickRunner(), turn_control=InMemoryTurnControl())
    key = "inv"
    try:
        async with (
            engine.preparing(key, author="") as driver_round,
            engine.preparing(key, author="alice") as alices_own,
        ):
            await engine.cancel_current(key, by="bob")
            assert driver_round.cancelled, "a bystander must be able to stop the agent"
            assert not alices_own.cancelled, "…without stopping Alice's own question"
    finally:
        await engine.forget(key)


async def test_a_driver_round_is_registered_unattributed():
    """The `chat_send` half of the same rule, which had no test of its own.

    The engine-level case pins what `cancel_current` does with an unattributed
    token. This pins the thing that decides a round IS unattributed: a goal
    follow-up and an off-hours round carry the goal-setter as their author, and
    passing that through would put them behind that one person's Stop on an item
    where #43 says anyone may stop the agent.
    """
    from workspace_app.api import create_app
    from workspace_app.api.schemas import _MessageBody
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.resources.conversation_goal import GOAL_DRIVER
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
    )
    engine = app.state.turn_engine
    service = app.state.chat_send
    seen: list[str] = []
    real = engine.preparing

    def spy(key: str, author: str = ""):  # noqa: ANN202 — passthrough
        seen.append(author)
        return real(key, author=author)

    engine.preparing = spy
    rid, conv = service._locator.conversation_for(iid)

    await service.send(iid, rid, conv, iid, _MessageBody(content="a person asks"), author="alice")
    await service.send(
        iid,
        rid,
        conv,
        iid,
        _MessageBody(content="the goal asks"),
        author="alice",
        driven_by=GOAL_DRIVER,
    )

    assert seen == ["alice", ""], "a driver's round belongs to nobody, a person's to them"


async def test_a_drivers_failed_round_still_raises_so_the_sweeper_can_retry():
    """Two callers, two things they can act on.

    A person's send answers 202 once the message is written, and a failure after
    that is reported on the thread and the stream. A DRIVER has no status to
    read: `OffHoursGoalSweeper.tick` releases its per-stretch claim on this
    exception so a later tick retries — "must not cost that chat its night" — and
    a cold sandbox wake is exactly what fails at the top of a stretch, when the
    item has been idle all evening. Swallowing it told the sweeper the round had
    started, so the claim was held until morning with no turn ever run.
    """
    from workspace_app.api import create_app
    from workspace_app.api.schemas import _MessageBody
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.resources.conversation_goal import GOAL_DRIVER
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
    )
    service = app.state.chat_send

    async def explode(*a: object, **k: object) -> None:
        raise RuntimeError("the sandbox would not wake")

    service._turn_ctx.build_chat_turn = explode
    rid, conv = service._locator.conversation_for(iid)

    # A person's send is accepted — the message is in the thread either way.
    await service.send(iid, rid, conv, iid, _MessageBody(content="a person asks"))

    # The driver's is not: it has to know, or it cannot give the night back.
    with pytest.raises(RuntimeError):
        await service.send(
            iid,
            rid,
            conv,
            iid,
            _MessageBody(content="the goal asks"),
            driven_by=GOAL_DRIVER,
        )


async def test_an_offhours_round_that_never_started_is_not_charged_for():
    """The budget is a promise about how much unattended work a goal may do. A
    round that never reached the model did none of it.

    The bump happens BEFORE the send on purpose — a crash must not forget a spent
    round and let the budget restart every night — but a raise is not a crash: it
    is the send saying, in as many words, that this round did not start. Without
    giving it back, the sweeper's own retry becomes the meter: it releases the
    claim and ticks again a minute later, so a sandbox that will not wake spends a
    goal's WHOLE allowance in half an hour, runs nothing, leaves the goal `active`
    so no hand-over, marker or bell ever fires, and drops it out of `_eligible`
    for good.
    """
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, make_spec
    from workspace_app.resources.conversation_goal import ConversationGoal, read_goal, upsert_goal
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
    )
    service = app.state.chat_send
    rid, _conv = service._locator.conversation_for(iid)
    upsert_goal(
        spec,
        ConversationGoal(conversation_id=rid, condition="ship it", set_by="alice", offhours=True),
        user="alice",
    )

    async def explode(*a: object, **k: object) -> None:
        raise RuntimeError("the sandbox would not wake")

    service._turn_ctx.build_chat_turn = explode

    before = read_goal(spec, rid)
    assert before is not None
    with pytest.raises(RuntimeError):
        await service.start_offhours_round(rid)

    after = read_goal(spec, rid)
    assert after is not None
    assert after.offhours_rounds_used == before.offhours_rounds_used, (
        "a round that never started must not be charged to the night's budget"
    )
    # …and the sweeper still learns it failed, so it can give the claim back.
    assert isinstance(spec.get_resource_manager(Conversation).get(rid).data, Conversation)


async def test_a_night_that_never_starts_parks_the_goal_instead_of_retrying_forever():
    """Giving a failed round back removed the only thing that ever ended such a
    night, so the round has to be given back AND counted as no progress.

    Before this, a goal whose sandbox would not wake spent its whole allowance in
    half an hour and then stopped for good; after the refund alone it stopped
    spending anything, which meant the sweeper retried it every minute, forever,
    writing two messages into its owner's thread each time. The budget was never
    the bound anyone wanted — `stall_count` is, and it is the same judgement
    (`_STALL_LIMIT` consecutive no-progress rounds park the goal for a person)
    that a night which runs but gets nowhere already goes through.
    """
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.resources.conversation_goal import ConversationGoal, read_goal, upsert_goal
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
    )
    service = app.state.chat_send
    rid, _conv = service._locator.conversation_for(iid)
    upsert_goal(
        spec,
        ConversationGoal(conversation_id=rid, condition="ship it", set_by="alice", offhours=True),
        user="alice",
    )

    async def explode(*a: object, **k: object) -> None:
        raise RuntimeError("the sandbox would not wake")

    service._turn_ctx.build_chat_turn = explode

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await service.start_offhours_round(rid)

    parked = read_goal(spec, rid)
    assert parked is not None
    assert parked.offhours_rounds_used == 0, "still not charged for rounds that never ran"
    assert parked.state == "stalled", (
        "a night that cannot start has to end for a person, not tick forever"
    )
    # And the person hears about it — the sweeper runs when nobody is watching,
    # so a marker nobody is there to read is not a hand-over.
    assert _bells(spec), "the goal's owner is told their overnight run stopped"


def _bells(spec) -> list:  # noqa: ANN001
    from specstar import QB

    from workspace_app.resources.notification import Notification

    rm = spec.get_resource_manager(Notification)
    return [
        r.data for r in rm.list_resources(QB.all()) if getattr(r.data, "kind", "") == "agent_done"
    ]


async def test_a_continuation_that_never_started_is_not_charged_either():
    """The same bump-before-send lives in `_goal_followup`, one round later.

    It is the smaller half — the chain stops rather than looping, because that
    exception is swallowed — but the round is still charged for work that never
    reached the model, and a budget only means anything if it counts turns that
    happened."""
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.resources.conversation_goal import ConversationGoal, read_goal, upsert_goal
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    class _NeverMet(ILlm):
        """Says the goal is unmet, so the chain reaches the continuation send."""

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield ("NOT_MET", False)

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_QuickRunner(),
        get_user_id=lambda: "alice",
        goal_checker_llm=_NeverMet(),
    )
    service = app.state.chat_send
    rid, _conv = service._locator.conversation_for(iid)
    upsert_goal(
        spec,
        ConversationGoal(conversation_id=rid, condition="ship it", set_by="alice"),
        user="alice",
    )
    engine_key = service._locator.engine_key(iid, rid)

    async def explode(*a: object, **k: object) -> None:
        raise RuntimeError("the sandbox would not wake")

    service._turn_ctx.build_chat_turn = explode

    before = read_goal(spec, rid)
    assert before is not None
    await service._goal_followup(iid, rid, engine_key, "alice", "ok")

    after = read_goal(spec, rid)
    assert after is not None
    assert after.rounds_used == before.rounds_used, (
        "a continuation that never reached the model is not a round of work"
    )
