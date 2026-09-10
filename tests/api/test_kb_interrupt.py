"""KB chat Stop — same contract as the workspace turn: DELETE interrupts the
turn that is RUNNING (204 even when idle), and the broadcast carries
RunCancelled.

"A new message cancels the in-flight one" was the old contract and is gone —
messages queue now (`test_kb_chat_queue`). What that changed here is which
session Stop has to reach: an enqueued turn lives in `_ws_sessions`, and only
`cancel_current` looks there. `cancel()` searches `_sessions`, which is where
`stream()` kept its turns and which the KB chat no longer creates — so Stop
found nothing to stop and fell back to the epoch watcher's poll.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from httpx import ASGITransport

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, ToolStart, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient


class _BlockingRunner:
    """Yields one event, signals first_yielded, then blocks on release."""

    def __init__(self) -> None:
        self.first_yielded = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield ToolStart(call_id="c1", name="kb_search", args={"query": "x"})
        self.first_yielded.set()
        await self.release.wait()
        yield RunDone()


def _app(runner: _BlockingRunner):
    spec = make_spec(default_user="u")
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        # current user == chat owner ("u"), so the owner-only send is allowed.
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )


async def test_delete_with_no_in_flight_turn_returns_204():
    app = _app(_BlockingRunner())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]
        r = await c.delete(f"/kb/chats/{cid}/messages/current")
        assert r.status_code == 204


async def test_stop_interrupts_the_running_turn_and_the_broadcast_says_so():
    runner = _BlockingRunner()
    app = _app(runner)
    _, kb_engine = app.state.turn_engines

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]

        seen: list[str] = []

        async def collect() -> None:
            async for ev in kb_engine.subscribe(cid):
                seen.append(type(ev).__name__)
                if type(ev).__name__ == "RunCancelled":
                    return

        collector = asyncio.create_task(collect())
        # The send awaits its own turn, so it only returns once the turn ends —
        # here, once Stop ends it.
        sending = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "hi"}))
        await asyncio.wait_for(runner.first_yielded.wait(), 5)

        started = time.perf_counter()
        d = await c.delete(f"/kb/chats/{cid}/messages/current")
        assert d.status_code == 204

        # PROMPTLY, and the number is the whole point. Both implementations
        # cancel eventually, so "RunCancelled arrives" passes either way — this
        # test was green against the unfixed code until it was given a bound.
        # Measured over five runs: 507 ms median through the epoch watcher's
        # poll (`cancel`, which searches the wrong session map), 4.9 ms when
        # Stop reaches the turn directly (`cancel_current`). 200 ms sits an
        # order of magnitude clear of both.
        await asyncio.wait_for(collector, 2)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert "RunCancelled" in seen, seen
        assert elapsed_ms < 200, f"Stop took {elapsed_ms:.0f} ms — it is waiting for a poll"

        runner.release.set()
        assert (await asyncio.wait_for(sending, 10)).status_code == 202

        # Idle again: a second Stop has nothing to interrupt and says so.
        d2 = await c.delete(f"/kb/chats/{cid}/messages/current")
        assert d2.status_code == 204


async def test_stop_leaves_a_message_that_is_only_QUEUED_alone():
    """#43's rule, inherited: Stop interrupts the running turn, not the queue.

    The queued question keeps its place — it was never the thing the user asked
    to stop, and dropping it would lose something they typed."""
    runner = _BlockingRunner()
    app = _app(runner)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]

        first = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "one"}))
        await asyncio.wait_for(runner.first_yielded.wait(), 5)
        second = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "two"}))
        await asyncio.sleep(0.05)

        assert (await c.delete(f"/kb/chats/{cid}/messages/current")).status_code == 204
        runner.release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 10)

        msgs = (await c.get(f"/kb/chats/{cid}")).json()["messages"]

    # Both questions survive the Stop.
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["one", "two"]
