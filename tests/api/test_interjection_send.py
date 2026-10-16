"""docs/plan-interjection.md P1 through the real send paths.

The reported sequence: Q1 is still being answered when Q2 is sent. Q2's turn
must see A1 — the thread as it stands when Q2's turn STARTS — not the [Q1] the
thread held when Q2 was accepted."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport

from workspace_app.agent import AgentToolContext
from workspace_app.api import AgentEvent, MessageDelta, RunDone

from ._client import AsyncClient


class _FirstTurnWaits:
    """Answers every prompt with `A(<prompt>)`; the first turn waits for
    `release`, so a second message can be sent while it runs."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.turns: list[dict[str, Any]] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.turns.append(
            {
                "prompt": prompt,
                "history": [h.get("content") for h in ctx.history],
                "leading": [i.content for i in ctx.leading_interjections],
            }
        )
        if len(self.turns) == 1:
            await self.release.wait()
        yield MessageDelta(text=f"A({prompt})")
        yield RunDone()


async def _until(cond, timeout: float = 5) -> None:
    async def loop() -> None:
        while not cond():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(loop(), timeout)


async def test_a_message_sent_while_the_answer_streams_sees_that_answer():
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _FirstTurnWaits()
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    url = f"/a/rca/items/{iid}/messages"
    engine = app.state.turn_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        first = asyncio.create_task(client.post(url, json={"content": "Q1"}))
        await _until(lambda: len(runner.turns) == 1)
        second = asyncio.create_task(client.post(url, json={"content": "Q2"}))
        await _until(lambda: not engine._ws_session(iid).queue.empty())  # noqa: SLF001
        runner.release.set()
        await _until(lambda: len(runner.turns) == 2)
        for task in (first, second):
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, 5)

    # Q2's turn was handed the thread with A1 in it, and Q2 itself as the turn.
    assert runner.turns[1]["prompt"].endswith("Q2")
    assert runner.turns[1]["history"][-2:] == ["[u]: Q1", "A(Q1)"]


async def test_two_messages_queued_behind_an_answer_are_answered_together():
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _FirstTurnWaits()
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    url = f"/a/rca/items/{iid}/messages"
    engine = app.state.turn_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        first = asyncio.create_task(client.post(url, json={"content": "Q1"}))
        await _until(lambda: len(runner.turns) == 1)
        second = asyncio.create_task(client.post(url, json={"content": "Q2"}))
        await _until(lambda: not engine._ws_session(iid).queue.empty())  # noqa: SLF001
        third = asyncio.create_task(client.post(url, json={"content": "Q3"}))
        await _until(lambda: len(engine._ws_session(iid).queue._items) == 2)  # noqa: SLF001
        runner.release.set()
        for task in (first, second, third):
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, 5)
    # One turn answered Q2 and Q3: Q2 leads it, after a history that holds A1.
    assert len(runner.turns) == 2
    assert runner.turns[1]["prompt"].endswith("Q3")
    assert [c.endswith("Q2") for c in runner.turns[1]["leading"]] == [True]
    assert runner.turns[1]["history"][-2:] == ["[u]: Q1", "A(Q1)"]
    # The thread reads in the order the AI read it: Q2 and Q3 were persisted
    # above A1 when they were accepted, and moved to where that turn read them.
    thread = [(m.role, m.content) for m in _conversation(spec, iid).messages]
    assert thread == [
        ("user", "Q1"),
        ("assistant", "A(Q1)"),
        ("user", "Q2"),
        ("user", "Q3"),
        ("assistant", "A(Q3)"),
    ]
    # No claim is left open: the folded message was settled by the reply.
    assert app.state.turn_claims.list_open() == []


class _ReadsAtTheBoundary:
    """The first turn runs a tool, waits for `release`, then — as the runner
    does before its next model call — reads its author's messages and answers."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.prompts: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        from workspace_app.api import Interjected, ToolEnd, ToolStart

        self.prompts.append(prompt)
        yield ToolStart(call_id="c1", name="look", args={})
        yield ToolEnd(call_id="c1", output="lot 1")
        if len(self.prompts) == 1:
            await self.release.wait()
        assert ctx.take_interjections is not None
        read = ctx.take_interjections()
        for i in read:
            yield Interjected(author=i.author, created_at=i.at)
        yield MessageDelta(text="answer to " + " + ".join([prompt, *(i.content for i in read)]))
        yield RunDone()


async def test_a_message_sent_mid_turn_by_its_author_is_answered_in_that_turn():
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = _ReadsAtTheBoundary()
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    url = f"/a/rca/items/{iid}/messages"
    engine = app.state.turn_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        first = asyncio.create_task(client.post(url, json={"content": "Q1"}))
        await _until(lambda: len(runner.prompts) == 1)
        second = asyncio.create_task(client.post(url, json={"content": "Q2"}))
        await _until(lambda: not engine._ws_session(iid).queue.empty())  # noqa: SLF001
        runner.release.set()
        for task in (first, second):
            await asyncio.wait_for(task, 5)

    # One turn: Q2 got no turn of its own.
    assert len(runner.prompts) == 1
    # Persisted where it was read — after the tool result, before the answer.
    thread = [(m.role, m.content) for m in _conversation(spec, iid).messages]
    assert thread == [
        ("user", "Q1"),
        ("tool", "lot 1"),
        ("user", "Q2"),
        ("assistant", "answer to Q1 + Q2"),
    ]
    assert app.state.turn_claims.list_open() == []


def _conversation(spec, iid: str):
    from specstar import QB

    from workspace_app.resources import Conversation

    for r in spec.get_resource_manager(Conversation).list_resources(QB.all()):
        if isinstance(r.data, Conversation) and r.data.item_id == iid:
            return r.data
    raise AssertionError("no conversation for item")
