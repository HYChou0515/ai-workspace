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
from datetime import timedelta

from asgi_lifespan import LifespanManager
from httpx import ASGITransport

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

from ._client import AsyncClient
from .conftest import register_rca_item


class _CountingSandbox(MockSandbox):
    """MockSandbox with create/kill counters so the idle-kill loop's
    observable effect (a sandbox actually got killed) is testable."""

    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.kill_calls = 0

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.create_calls += 1
        return await super().create(spec, sandbox_id)

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


class _ShellWritingRunner:
    """Wakes the sandbox and writes a file directly into it (as a shell
    command would) — bypassing the file tools, so only the mirror can
    surface it in the snapshot."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        h = await ctx.ensure_sandbox()
        assert ctx.sandbox is not None
        await ctx.sandbox.upload(h, b"shell-made", "/out.txt")
        yield RunDone()


class _BigWritingRunner:
    """Wakes the sandbox and writes an oversized file straight into it, so the
    scratch-quota sweep (#345) has an over-cap workspace to reap."""

    def __init__(self, nbytes: int) -> None:
        self._n = nbytes

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        h = await ctx.ensure_sandbox()
        assert ctx.sandbox is not None
        await ctx.sandbox.upload(h, b"x" * self._n, "/big.bin")
        yield RunDone()


def _make_components(
    *,
    idle_timeout: timedelta,
    idle_check_interval: timedelta,
    mirror_interval: timedelta = timedelta(seconds=60),
    max_workspace_bytes: int = 0,
    runner=None,
):
    spec = make_spec(default_user="u")
    sandbox = _CountingSandbox()
    filestore = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=runner or _ExecRunner(),
        idle_timeout=idle_timeout,
        idle_check_interval=idle_check_interval,
        mirror_interval=mirror_interval,
        max_workspace_bytes=max_workspace_bytes,
    )
    return app, sandbox, filestore, spec


@asynccontextmanager
async def _running_app(app):
    """ASGITransport alone doesn't dispatch lifespan events — wrap with
    LifespanManager so startup/shutdown actually fire."""
    async with (
        LifespanManager(app) as manager,
        # routes_from=app: LifespanManager wraps the app as a bare callable, so
        # route discovery for the /api auto-prefix reads the original app (#177).
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://t", routes_from=app
        ) as client,
    ):
        yield client


async def test_idle_killer_reaps_session_past_threshold():
    """End-to-end: POST creates a session+sandbox. After idle_timeout
    elapses with no further activity, the next sweep kills it."""
    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=0.1),
        idle_check_interval=timedelta(seconds=0.05),
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        resp = await client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
        assert resp.status_code == 202
        assert sandbox.create_calls == 1
        # Wait long enough for idle threshold + at least one sweep.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sandbox.kill_calls >= 1:
                break
    assert sandbox.kill_calls == 1


async def test_active_session_within_threshold_is_not_reaped():
    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=2),
        idle_check_interval=timedelta(seconds=0.05),
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        await client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
        # Sweep happens but nothing's idle yet.
        await asyncio.sleep(0.2)
    # Lifespan-shutdown's close_all will count as a kill — so we only
    # check that the count is 1 (from shutdown), not >1 (which would
    # indicate the idle-kill loop fired before shutdown).
    assert sandbox.kill_calls == 1


async def test_shutdown_close_all_kills_alive_sessions():
    """When the app's lifespan exits, the idle-killer is cancelled and
    registry.close_all() releases anything still in-flight."""
    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
    )
    iid_a = register_rca_item(spec)
    iid_b = register_rca_item(spec)
    async with _running_app(app) as client:
        await client.post(f"/a/rca/items/{iid_a}/messages", json={"content": "a"})
        await client.post(f"/a/rca/items/{iid_b}/messages", json={"content": "b"})
        assert sandbox.create_calls == 2
        assert sandbox.kill_calls == 0  # nothing reaped yet
    # Lifespan exit happens here.
    assert sandbox.kill_calls == 2


async def test_mirror_sweeper_persists_warm_sandbox_to_snapshot():
    """The throttle sweep mirrors a warm sandbox to the snapshot every
    mirror_interval — surfacing even files the shell wrote (which the file
    tools never touched) in the durable FileStore."""
    app, _sandbox, filestore, spec = _make_components(
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
        mirror_interval=timedelta(seconds=0.05),
        runner=_ShellWritingRunner(),
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        await client.post(f"/a/rca/items/{iid}/messages", json={"content": "go"})
        # the shell-written file is NOT in the snapshot yet (no mirror ran)…
        for _ in range(40):
            await asyncio.sleep(0.05)
            if await filestore.exists(iid, "/out.txt"):
                break
    # …a sweep tick mirrored it into the snapshot.
    assert await filestore.read(iid, "/out.txt") == b"shell-made"


async def test_quota_sweeper_recycles_over_quota_workspace():
    """#345: a workspace over `max_workspace_bytes` is recycled by the sweeper
    even though it is NOT idle — the only relief from scratch-vol disk pressure."""
    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=60),  # NOT idle — only the quota can reap it
        idle_check_interval=timedelta(seconds=0.05),
        max_workspace_bytes=100,
        runner=_BigWritingRunner(500),  # 500 bytes > 100-byte cap
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        resp = await client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
        assert resp.status_code == 202
        assert sandbox.create_calls == 1
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sandbox.kill_calls >= 1:
                break
    assert sandbox.kill_calls == 1  # reaped by the quota sweep despite being active


async def test_quota_sweeper_off_by_default_leaves_big_workspace():
    """With `max_workspace_bytes` 0 (default) the sweeper never measures or
    reaps — a big-but-not-idle workspace is left alone."""
    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=0.05),
        max_workspace_bytes=0,  # disabled
        runner=_BigWritingRunner(500),
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        await client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
        await asyncio.sleep(0.2)  # several sweeps
        assert sandbox.kill_calls == 0  # quota disabled → nothing reaped while running
    assert sandbox.kill_calls == 1  # only shutdown close_all reaps it


async def test_lifespan_registers_activity_model_for_local_sandbox(tmp_path):
    # #345: a LocalProcessSandbox wires the global activity store, so the lifespan
    # startup registers the per-item heartbeat model (the `registry.activity is
    # not None` boot branch). MockSandbox-backed apps skip it (activity is None).
    from workspace_app.api.sandbox_activity import _SandboxActivity
    from workspace_app.sandbox.local_process import LocalProcessSandbox

    spec = make_spec(default_user="u")
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    filestore = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
    )
    async with _running_app(app):
        # registered at boot ⇒ get_resource_manager resolves instead of raising.
        assert spec.get_resource_manager(_SandboxActivity) is not None


async def test_lifespan_registers_address_model_for_http_sandbox():
    # #366: an HttpSandbox mints per-pod uuid handles that don't converge across
    # pods, so the lifespan wires the shared per-item address store + registers
    # its model (the `registry.address is not None` boot branch). Local/mock apps
    # skip it — they already converge via the item-keyed shared dir.
    from workspace_app.api.sandbox_address import _SandboxAddress
    from workspace_app.sandbox.http_client import HttpSandbox

    spec = make_spec(default_user="u")
    sandbox = HttpSandbox(base_url="http://sandbox-host.invalid")
    filestore = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
    )
    async with _running_app(app):
        # registered at boot ⇒ get_resource_manager resolves instead of raising.
        assert spec.get_resource_manager(_SandboxAddress) is not None


async def test_lifespan_skips_address_model_for_mock_sandbox_366():
    # A non-http backend does not wire the address store, so its model stays
    # unregistered (no needless table for a backend that already converges).
    import pytest

    from workspace_app.api.sandbox_address import _SandboxAddress

    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
    )
    async with _running_app(app):
        with pytest.raises(Exception):  # noqa: B017,PT011 — unregistered ⇒ lookup fails
            spec.get_resource_manager(_SandboxAddress)


async def test_default_idle_timeout_matches_rca_pivot():
    """Default knob is 8h per the RCA pivot (was 15min for the prior
    workspace-app — RCA sessions are long-running per grill-me Q10)."""
    import inspect

    sig = inspect.signature(create_app)
    default = sig.parameters["idle_timeout"].default
    assert default == timedelta(hours=8)
