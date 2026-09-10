"""#349 — create_app must inject a SHARED, specstar-backed cancel control.

The engine-level tests (test_cross_pod_cancel) prove the mechanism with two
engines over one in-memory control. This proves the WIRING: a real `create_app`
must hand its turn engines a specstar-backed `ITurnControl` (not the per-engine
in-memory default), or a Stop handled on a peer replica still can't reach a turn
running on this one. Pod B is a bare engine over the SAME spec; its Stop must
cancel pod A's live HTTP turn through the shared epoch.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from httpx import ASGITransport

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import MessageDelta, RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.api.turns import ChatTurnEngine
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.turn_control import SpecstarTurnControl

from ._client import AsyncClient


class _BlockingRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="working")
        self.started.set()
        await asyncio.sleep(30)  # hang until a peer pod's Stop cancels us
        yield RunDone()  # pragma: no cover — never reached


class _FastRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


async def test_create_app_wires_a_shared_specstar_cancel_control():
    runner = _BlockingRunner()
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        turn_cancel_poll_seconds=0.01,
    )
    # Pod B: a separate engine over the SAME spec — i.e. another replica.
    pod_b = ChatTurnEngine(
        _FastRunner(), turn_control=SpecstarTurnControl(spec), poll_interval=0.01
    )

    _, kb_engine = app.state.turn_engines

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]

        # The cancellation arrives on the chat's BROADCAST now, not in the POST's
        # body — the send queues and answers 202, so there is no stream to read it
        # off. Subscribed before the send, exactly as a viewer is.
        seen: list[str] = []

        async def collect() -> None:
            async for ev in kb_engine.subscribe(cid):
                seen.append(type(ev).__name__)
                if type(ev).__name__ == "RunCancelled":
                    return

        collector = asyncio.create_task(collect())
        post = asyncio.create_task(c.post(f"/kb/chats/{cid}/messages", json={"content": "go"}))
        await asyncio.wait_for(runner.started.wait(), 2)

        # Stop pressed; the request landed on the wrong replica. `cancel_current`
        # is what the route calls — and on a pod holding no session for this chat
        # all it can do is bump the SHARED epoch, which is precisely the wiring
        # under test.
        await pod_b.cancel_current(cid)

        # Bounded: the peer's Stop must reach this pod's turn through the epoch
        # watcher, and a wait with no deadline would turn "it never did" into a
        # hang, which reports nothing.
        await asyncio.wait_for(collector, 3)
        assert "RunCancelled" in seen, seen

        resp = await asyncio.wait_for(post, 5)
        assert resp.status_code == 202
