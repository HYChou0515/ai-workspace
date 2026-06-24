"""KB chat interrupt — same contract as the RCA workspace turn (plan §3.2):
a new message cancels the in-flight one, DELETE cancels it (204 even when
idle), and the cancelled stream surfaces RunCancelled before closing."""

from __future__ import annotations

import asyncio
import json
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


async def test_post_then_delete_cancels_and_stream_carries_run_cancelled():
    runner = _BlockingRunner()
    app = _app(runner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]
        post = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "go"}))
        await asyncio.wait_for(runner.first_yielded.wait(), timeout=2.0)

        d = await c.delete(f"/kb/chats/{cid}/messages/current")
        assert d.status_code == 204

        runner.release.set()  # let the cancelled turn unwind
        resp = await asyncio.wait_for(post, timeout=2.0)
        assert resp.status_code == 200
        assert "run_cancelled" in resp.text

        # A second DELETE after the turn is done is a no-op 204 (prev.done()).
        d2 = await c.delete(f"/kb/chats/{cid}/messages/current")
        assert d2.status_code == 204


async def test_second_message_cancels_the_first():
    runner = _BlockingRunner()
    app = _app(runner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]
        first = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "one"}))
        await asyncio.wait_for(runner.first_yielded.wait(), timeout=2.0)

        # Reset gates so turn-2 doesn't share turn-1's blocked release.
        runner.first_yielded = asyncio.Event()
        runner.release = asyncio.Event()
        runner.release.set()  # turn-2 runs to completion immediately

        second = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "two"}))
        second_resp = await asyncio.wait_for(second, timeout=2.0)
        first_resp = await asyncio.wait_for(first, timeout=2.0)

    assert second_resp.status_code == 200
    assert first_resp.status_code == 200
    assert "run_cancelled" in first_resp.text  # turn-1 was interrupted

    events = [
        json.loads(line[len("data:") :].strip())
        for line in second_resp.text.split("\n\n")
        if line.startswith("data:")
    ]
    assert events[-1]["type"] == "done"  # turn-2 finished cleanly
    assert all(e["type"] != "run_cancelled" for e in events)
