"""docs/plan-interjection.md P1: a queued message builds its history when its
turn STARTS, and every queued message no turn has seen is answered by ONE turn
(Q10) — never one turn per message, each blind to the answers before it.

The reported shape: Q1 is running, Q2 is sent. Q2's context used to be fixed at
acceptance, when the thread held only [Q1], so Q2's turn never saw A1 and
answered Q1 again; Q3's turn then answered Q2. These tests drive the engine the
way both chat surfaces do (`enqueue` with a `QueuedMessage`)."""

from __future__ import annotations

import asyncio
from typing import Any

from workspace_app.agent import AgentToolContext
from workspace_app.api import Interjected, MessageDelta, RunDone, ToolEnd, ToolStart
from workspace_app.api.turns import (
    PLACE_ROLE,
    ChatTurnEngine,
    Interjection,
    PendingTurn,
    QueuedMessage,
)


class _GatedRunner:
    """Runs each turn to completion, except the first, which waits for `release`
    so the test can queue messages behind a RUNNING turn. Records what each turn
    was handed: its prompt, its history, and the queued messages folded in
    ahead of the prompt."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.turns: list[dict[str, Any]] = []

    async def run(self, content: str, ctx: AgentToolContext):
        self.turns.append(
            {
                "content": content,
                "history": list(ctx.history),
                "leading": list(ctx.leading_interjections),
            }
        )
        if len(self.turns) == 1:
            self.started.set()
            await self.release.wait()
        yield MessageDelta(text=f"answer to {content}")
        yield RunDone()


def _message(
    author: str, at: int, log: list[Any], *, place: bool = True, **kw: Any
) -> QueuedMessage:
    """A chat message as a surface hands it over. Its `refresh` records what it
    was asked to leave out and says whether the answered messages must move
    (`place`: a real surface says so when something now sits after them)."""

    async def refresh(answering: list[Any], pending: set[Any], ctx: AgentToolContext) -> bool:
        log.append(("refresh", author, at, [a for a, _ in answering]))
        log.append(("pending", at, sorted(a for a, _ in pending)))
        ctx.history = [
            {"role": "assistant", "content": f"thread without {[a for a, _ in answering]}"}
        ]
        return place

    def on_absorbed() -> None:
        log.append(("absorbed", author, at))

    return QueuedMessage(author=author, at=at, refresh=refresh, on_absorbed=on_absorbed, **kw)


async def _drain(engine: ChatTurnEngine, *futs: asyncio.Future[None]) -> None:
    await asyncio.wait_for(asyncio.gather(*futs), 3)
    await engine.forget("chat")


async def test_a_message_queued_behind_a_running_turn_rebuilds_its_history_when_it_starts():
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    persisted: list[str] = []
    replies: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            persisted.append(name)
            replies[name] = [
                (m.role, m.created_at if m.role == PLACE_ROLE else None) for m in produced
            ]

        return on_complete

    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(history=[{"role": "user", "content": "old"}]),
        on_complete=keep("Q1"),
        message=_message("ann", 1, log, place=False),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    # Accepted while Q1 runs: its history, built now, can only hold [Q1].
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(history=[{"role": "user", "content": "Q1"}]),
        on_complete=keep("Q2"),
        message=_message("ann", 2, log),
    )
    runner.release.set()
    await _drain(engine, f1, f2)

    # Every chat message is answered with the thread as it stands when its turn
    # starts — Q1 too (nothing changed for it, which its surface says by not
    # asking to move it).
    assert runner.turns[0]["history"] == [{"role": "assistant", "content": "thread without [1]"}]
    # Q2's history was rebuilt at its start — from the thread as it stands
    # after A1 persisted — not the [Q1] it was accepted with.
    assert runner.turns[1]["history"] == [{"role": "assistant", "content": "thread without [2]"}]
    assert ("refresh", "ann", 2, [2]) in log
    assert persisted == ["Q1", "Q2"]
    # Q2 was persisted above A1; its reply opens by putting it back where it
    # was read. Q1 waited for nothing and is where it was.
    assert replies["Q1"] == [("assistant", None)]
    assert replies["Q2"] == [(PLACE_ROLE, 2), ("assistant", None)]


async def test_queued_messages_no_turn_has_seen_are_answered_by_one_turn():
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    persisted: list[str] = []

    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: persisted.append("Q1"),
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    q2_ctx = AgentToolContext(turn_image_urls=["data:image/png;base64,AAA"])
    f2 = engine.enqueue(
        "chat",
        "Q2",
        q2_ctx,
        on_complete=lambda p: persisted.append("Q2"),
        message=_message("ann", 2, log, speaker="Ann (ann)"),
    )
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: persisted.append("Q3"),
        message=_message("ann", 3, log),
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)

    # Two turns, not three: Q2 and Q3 were both unseen when Q1 ended.
    assert [t["content"] for t in runner.turns] == ["Q1", "Q3"]
    # The last one is the turn; the earlier one leads it, in order, carrying
    # what was attached to it.
    assert runner.turns[1]["leading"] == [
        Interjection(
            content="Q2",
            author="ann",
            at=2,
            speaker="Ann (ann)",
            image_urls=["data:image/png;base64,AAA"],
        )
    ]
    # History is rebuilt from before the FIRST message the turn answers.
    assert ("refresh", "ann", 3, [2, 3]) in log
    assert runner.turns[1]["history"] == [{"role": "assistant", "content": "thread without [2, 3]"}]
    # One reply, persisted through the turn's own message; the folded one is
    # settled only once that reply is in the store.
    assert persisted == ["Q1", "Q3"]
    assert log[-1] == ("absorbed", "ann", 2)


async def test_a_folded_message_is_not_settled_when_the_reply_was_not_this_pods():
    """A handed-over turn (`on_complete` → False) wrote nothing: the messages it
    folded in are still owed, so their claims must stay open for a peer."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []

    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=lambda p: None, message=_message("a", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat", "Q2", AgentToolContext(), on_complete=lambda p: None, message=_message("a", 2, log)
    )
    f3 = engine.enqueue(
        "chat", "Q3", AgentToolContext(), on_complete=lambda p: False, message=_message("a", 3, log)
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)

    assert [t["content"] for t in runner.turns] == ["Q1", "Q3"]
    assert not any(e[0] == "absorbed" for e in log)


async def test_a_message_stopped_while_preparing_is_not_folded_into_the_next_turn():
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    persisted: list[list[Any]] = []

    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: persisted.append(p),
        message=_message("a", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    stopped = PendingTurn(author="a")
    stopped.cancelled = True
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: persisted.append(p),
        pending=stopped,
        message=_message("a", 2, log),
    )
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: persisted.append(p),
        message=_message("a", 3, log),
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)

    # Q3 is the turn and nothing leads it; Q2 ended as a declined turn does.
    assert [t["content"] for t in runner.turns] == ["Q1", "Q3"]
    assert runner.turns[1]["leading"] == []
    assert any(len(p) == 1 and p[0].error_kind == "cancelled" for p in persisted)


async def test_a_refresh_that_fails_keeps_the_history_the_message_was_accepted_with():
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]

    async def broken(answering: list[Any], pending: set[Any], ctx: AgentToolContext) -> bool:
        raise RuntimeError("store down")

    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=lambda p: None, message=QueuedMessage("a", 1)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(history=[{"role": "user", "content": "Q1"}]),
        on_complete=lambda p: None,
        message=QueuedMessage("a", 2, refresh=broken),
    )
    runner.release.set()
    await _drain(engine, f1, f2)

    assert runner.turns[1]["history"] == [{"role": "user", "content": "Q1"}]


class _ReadsMidTurn:
    """Stands in for the runner's model-call boundary: runs one tool, waits for
    `release`, then asks for its author's messages (as the SDK filter does),
    says where it read each, and answers."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.turns: list[str] = []
        self.read: list[Interjection] = []

    async def run(self, content: str, ctx: AgentToolContext):
        self.turns.append(content)
        yield ToolStart(call_id="c1", name="look", args={})
        yield ToolEnd(call_id="c1", output="lot 1")
        if len(self.turns) == 1:
            self.started.set()
            await self.release.wait()
        taken = ctx.take_interjections() if ctx.take_interjections is not None else []
        self.read.extend(taken)
        for i in taken:
            yield Interjected(author=i.author, created_at=i.at)
        yield MessageDelta(text="answer")
        yield RunDone()


async def test_the_running_turn_reads_its_authors_message_where_it_is_sent():
    runner = _ReadsMidTurn()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    replies: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            replies[name] = [
                (m.role, m.created_at if m.role == PLACE_ROLE else m.content) for m in produced
            ]

        return on_complete

    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=keep("Q1"),
        message=_message("ann", 1, log, place=False),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(applied_skills=["lot-map"]),
        on_complete=keep("Q2"),
        message=_message("ann", 2, log, speaker="Ann (ann)"),
    )
    runner.release.set()
    await _drain(engine, f1, f2)

    # Ann's message was read by her running turn, with what it carried, and
    # got no turn of its own.
    assert runner.read == [
        Interjection(
            content="Q2", author="ann", at=2, speaker="Ann (ann)", applied_skills=["lot-map"]
        )
    ]
    assert runner.turns == ["Q1"]
    # One reply for Q1 and Q2, with Q2 placed where it was read: after the
    # tool result, before the answer that saw it.
    assert replies["Q1"] == [("tool", "lot 1"), (PLACE_ROLE, 2), ("assistant", "answer")]
    assert "Q2" not in replies
    # Settled by that reply, not before it.
    assert ("absorbed", "ann", 2) in log


async def test_the_reader_never_takes_another_persons_message():
    """A running turn reads as one person. Another person's message normally
    stops that turn on arrival (Q2), but the stop lands at the turn's next
    await — so the reader itself must still leave such a message alone."""
    from workspace_app.api.turns import _QueueEntry, _TurnStamp, _WorkspaceSession

    session = _WorkspaceSession()
    loop = asyncio.get_running_loop()

    def entry(author: str, at: int) -> _QueueEntry:
        return _QueueEntry(
            content=f"Q{at}",
            ctx=AgentToolContext(),
            on_complete=lambda p: None,
            on_turn_end=None,
            fut=loop.create_future(),
            pending=None,
            stamp=_TurnStamp("chat", at),
            message=QueuedMessage(author, at),
        )

    bob, ann = entry("bob", 2), entry("ann", 3)
    session.queue.put_nowait(bob)
    session.queue.put_nowait(ann)
    folded: list[Any] = []

    taken = ChatTurnEngine._reader(session, "ann", folded)()  # noqa: SLF001

    assert [i.content for i in taken] == ["Q3"]
    assert folded == [ann]
    assert session.queue.take(lambda e: True) == [bob]


async def test_a_turn_queued_with_no_message_reads_nothing():
    runner = _ReadsMidTurn()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    f1 = engine.enqueue("chat", "s1", AgentToolContext(), on_complete=lambda p: None)
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat", "Q2", AgentToolContext(), on_complete=lambda p: None, message=QueuedMessage("a", 2)
    )
    runner.release.set()
    await _drain(engine, f1, f2)
    assert runner.read == []
    assert runner.turns == ["s1", "Q2"]


class _RunsUntilStopped:
    """Ann's turn runs a tool and then keeps going until it is cancelled; every
    later turn answers at once. Records each turn's prompt and leading messages."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.turns: list[dict[str, Any]] = []

    async def run(self, content: str, ctx: AgentToolContext):
        self.turns.append(
            {"content": content, "leading": [i.content for i in ctx.leading_interjections]}
        )
        if len(self.turns) == 1:
            yield ToolStart(call_id="c1", name="look", args={})
            yield ToolEnd(call_id="c1", output="lot 1")
            self.started.set()
            await asyncio.sleep(30)
        yield MessageDelta(text=f"answer to {content}")
        yield RunDone()


async def test_another_persons_message_stops_the_running_turn_and_takes_over():
    """plan-interjection Q2: a turn runs as one person. Bob's message is not read
    into Ann's turn — it stops it, and Bob's turn takes over with the thread as
    it stands (Ann's partial work in it). The ending names who interrupted."""
    runner = _RunsUntilStopped()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    replies: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            replies[name] = [(m.role, m.error_kind, m.author) for m in produced]

        return on_complete

    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=keep("Q1"),
        message=_message("ann", 1, log, place=False),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat", "Q2", AgentToolContext(), on_complete=keep("Q2"), message=_message("bob", 2, log)
    )
    await _drain(engine, f1, f2)

    # Ann's turn ended at once (not after its 30s), with its tool result kept and
    # an ending that says Bob's message interrupted it.
    assert replies["Q1"] == [("tool", None, None), ("error", "interrupted", "bob")]
    # Bob's turn ran — rebuilt from the thread as it stands, since it waited.
    assert [t["content"] for t in runner.turns] == ["Q1", "Q2"]
    assert ("refresh", "bob", 2, [2]) in log


async def test_the_interrupted_persons_unread_message_goes_to_the_turn_that_took_over():
    """plan-interjection Q10: Ann's own follow-up, still waiting for her turn's
    next model call when Bob interrupted, was read by no turn — so Bob's turn,
    the next one, answers it too (as Bob: it only READS Ann's words)."""
    runner = _RunsUntilStopped()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 2, log),
    )
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("bob", 3, log),
    )
    await _drain(engine, f1, f2, f3)

    assert runner.turns[1:] == [{"content": "Q3", "leading": ["Q2"]}]


async def test_a_message_with_no_author_does_not_stop_anything():
    """A turn queued without a message (a workflow step) is not a person
    speaking: it waits its turn, as before."""
    runner = _RunsUntilStopped()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    engine.enqueue("chat", "step", AgentToolContext(), on_complete=lambda p: None)
    await asyncio.sleep(0.1)
    assert not f1.done()
    await engine.forget("chat")


async def test_entries_without_a_message_keep_one_turn_each():
    """Workflow steps (and any caller that names no message) are not chat
    messages: each is its own turn, as before."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    f1 = engine.enqueue("chat", "s1", AgentToolContext(), on_complete=lambda p: None)
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue("chat", "s2", AgentToolContext(), on_complete=lambda p: None)
    f3 = engine.enqueue("chat", "s3", AgentToolContext(), on_complete=lambda p: None)
    runner.release.set()
    await _drain(engine, f1, f2, f3)
    assert [t["content"] for t in runner.turns] == ["s1", "s2", "s3"]


# ── review round 1 (docs/plan-interjection.md §9 C8–C12) ─────────────────────


async def test_a_rebuilt_history_leaves_out_every_message_no_turn_has_answered_yet():
    """F1/R3: a message persisted but still preparing (or waiting elsewhere) is
    not part of anyone's answered thread. A rebuilt history that kept it showed
    the model a question nobody had answered, in the wrong place."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 2, log),
    )
    # Q3 is persisted and still preparing (a cold sandbox, compaction…).
    async with engine.preparing("chat", author="cat", at=3):
        runner.release.set()
        await asyncio.wait_for(f2, 3)
    await _drain(engine, f1)
    assert ("pending", 2, [3]) in log


async def test_a_goal_round_never_takes_over_or_is_read_into_a_persons_turn():
    """R1: the goal driver's round is not a person speaking. Queued behind
    Bob's turn it waits its turn — it does not stop Bob, and it is not read into
    a turn of the goal's own setter either."""
    runner = _ReadsMidTurn()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    ended: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            ended[name] = [(m.role, m.error_kind) for m in produced if m.role == "error"]

        return on_complete

    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=keep("bob"), message=_message("bob", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "[goal] continue",
        AgentToolContext(),
        on_complete=keep("goal"),
        message=_message("alice", 2, log, driven=True),
    )
    runner.release.set()
    await _drain(engine, f1, f2)
    assert ended["bob"] == []
    assert runner.turns == ["Q1", "[goal] continue"]
    assert runner.read == []


async def test_a_message_stopped_while_preparing_takes_nothing_over():
    """R4: a send already stopped during its preparation will be declined — it
    must not stop someone else's running turn on its way to being declined."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    ended: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            ended[name] = [(m.role, m.error_kind) for m in produced if m.role == "error"]

        return on_complete

    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=keep("bob"), message=_message("bob", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    stopped = PendingTurn(author="carol")
    stopped.cancelled = True
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=keep("carol"),
        pending=stopped,
        message=_message("carol", 2, log),
    )
    await asyncio.sleep(0.05)
    runner.release.set()
    await _drain(engine, f1, f2)
    assert ended["bob"] == []
    assert ended["carol"] == [("error", "cancelled")]


class _ReadsThenWorks:
    """Reads its author's messages once they are queued, then keeps working
    (until stopped); every later turn answers at once."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.queued = asyncio.Event()
        self.read = asyncio.Event()
        self.turns: list[dict[str, Any]] = []

    async def run(self, content: str, ctx: AgentToolContext):
        self.turns.append(
            {"content": content, "leading": [i.content for i in ctx.leading_interjections]}
        )
        if len(self.turns) == 1:
            self.started.set()
            await self.queued.wait()
            assert ctx.take_interjections is not None
            for i in ctx.take_interjections():
                yield Interjected(author=i.author, created_at=i.at)
            self.read.set()
            await asyncio.sleep(30)
        yield MessageDelta(text=f"answer to {content}")
        yield RunDone()


async def test_a_message_the_stopped_turn_had_read_is_still_answered():
    """R2: Stop ends the answer that was being written — it has never meant
    "and drop what is waiting" (`cancel_current`). A message the stopped turn
    had READ was not answered by it, so it goes back to the queue and gets the
    next turn, and its claim stays owed until then."""
    runner = _ReadsThenWorks()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 2, log),
    )
    runner.queued.set()
    await asyncio.wait_for(runner.read.wait(), 3)
    await engine.cancel_current("chat", by="ann")
    await _drain(engine, f1, f2)
    assert [t["content"] for t in runner.turns] == ["Q1", "Q2"]
    assert ("absorbed", "ann", 2) not in log


async def test_another_persons_message_takes_over_what_the_stopped_turn_had_read():
    """R2 with Q2/Q10: Bob's message stops Ann's turn after it read Ann's
    follow-up. That follow-up was not answered — Bob's turn answers it too."""
    runner = _ReadsThenWorks()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 2, log),
    )
    runner.queued.set()
    await asyncio.wait_for(runner.read.wait(), 3)
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("bob", 3, log),
    )
    await _drain(engine, f1, f2, f3)
    assert runner.turns[1:] == [{"content": "Q3", "leading": ["Q2"]}]


async def test_a_takeover_while_the_next_turn_is_still_preparing_answers_it_as_the_newcomer():
    """F3: Ann's queued message is about to start (its history being rebuilt)
    when Bob sends. Nothing has run for Ann yet, so there is nothing to stop:
    her message waits for the next turn, which is Bob's, and answers both."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    rebuilding = asyncio.Event()
    go_on = asyncio.Event()

    async def slow(answering: list[Any], pending: set[Any], ctx: AgentToolContext) -> bool:
        rebuilding.set()
        await go_on.wait()
        return True

    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=QueuedMessage("ann", 2, refresh=slow),
    )
    runner.release.set()
    await asyncio.wait_for(rebuilding.wait(), 3)
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("bob", 3, log),
    )
    go_on.set()
    await _drain(engine, f1, f2, f3)
    assert [(t["content"], [i.content for i in t["leading"]]) for t in runner.turns] == [
        ("Q1", []),
        ("Q3", ["Q2"]),
    ]


async def test_a_takeover_before_the_turns_first_step_still_ends_it_on_the_record():
    """F3: the turn's task exists but has not run a single step when Bob's
    message lands. Cancelling it there skipped its body — and with it the
    ending every turn must persist. It ends as interrupted, like any other."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    ended: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            ended[name] = [(m.role, m.error_kind, m.author) for m in produced if m.role == "error"]

        return on_complete

    rebuilt = asyncio.Event()

    async def signals(answering: list[Any], pending: set[Any], ctx: AgentToolContext) -> bool:
        rebuilt.set()
        return True

    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=keep("a1"), message=_message("ann", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=keep("a2"),
        message=QueuedMessage("ann", 2, refresh=signals),
    )

    async def bob() -> asyncio.Future[None]:
        await rebuilt.wait()
        # Ready in the same loop pass the worker creates Ann's turn task.
        return engine.enqueue(
            "chat", "Q3", AgentToolContext(), on_complete=keep("b"), message=_message("bob", 3, log)
        )

    pending_bob = asyncio.create_task(bob())
    runner.release.set()
    f3 = await asyncio.wait_for(pending_bob, 3)
    await _drain(engine, f1, f2, f3)
    assert "a2" in ended, "Ann's second turn was cancelled before it began and wrote nothing"


async def test_a_drain_does_not_restart_what_the_turn_it_cancelled_had_folded():
    """§9 C13 (review round 2, F1): past its deadline a drain cancels the
    running turn — that is not a Stop. Treated as one, the messages the turn
    had folded went back to the queue and the worker started them as a new turn
    on the pod that was shutting down, still running after `aclose` returned
    while a peer re-ran the same question. The drain hands them over instead:
    nothing restarts here, and every waiting POST is woken."""
    from workspace_app.turn_control.memory import InMemoryTurnControl

    class _SlowControl(InMemoryTurnControl):
        async def current(self, key: str) -> int:  # a store round trip, as in prod
            await asyncio.sleep(0.02)
            return await super().current(key)

    class _Blocks:
        def __init__(self) -> None:
            self.turns: list[str] = []
            self.started = asyncio.Event()

        async def run(self, content: str, ctx: AgentToolContext):
            self.turns.append(content)
            self.started.set()
            await asyncio.sleep(30)
            yield RunDone()

    runner = _Blocks()
    engine = ChatTurnEngine(runner, turn_control=_SlowControl())  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 2, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    await engine.aclose(timeout=0.1)
    await asyncio.sleep(0.3)  # anything restarted would have begun by now
    assert runner.turns == ["Q2"]
    assert f1.done() and f2.done()
    assert ("absorbed", "ann", 1) not in log  # handed over, still owed


async def test_a_stop_before_the_turns_first_step_still_ends_it_on_the_record():
    """§9 C11, the same window for Stop: pressed in the loop pass the turn's
    task is created, a bare `cancel()` skipped the turn's body — no ending, no
    broadcast, the thread left on an unanswered question."""

    class _SecondTurnWorks(_GatedRunner):
        async def run(self, content: str, ctx: AgentToolContext):
            if self.turns:  # the second turn: still working when Stop lands
                self.turns.append({"content": content})
                await asyncio.sleep(30)
            async for ev in super().run(content, ctx):
                yield ev

    runner = _SecondTurnWorks()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    ended: dict[str, list[Any]] = {}

    def keep(name: str):
        def on_complete(produced: list[Any]) -> None:
            ended[name] = [(m.role, m.error_kind) for m in produced if m.role == "error"]

        return on_complete

    rebuilt = asyncio.Event()

    async def signals(answering: list[Any], pending: set[Any], ctx: AgentToolContext) -> bool:
        rebuilt.set()
        return True

    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=keep("a1"), message=_message("ann", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=keep("a2"),
        message=QueuedMessage("ann", 2, refresh=signals),
    )

    async def stop() -> None:
        await rebuilt.wait()
        await engine.cancel_current("chat", by="ann")

    stopping = asyncio.create_task(stop())
    runner.release.set()
    await asyncio.wait_for(stopping, 3)
    await _drain(engine, f1, f2)
    assert ended.get("a2") == [("error", "cancelled")]


# ── review round 2, veracity: the claims nothing pinned ──────────────────────


async def test_a_goal_round_is_never_read_into_its_own_setters_turn():
    """§9 C10: the goal driver's round is stamped with the setter, so it IS a
    candidate for the setter's running turn by author — and still must not be
    read into it: it is not the setter speaking."""
    runner = _ReadsMidTurn()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "[goal] continue",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("ann", 2, log, driven=True),
    )
    runner.release.set()
    await _drain(engine, f1, f2)
    assert runner.read == []
    assert runner.turns == ["Q1", "[goal] continue"]


async def test_a_goal_round_and_a_persons_message_are_not_folded_together():
    """§9 C10: queued behind a turn, a goal round and a person's message each
    keep their own turn — the round is not the person speaking, and the person
    is not the round's."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("bob", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("bob", 2, log),
    )
    f3 = engine.enqueue(
        "chat",
        "[goal] continue",
        AgentToolContext(),
        on_complete=lambda p: None,
        message=_message("alice", 3, log, driven=True),
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)
    assert [(t["content"], [i.content for i in t["leading"]]) for t in runner.turns] == [
        ("Q1", []),
        ("Q2", []),
        ("[goal] continue", []),
    ]


async def test_a_send_stopped_while_preparing_is_not_folded_from_behind_the_head():
    """A stopped send sitting BEHIND the message the worker reaches is not
    folded into that turn: it is declined on its own (the existing test had it
    at the head, where it is declined before any fold)."""
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=lambda p: None, message=_message("a", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat", "Q2", AgentToolContext(), on_complete=lambda p: None, message=_message("a", 2, log)
    )
    stopped = PendingTurn(author="a")
    stopped.cancelled = True
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: None,
        pending=stopped,
        message=_message("a", 3, log),
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)
    assert [(t["content"], [i.content for i in t["leading"]]) for t in runner.turns] == [
        ("Q1", []),
        ("Q2", []),
    ]


async def test_a_folded_messages_skill_is_readable_in_the_turn_that_answers_it():
    """§9 Q7: a skill applied with a message folded into another turn is
    readable there (its body is already in the message's content)."""
    seen: list[list[str]] = []

    class _Records(_GatedRunner):
        async def run(self, content: str, ctx: AgentToolContext):
            seen.append(list(ctx.applied_skills))
            async for ev in super().run(content, ctx):
                yield ev

    runner = _Records()
    engine = ChatTurnEngine(runner)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat", "Q1", AgentToolContext(), on_complete=lambda p: None, message=_message("a", 1, log)
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(applied_skills=["lot-map"]),
        on_complete=lambda p: None,
        message=_message("a", 2, log),
    )
    f3 = engine.enqueue(
        "chat", "Q3", AgentToolContext(), on_complete=lambda p: None, message=_message("a", 3, log)
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)
    assert seen[-1] == ["lot-map"]


async def test_a_turn_answering_folded_messages_is_recorded_as_accepted_with_the_earliest():
    """§9: the capacity record's wait (accepted → started) runs from the
    earliest message the turn answers, not the latest."""
    from workspace_app.capacity.recorder import UsageRecorder
    from workspace_app.capacity.sink import UsageSink

    class _ListSink(UsageSink):
        def __init__(self) -> None:
            self.docs: list[dict] = []

        def write(self, documents):  # noqa: ANN001, ANN202
            self.docs.extend(documents)

    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="p", window_s=300, queue_max=1000)
    runner = _GatedRunner()
    engine = ChatTurnEngine(runner, usage_recorder=recorder)  # ty: ignore[invalid-argument-type]
    log: list[Any] = []
    f1 = engine.enqueue(
        "chat",
        "Q1",
        AgentToolContext(),
        on_complete=lambda p: None,
        accepted_ms=1_000,
        message=_message("a", 1, log),
    )
    await asyncio.wait_for(runner.started.wait(), 3)
    f2 = engine.enqueue(
        "chat",
        "Q2",
        AgentToolContext(),
        on_complete=lambda p: None,
        accepted_ms=2_000,
        message=_message("a", 2, log),
    )
    f3 = engine.enqueue(
        "chat",
        "Q3",
        AgentToolContext(),
        on_complete=lambda p: None,
        accepted_ms=3_000,
        message=_message("a", 3, log),
    )
    runner.release.set()
    await _drain(engine, f1, f2, f3)
    recorder.flush()
    accepted = sorted(d["usage"]["accepted_ms"] for d in sink.docs if d["usage"]["kind"] == "turn")
    assert accepted == [1_000, 2_000]


def test_a_message_read_mid_turn_is_placed_by_its_author_too():
    """§9 C14 (veracity round 2): the marker an `Interjected` leaves carries the
    author, so placing it cannot move another person's same-millisecond
    message."""
    from workspace_app.api.turns import _TurnReducer

    reducer = _TurnReducer()
    reducer.add(Interjected(author="ann", created_at=5))
    (marker,) = reducer.produced
    assert (marker.role, marker.created_at, marker.author) == (PLACE_ROLE, 5, "ann")
