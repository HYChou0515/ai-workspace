"""A second KB-chat message QUEUES behind the running turn; it does not cancel it.

The KB chat used to run its turns through `ChatTurnEngine.stream()`, whose first
act is `_cancel_prior_turn` — so asking a follow-up while an answer was streaming
threw that answer away. The composer hid this by refusing to send at all, which
is how the gesture came to produce no reaction whatsoever: no bubble, no cleared
box, nothing. Both halves are wrong, and this file pins the backend half: the
turns serialize, exactly as the workspace chat's have since #43.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from httpx import ASGITransport

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone, ToolEnd, ToolStart
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient


class _GatedRunner:
    """The first turn parks until it is released, so the second message arrives
    while it is genuinely still running — the only arrangement in which queueing
    and cancelling look different."""

    def __init__(self) -> None:
        self.started = False
        self.release = False
        self.finished: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        first = prompt.strip().endswith("a")
        if first:
            self.started = True
            # Bounded, so a turn that is never released FAILS the test instead of
            # hanging it — a hang is the one failure that reports nothing.
            for _ in range(400):
                if self.release:
                    break
                await asyncio.sleep(0.01)
        # Yielded AFTER the wait on purpose: a cancelled turn never gets here, so
        # the answer's presence is the evidence that it ran to completion.
        yield MessageDelta(text=prompt.strip()[-1])
        yield RunDone()
        self.finished.append(prompt.strip()[-1])


def _app(runner: object):
    return create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )


async def test_a_second_message_queues_behind_the_running_kb_turn() -> None:
    runner = _GatedRunner()
    app = _app(runner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={"title": "t", "collection_ids": []})).json()[
            "resource_id"
        ]

        async def send(text: str):
            return await c.post(f"/kb/chats/{cid}/messages", json={"content": text})

        first = asyncio.create_task(send("a"))
        for _ in range(400):  # bounded wait for the first turn to be underway
            if runner.started:
                break
            await asyncio.sleep(0.01)
        assert runner.started, "the first turn never started"

        second = asyncio.create_task(send("b"))
        await asyncio.sleep(0.05)  # let the second request reach the route
        runner.release = True
        await asyncio.wait_for(asyncio.gather(first, second), 10)

        msgs = (await c.get(f"/kb/chats/{cid}")).json()["messages"]

    answers = [m["content"] for m in msgs if m["role"] == "assistant"]
    # Both answered, in order. Under the old `stream()` the first turn was
    # cancelled before it reached its delta, so this read ["b"].
    assert answers == ["a", "b"], f"expected both turns to run, got {answers}"
    assert runner.finished == ["a", "b"], "the turns did not serialize"


async def test_both_questions_are_kept_when_one_queues_behind_the_other() -> None:
    """The queued question is persisted too — the thread is the record of what was
    asked, and a question that vanishes is what made this look like a dead app."""
    runner = _GatedRunner()
    app = _app(runner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={"title": "t", "collection_ids": []})).json()[
            "resource_id"
        ]

        async def send(text: str):
            return await c.post(f"/kb/chats/{cid}/messages", json={"content": text})

        first = asyncio.create_task(send("a"))
        for _ in range(400):
            if runner.started:
                break
            await asyncio.sleep(0.01)
        second = asyncio.create_task(send("b"))
        await asyncio.sleep(0.05)
        runner.release = True
        await asyncio.wait_for(asyncio.gather(first, second), 10)

        msgs = (await c.get(f"/kb/chats/{cid}")).json()["messages"]

    assert [m["content"] for m in msgs if m["role"] == "user"] == ["a", "b"]


class _ToolRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="Let me check. ")
        yield ToolStart(call_id="t1", name="kb_search", args={"query": "reflow"})
        yield ToolEnd(call_id="t1", output="[1] reflow.md: zone three drift")
        yield MessageDelta(text="Zone three drifted [1].")
        yield RunDone()


async def test_the_chat_broadcast_carries_the_question_then_the_tool_and_the_answer() -> None:
    """#4 Part B, moved here from `test_kb_chat_api` along with the rule it tests.

    The intermediate events used to come back in the POST's body. They now ride
    the chat's own broadcast — which is the whole point, because a body that
    stays open until the answer is finished cannot also let the next message
    queue behind it.

    Read off the ENGINE, not over HTTP: `ASGITransport` cannot read an infinite
    response incrementally (see `test_file_broadcast`, which says so and does the
    same), so an HTTP read of a live SSE endpoint hangs rather than fails. The
    endpoint is a `subscribe_sse` wrapper over exactly this; its own coverage is
    the gate test below."""
    app = _app(_ToolRunner())
    _, kb_engine = app.state.turn_engines

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={"collection_ids": ["c"]})).json()["resource_id"]

        # Subscribed BEFORE the send, exactly as a viewer's stream is.
        sub = kb_engine.subscribe(cid)
        seen: list[str] = []

        async def collect() -> None:
            async for ev in sub:
                seen.append(type(ev).__name__)
                if type(ev).__name__ == "RunDone":
                    return

        collector = asyncio.create_task(collect())
        r = await c.post(f"/kb/chats/{cid}/messages", json={"content": "why voids?"})
        assert r.status_code == 202
        # Bounded: a broadcast that never arrives must FAIL, not hang.
        await asyncio.wait_for(collector, 10)

    # The question first — that is what lets the sender's own optimistic bubble be
    # adopted rather than drawn a second time — then the work, then the answer.
    assert seen[0] == "UserMessage", seen
    assert "ToolStart" in seen and "ToolEnd" in seen, seen
    assert seen.index("ToolStart") < len(seen) - 1 - seen[::-1].index("MessageDelta"), seen


async def test_the_stream_endpoint_refuses_a_chat_you_cannot_read() -> None:
    """The gate on the new endpoint, checked on the response that TERMINATES —
    a refusal is a finite body, so this says something about the route without
    trying to read an endless one."""
    app = _app(_ToolRunner())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/kb/chats/does-not-exist/stream")
    assert r.status_code == 404
