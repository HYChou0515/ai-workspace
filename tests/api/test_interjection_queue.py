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
from workspace_app.api import MessageDelta, RunDone
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


def _message(author: str, at: int, log: list[Any], **kw: Any) -> QueuedMessage:
    async def refresh(answering: list[int], ctx: AgentToolContext) -> None:
        log.append(("refresh", author, at, answering))
        ctx.history = [{"role": "assistant", "content": f"thread without {answering}"}]

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
        message=_message("ann", 1, log),
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

    # Q1 ran with the history it was built with: nothing was waiting before it.
    assert runner.turns[0]["history"] == [{"role": "user", "content": "old"}]
    # Q2's history was rebuilt at its start — from the thread as it stands
    # after A1 persisted — not the [Q1] it was accepted with.
    assert runner.turns[1]["history"] == [{"role": "assistant", "content": "thread without [2]"}]
    assert ("refresh", "ann", 2, [2]) in log
    assert not any(e[0] == "refresh" and e[2] == 1 for e in log)
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

    async def broken(answering: list[int], ctx: AgentToolContext) -> None:
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
