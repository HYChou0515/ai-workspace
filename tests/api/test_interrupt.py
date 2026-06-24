"""Interrupt behavior for the shared RCA workspace (#43).

The investigation turn model is now a serialized queue + broadcast (not the
per-requester cancel-prior stream): a new message QUEUES behind the in-flight
turn rather than cancelling it (covered at the engine level in
test_turn_queue.py), and Stop = DELETE .../messages/current cancels ONLY the
running turn (anyone may). Here we check the DELETE endpoint contract:
  - 204 even when nothing is in flight;
  - it cancels the in-flight turn so the awaiting POST returns and the turn is
    persisted with a cancelled marker.
The mid-flight scenario over httpx+ASGITransport is timing-sensitive, so the
cancellation reducer contract is also checked directly (no HTTP).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from httpx import ASGITransport
from specstar import QB

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunCancelled, RunDone, ToolStart, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient
from .conftest import register_rca_item


class _BlockingRunner:
    """Yields one event, signals first_yielded, then blocks on release."""

    def __init__(self) -> None:
        self.first_yielded = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield ToolStart(call_id="c1", name="exec", args={})
        self.first_yielded.set()
        await self.release.wait()
        yield RunDone()


async def test_delete_with_no_in_flight_turn_returns_204():
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_BlockingRunner(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.delete(f"/a/rca/items/{iid}/messages/current")
        assert resp.status_code == 204


async def test_delete_cancels_the_in_flight_turn():
    """Stop interrupts the running turn: the awaiting POST returns (202) and a
    cancelled marker is persisted so a reloaded thread shows the interruption."""
    runner = _BlockingRunner()
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        post_task = asyncio.create_task(
            client.post(f"/a/rca/items/{iid}/messages", json={"content": "first"})
        )
        await asyncio.wait_for(runner.first_yielded.wait(), timeout=2.0)

        d = await client.delete(f"/a/rca/items/{iid}/messages/current")
        assert d.status_code == 204

        # Cancelling unblocks the held turn (no need to release the runner).
        resp = await asyncio.wait_for(post_task, timeout=2.0)
        assert resp.status_code == 202

    conv = next(
        r.data
        for r in spec.get_resource_manager(Conversation).list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    assert any(m.role == "error" and m.error_kind == "cancelled" for m in conv.messages)


async def test_run_cancelled_reducer_contract_without_http():
    """Bypass the HTTP layer: cancelling the driver enqueues RunCancelled before
    the sentinel (the same reduction the worker uses to persist a cancelled
    turn)."""
    runner = _BlockingRunner()
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
    ctx = AgentToolContext(investigation_id="ws-direct")

    async def driver():
        try:
            async for ev in runner.run("hi", ctx):
                await queue.put(ev)
        except asyncio.CancelledError:
            await queue.put(RunCancelled())
            raise
        finally:
            await queue.put(None)

    task = asyncio.create_task(driver())
    await asyncio.wait_for(runner.first_yielded.wait(), timeout=2.0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    collected: list = []
    while not queue.empty():
        collected.append(queue.get_nowait())

    types = [type(c).__name__ for c in collected if c is not None]
    assert types == ["ToolStart", "RunCancelled"]
    assert collected[-1] is None
