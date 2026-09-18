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
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
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


async def test_a_tick_inside_the_turns_window_does_not_orphan_the_sandbox(caplog, monkeypatch):
    """The window the test above falls into since #804, four for four on CI.

    A turn's session is created when its context is built (`turn_context`
    fetches it, no handle yet); the sandbox is acquired by the engine's warm
    as the turn starts (`_drive`), after the rest of the context build, the
    message persist and the enqueue — 0.26 s under coverage here. A reaper
    tick in between saw "no sandbox, timestamp old", took the "nothing to
    reap" exit and dropped the session from the table. The turn still held
    the object, put a handle on it, and from then on nothing listed that
    sandbox: not this reaper, not `close_all`. P33 (#775) closed the other
    half of this window, the acquire itself, with the session lock; this is
    the half before the lock is taken.

    Forced rather than timed: `registry.session` is held at its return — the
    session is in the table with no handle and the turn does not yet have
    it — until the reaper's own log says a tick dropped it (a run where none
    landed fails here instead of passing vacuously); then the turn goes on
    through the real chain: context build, enqueue, the engine's warm,
    `ensure_handle`. Threshold 0 so every tick sees everything as idle.
    """
    from workspace_app.api.registry import InvestigationRegistry

    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=0),
        idle_check_interval=timedelta(seconds=0.05),
    )
    iid = register_rca_item(spec)
    parked, go = asyncio.Event(), asyncio.Event()
    fetch = InvestigationRegistry.session

    async def held_at_return(self: InvestigationRegistry, inv_id: str):
        session = await fetch(self, inv_id)
        if inv_id == iid and not parked.is_set():
            parked.set()
            await go.wait()
        return session

    monkeypatch.setattr(InvestigationRegistry, "session", held_at_return)
    caplog.set_level(logging.INFO, logger="workspace_app.api.registry")
    async with _running_app(app) as client:
        post = asyncio.create_task(
            client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
        )
        await asyncio.wait_for(parked.wait(), 10)
        for _ in range(100):
            if any("no sandbox to reap" in r.getMessage() for r in caplog.records):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("no reaper tick dropped the session inside the window — nothing exercised")
        go.set()
        assert (await post).status_code == 202
        assert sandbox.create_calls == 1
        for _ in range(100):
            await asyncio.sleep(0.05)
            if sandbox.kill_calls >= 1:
                break
    assert sandbox.kill_calls == 1, "the sandbox the turn built after the drop was never reaped"


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
    # Neither the sweep nor the lifespan's `close_all` kills a sandbox the
    # fleet was using inside the threshold (same rule, plan-graceful-shutdown
    # round 1): a kill here would be the idle-kill loop firing early.
    assert sandbox.kill_calls == 0


async def test_shutdown_lets_go_of_alive_sessions_without_killing_them():
    """When the app's lifespan exits, the idle-killer is cancelled and
    `registry.close_all()` lets go of anything still in flight — written
    back and dropped from this pod, NOT killed: the sandbox is the fleet's
    (#345 shared dir, #366 shared address), a peer may be mid-turn in it
    (the turn this pod just handed over, for one), and `kill_idle`'s rule
    applies — only a sandbox no pod has touched past the idle threshold is
    torn down. Before plan-graceful-shutdown P2 this path never ran in a
    rollout; its unconditional kill was found the first time it could."""
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
    # Lifespan exit happens here: both were used seconds ago, both kept.
    assert sandbox.kill_calls == 0


async def test_shutdown_kills_only_what_the_fleet_left_idle():
    """The other half of the rule: a session whose sandbox nobody has touched
    past the threshold IS torn down at shutdown, as the sweep would have."""
    app, sandbox, _, spec = _make_components(
        idle_timeout=timedelta(seconds=0.3),
        idle_check_interval=timedelta(seconds=60),  # the sweep never fires
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        await client.post(f"/a/rca/items/{iid}/messages", json={"content": "a"})
        assert sandbox.create_calls == 1
        await asyncio.sleep(0.5)  # past the threshold, with no sweep to reap it
    assert sandbox.kill_calls == 1


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
    # pods, so the app wires the shared per-item address store (local/mock apps
    # get no STORE — they already converge via the item-keyed shared dir); the
    # MODEL is registered by `create_app` on every backend (see the next test).
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


def test_the_address_model_is_registered_whatever_the_sandbox_backend():
    """This used to assert the opposite — a non-http backend left the address
    model unregistered, "no needless table". The blob-gc worker composes
    `create_app` and never enters a lifespan, and the API's ask names every
    model the API holds, so a registry that depends on which features are on
    is one more way for asker and runner to diverge (`_check_registry` would
    refuse every pass). Registered unconditionally, at `create_app` time — a
    registered but unused coordination model costs nothing."""
    from workspace_app.api.sandbox_address import _SandboxAddress

    spec = make_spec(default_user="u")
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=60),
        idle_check_interval=timedelta(seconds=60),
    )
    assert spec.get_resource_manager(_SandboxAddress) is not None  # no lifespan needed


async def test_default_idle_timeout_matches_rca_pivot():
    """Default knob is 8h per the RCA pivot (was 15min for the prior
    workspace-app — RCA sessions are long-running per grill-me Q10)."""
    import inspect

    sig = inspect.signature(create_app)
    default = sig.parameters["idle_timeout"].default
    assert default == timedelta(hours=8)


class _SweepingSandbox(MockSandbox):
    """A backend that owns a persistent uv cache, like the local one does."""

    def __init__(self) -> None:
        super().__init__()
        self.swept: list[tuple[set[str], int | None]] = []

    def cache_keys_in_use(self) -> set[str]:
        return {"live-item"}

    def cache_keys_present(self) -> set[str]:
        # The caller needs the candidates too, so it can ask the CROSS-POD
        # heartbeat about the ones this process does not know are live.
        return {"live-item", "gone-item"}

    def sweep_uv_cache(self, *, in_use: set[str], max_bytes: int | None = None) -> list[str]:
        self.swept.append((set(in_use), max_bytes))
        return []


async def test_the_idle_tick_bounds_the_uv_caches_with_the_configured_ceiling():
    """#775: the per-item uv caches outlive their sandboxes on purpose, so
    something has to bound them — and a ceiling nothing ever reads is worse than
    none, because it reads like a limit.

    Asserted through the real entry point: the value goes in at `create_app`
    where an operator's config lands, and the assertion is that the BACKEND was
    asked, with that number and with the live items protected.
    """
    spec = make_spec(default_user="u")
    sandbox = _SweepingSandbox()
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=5),
        idle_check_interval=timedelta(seconds=0.05),
        mirror_interval=timedelta(seconds=60),
        uv_cache_max_bytes=4096,
    )
    async with _running_app(app):
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sandbox.swept:
                break

    assert sandbox.swept, "a ceiling nobody reads is worse than no ceiling"
    in_use, ceiling = sandbox.swept[-1]
    assert ceiling == 4096, "the operator's number, not a default invented on the way"
    assert in_use == {"live-item"}, "and a live item's cache is never collectable"


class _ExplodingSweepSandbox(_CountingSandbox):
    """A backend whose uv-cache sweep raises, the way a real one does when a
    peer pod removes a cache between this pod's listing and its `stat`."""

    def cache_keys_in_use(self) -> set[str]:
        return set()

    def cache_keys_present(self) -> set[str]:
        return {"whatever"}

    def __init__(self) -> None:
        super().__init__()
        self.sweep_calls = 0

    def sweep_uv_cache(self, *, in_use: set[str], max_bytes: int | None = None) -> list[str]:
        self.sweep_calls += 1
        raise FileNotFoundError("a peer pod deleted it mid-walk")


async def test_a_sweep_that_raises_does_not_stop_the_idle_reaper():
    """`idle_killer` catches only `CancelledError`, so an exception from the new
    sweep would end the loop — and with it idle reaping, turn-end write-back and
    scratch reclamation, for the pod's whole life. Review measured the shape:
    19 ticks in a second became 1.

    `kill_idle` and `mirror_warm` are per-item resilient for exactly this
    reason; the sweep that rides the same tick has to be too. A cache left
    unswept is a disk problem. A dead reaper is every problem.
    """
    spec = make_spec(default_user="u")
    sandbox = _ExplodingSweepSandbox()
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=0.1),
        idle_check_interval=timedelta(seconds=0.05),
        mirror_interval=timedelta(seconds=60),
        uv_cache_max_bytes=4096,
    )
    iid = register_rca_item(spec)
    async with _running_app(app) as client:
        resp = await client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
        assert resp.status_code == 202
        for _ in range(40):
            await asyncio.sleep(0.05)
            if sandbox.sweep_calls >= 3:
                break

    # Counting SWEEPS, not kills. `kill_idle` runs BEFORE the sweep on each
    # tick, so a kill proves only that the loop reached the raise once —
    # asserting on it passed with the guard removed, which is the same
    # can-not-fail assertion this round exists to stop writing.
    assert sandbox.sweep_calls >= 3, (
        f"the loop must survive a raising sweep and tick again: {sandbox.sweep_calls}"
    )


async def test_a_ceiling_that_stopped_being_applied_says_so(caplog):
    """Surviving silently is how a ceiling stops existing without anyone noticing.

    The test above pins that the reaper lives through a raising sweep, which is
    right — but a bare `contextlib.suppress` also means an operator who set a
    number never learns it is doing nothing. What raises here is no longer only
    a racing `FileNotFoundError`: the cross-pod check is a specstar call, so an
    outage of it turns the ceiling off indefinitely, and the over-ceiling
    warning cannot cover that because it only fires when a sweep COMPLETES.

    `kill_idle` and `mirror_warm` — the two precedents this suppression was
    modelled on — both log with `exc_info`. This one did not.
    """
    spec = make_spec(default_user="u")
    sandbox = _ExplodingSweepSandbox()
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=_ExecRunner(),
        idle_timeout=timedelta(seconds=0.1),
        idle_check_interval=timedelta(seconds=0.05),
        mirror_interval=timedelta(seconds=60),
        uv_cache_max_bytes=4096,
    )
    iid = register_rca_item(spec)
    with caplog.at_level(logging.WARNING, logger="workspace_app.api.lifecycle"):
        async with _running_app(app) as client:
            await client.post(f"/a/rca/items/{iid}/messages", json={"content": "x"})
            for _ in range(40):
                await asyncio.sleep(0.05)
                if sandbox.sweep_calls >= 1:
                    break

    said = [r for r in caplog.records if "uv cache" in r.getMessage()]
    assert said, (
        "a ceiling that has stopped being applied must reach the log: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    assert said[0].exc_info is not None, (
        "and with the traceback — the reason is the whole value of the line"
    )
