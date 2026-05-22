"""Idle-kill behavior — plan-backend §3.3.

The lifespan-driven background task wakes every `idle_check_interval`
and reaps sandboxes whose `last_active` is past `idle_timeout`.
Shutdown cancels the reaper and runs `registry.close_all` to release
anything still alive.

Tests parameterize the timings down to fractions of a second so the
assertions stay fast.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


class _CountingSandbox(MockSandbox):
    """MockSandbox with create/kill counters so the idle-kill loop's
    observable effect (a sandbox actually got killed) is testable."""

    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.kill_calls = 0

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        self.create_calls += 1
        return await super().create(spec)

    async def kill(self, handle: SandboxHandle) -> None:
        self.kill_calls += 1
        await super().kill(handle)


class _ExecRunner:
    """Runs one exec via the tool context so a sandbox actually exists
    on the session, then closes. Cheap, deterministic, no LLM."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        # ensure_sandbox creates the handle (or reuses the registry's)
        await ctx.ensure_sandbox()
        yield RunDone()


def _make_components(*, idle_timeout: timedelta, idle_check_interval: timedelta):
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    sandbox = _CountingSandbox()
    filestore = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=_ExecRunner(),
        idle_timeout=idle_timeout,
        idle_check_interval=idle_check_interval,
    )
    return app, sandbox


@asynccontextmanager
async def _running_app(app):
    """ASGITransport alone doesn't dispatch lifespan events — wrap with
    LifespanManager so startup/shutdown actually fire."""
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://t") as client,
    ):
        yield client


async def test_idle_killer_reaps_session_past_threshold():
    """End-to-end: POST creates a session+sandbox. After idle_timeout
    elapses with no further activity, the next sweep kills it."""
    app, sandbox = _make_components(
        idle_timeout=timedelta(seconds=0.1),
        idle_check_interval=timedelta(seconds=0.05),
    )
    async with _running_app(app) as client:
        resp = await client.post("/workspaces/ws/messages", json={"content": "x"})
        assert resp.status_code == 200
        assert sandbox.create_calls == 1
        # Wait long enough for idle threshold + at least one sweep.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sandbox.kill_calls >= 1:
                break
    assert sandbox.kill_calls == 1


async def test_active_session_within_threshold_is_not_reaped():
    app, sandbox = _make_components(
        idle_timeout=timedelta(seconds=2),
        idle_check_interval=timedelta(seconds=0.05),
    )
    async with _running_app(app) as client:
        await client.post("/workspaces/ws/messages", json={"content": "x"})
        # Sweep happens but nothing's idle yet.
        await asyncio.sleep(0.2)
    # Lifespan-shutdown's close_all will count as a kill — so we only
    # check that the count is 1 (from shutdown), not >1 (which would
    # indicate the idle-kill loop fired before shutdown).
    assert sandbox.kill_calls == 1


async def test_shutdown_close_all_kills_alive_sessions():
    """When the app's lifespan exits, the idle-killer is cancelled and
    registry.close_all() releases anything still in-flight."""
    app, sandbox = _make_components(
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
    )
    async with _running_app(app) as client:
        await client.post("/workspaces/ws-a/messages", json={"content": "a"})
        await client.post("/workspaces/ws-b/messages", json={"content": "b"})
        assert sandbox.create_calls == 2
        assert sandbox.kill_calls == 0  # nothing reaped yet
    # Lifespan exit happens here.
    assert sandbox.kill_calls == 2


async def test_default_idle_timeout_matches_grill_me_decision():
    """Default knob is 15 min to match Q10/b1, not some arbitrary value."""
    import inspect

    sig = inspect.signature(create_app)
    default = sig.parameters["idle_timeout"].default
    assert default == timedelta(minutes=15)
