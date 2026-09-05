"""Sandbox host operations — health, readiness, graceful drain, idle-reaper."""

from __future__ import annotations

import asyncio
import contextlib
import os
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.__main__ import _reaper_loop, _reaper_task
from sandbox_host.app import check_cgroup_ready, make_host_app
from sandbox_host.mock import MockSandbox


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


async def test_healthz_ok():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


async def test_readyz_ok_by_default():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        assert (await c.get("/readyz")).status_code == 200


async def test_readyz_503_when_readiness_check_fails():
    def not_ready() -> None:
        raise RuntimeError("cgroup v2 not mounted")

    app = make_host_app(MockSandbox(), advertise_url="http://h", readiness=not_ready)
    async with _client(app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 503
        assert r.json()["ready"] is False


async def test_create_rejected_with_503_while_draining():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    app.state.controller.start_draining()
    async with _client(app) as c:
        r = await c.post("/sandboxes", json={})
        assert r.status_code == 503


async def test_drain_endpoint_then_create_rejected():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        assert (await c.post("/sandboxes", json={})).status_code == 200  # ok before
        assert (await c.post("/drain")).status_code == 202
        assert (await c.post("/sandboxes", json={})).status_code == 503  # rejected after


async def test_idle_reaper_kills_only_stale_handles():
    clock = {"t": 0.0}
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", idle_ttl=100.0, clock=lambda: clock["t"]
    )
    ctrl = app.state.controller
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={})).json()["remote_id"]
    clock["t"] = 50.0
    assert await ctrl.reap_idle() == []  # not yet idle
    clock["t"] = 201.0
    assert await ctrl.reap_idle() == [rid]  # idle past the ttl → reaped
    assert await ctrl.reap_idle() == []  # already gone


async def test_activity_on_any_endpoint_resets_the_idle_timer():
    clock = {"t": 0.0}
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", idle_ttl=100.0, clock=lambda: clock["t"]
    )
    ctrl = app.state.controller
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={})).json()["remote_id"]
        clock["t"] = 80.0
        await c.get(f"/sandboxes/{rid}/exists", params={"path": "/x"})  # touch
    clock["t"] = 150.0  # 70s since the touch < ttl
    assert await ctrl.reap_idle() == []


async def test_idle_reaper_disabled_when_ttl_zero():
    clock = {"t": 0.0}
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", idle_ttl=0.0, clock=lambda: clock["t"]
    )
    ctrl = app.state.controller
    async with _client(app) as c:
        await c.post("/sandboxes", json={})
    clock["t"] = 1e9
    assert await ctrl.reap_idle() == []


def test_check_cgroup_ready_passes_on_writable_v2(tmp_path):
    marker = tmp_path / "cgroup.controllers"
    marker.write_text("cpu memory pids")
    root = tmp_path / "delegated"
    root.mkdir()
    check_cgroup_ready(root, controllers_marker=marker)  # no raise


def test_check_cgroup_ready_raises_without_v2(tmp_path):
    with pytest.raises(RuntimeError, match="cgroup v2"):
        check_cgroup_ready(tmp_path / "x", controllers_marker=tmp_path / "absent")


def test_check_cgroup_ready_raises_when_not_writable(tmp_path):
    marker = tmp_path / "m"
    marker.write_text("cpu")
    root = tmp_path / "ro"
    root.mkdir()
    os.chmod(root, 0o500)
    try:
        with pytest.raises(RuntimeError, match="writable"):
            check_cgroup_ready(root, controllers_marker=marker)
    finally:
        os.chmod(root, 0o700)


async def test_healthz_names_the_behaviours_this_build_has():
    """Which code a running host carries is otherwise invisible: `image:
    sandbox-host:latest` is the same string before and after a rebuild, and a
    host that is merely OLD answers every request perfectly well — it just
    behaves like the older code. That cost a full diagnosis once already: a
    sandbox whose `$HOME` was never set looked, from the app side, exactly like
    a sandbox whose HOME was set correctly.

    Capabilities rather than a version number, because they are what the caller
    actually wants to know and they cannot drift from the code that declares
    them — no build-time stamping to remember, no compatibility table to keep
    in sync."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        body = (await c.get("/healthz")).json()
    assert body["status"] == "ok"
    # `per-exec-home` is the one this was built for: it says every exec gets
    # HOME pointed at the sandbox's own `.home`, created and owned on the spot.
    assert "per-exec-home" in body["capabilities"]
    assert isinstance(body["version"], str)


async def test_healthz_publishes_the_ceilings_this_host_will_apply():
    """The app charges a sandbox's owner for what it holds, and a sandbox whose
    spec states nothing is still capped — by THIS host's `SANDBOX_HOST_*`. Those
    live in this service's environment, where the app cannot read them, so it
    could only charge the request: an App that declared nothing held a core for
    free. The number published here is the enforcer's own answer, not a second
    copy of the config, so the two cannot drift."""
    app = make_host_app(
        MockSandbox(cpu_cores=1.0, memory_bytes=512 * 1024**2), advertise_url="http://h"
    )
    async with _client(app) as c:
        body = (await c.get("/healthz")).json()
    assert body["defaults"] == {"cpu_cores": 1.0, "memory_bytes": 512 * 1024**2}
    # Named, so an app talking to a host too old to say can tell "not advertised"
    # from "advertised as nothing" and report a stale image instead of charging 0.
    assert "resource-defaults" in body["capabilities"]


async def test_healthz_survives_a_backend_that_cannot_answer():
    """This endpoint is the deployment's LIVENESS probe. Making it depend on the
    sandbox answering a resource question turns "cannot say" into a crashloop —
    the app charging nothing is a visible under-count, a restarting pod is an
    outage."""

    class _Mute(MockSandbox):
        async def effective_limits(self, spec):  # noqa: ANN001, ANN201
            raise RuntimeError("this backend has no idea")

    app = make_host_app(_Mute(), advertise_url="http://h")
    async with _client(app) as c:
        resp = await c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["defaults"] == {"cpu_cores": None, "memory_bytes": None}


async def test_the_host_can_say_which_items_have_a_live_sandbox():
    """The app cannot otherwise find out what exists.

    Every record it keeps — the heartbeat that bills people, the address that
    routes to a sandbox, the panel that offers a Close button — is a belief
    written down at some past moment. Nothing could check those beliefs against
    the machine, so a stale one was indistinguishable from a true one, and
    clearing a record became the only way to say "gone" even when it was not.

    Keyed by ITEM, because that is the only name the app has. The host already
    tracks both halves for its own idle reaper."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        assert (await c.get("/sandboxes")).json()["sandboxes"] == []

        first = (await c.post("/sandboxes", json={"item_id": "item-a"})).json()["remote_id"]
        second = (await c.post("/sandboxes", json={"item_id": "item-b"})).json()["remote_id"]
        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert sorted(s["item_id"] for s in listed) == ["item-a", "item-b"]
        assert {s["remote_id"] for s in listed} == {first, second}, (
            "each entry names the sandbox it is about"
        )

        await c.delete(f"/sandboxes/{first}")
        assert [s["item_id"] for s in (await c.get("/sandboxes")).json()["sandboxes"]] == ["item-b"]


async def test_a_sandbox_created_without_an_archive_still_remembers_its_item():
    """`_item_of` was filled only when an NFS archive was wired, because until
    now its only reader was `persist`. A listing keyed by item is useless on a
    deployment with no archive if the mapping is not there."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")  # no archive
    async with _client(app) as c:
        await c.post("/sandboxes", json={"item_id": "item-a"})
        assert [s["item_id"] for s in (await c.get("/sandboxes")).json()["sandboxes"]] == ["item-a"]


async def test_a_sandbox_created_without_an_item_is_listed_as_anonymous():
    """An older app, or a caller that has no item, still gets a sandbox — and it
    must appear in the listing, or the host would under-report what it is
    running."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        await c.post("/sandboxes", json={})
        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert len(listed) == 1
        assert listed[0]["item_id"] is None


async def test_a_listed_sandbox_can_be_addressed_directly():
    """Naming the item is not enough to DO anything about it.

    The service in front of the host load-balances, so a listing answers for the
    one pod that happened to take the request, and every later call has to reach
    THAT pod. `create` already solves this by returning the answering pod's own
    directly-addressable url; a listing that omitted it would let the app see an
    orphaned sandbox and still have no way to kill it."""
    app = make_host_app(MockSandbox(), advertise_url="http://pod-7:8000")
    async with _client(app) as c:
        await c.post("/sandboxes", json={"item_id": "item-a"})
        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert [s["pod_url"] for s in listed] == ["http://pod-7:8000"]


async def test_a_sandbox_whose_teardown_failed_is_still_listed():
    """Forgetting it would leave it running and invisible to everything.

    The idle reaper walks the same table, so dropping the entry before the kill
    succeeded meant nothing would ever retry the teardown AND nothing could
    report the sandbox — the app's last-resort way of finding an orphan would be
    blind to precisely the sandbox most likely to have become one."""

    class _StubbornSandbox(MockSandbox):
        async def kill(self, handle):
            raise RuntimeError("device busy")

    app = make_host_app(_StubbornSandbox(), advertise_url="http://h")
    ctrl = app.state.controller
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-a"})).json()["remote_id"]
        # Driven on the controller: the route has no handler for an arbitrary
        # backend failure, and this is about what the host REMEMBERS afterwards,
        # not about which status that failure maps to.
        with pytest.raises(RuntimeError):
            await ctrl.kill(rid)

        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert [s["item_id"] for s in listed] == ["item-a"]


async def test_one_sandbox_that_will_not_die_does_not_strand_the_rest():
    """The reaper used to stop at the first failure, so every idle sandbox after
    it survived the pass — and the next pass hits the same one first and stops
    in the same place, so they survive forever."""
    clock = {"t": 0.0}

    class _OneStubborn(MockSandbox):
        stubborn = ""

        async def kill(self, handle):
            if handle.id == self.stubborn:
                raise RuntimeError("device busy")
            await super().kill(handle)

    backend = _OneStubborn()
    app = make_host_app(backend, advertise_url="http://h", idle_ttl=100.0, clock=lambda: clock["t"])
    ctrl = app.state.controller
    async with _client(app) as c:
        first = (await c.post("/sandboxes", json={"item_id": "a"})).json()["remote_id"]
        second = (await c.post("/sandboxes", json={"item_id": "b"})).json()["remote_id"]
    backend.stubborn = first

    clock["t"] = 201.0
    assert await ctrl.reap_idle() == [second], "the second sandbox was stranded by the first"


# --- #775: the uv-cache ceiling, from the env var to the sweep -----------------
#
# The leaf — `LocalProcessSandbox.sweep_uv_cache` — is covered in
# `test_project_venv_shim.py`. Everything BETWEEN the knob and the leaf was not,
# on either the app or the host side, and this is the only one of the two live
# in production: `kind: http` runs here. A ceiling that parses into settings and
# is then handed to nobody is a knob that does nothing, and the whole chain
# would still have been green.


async def test_the_controller_asks_the_live_sandboxes_which_caches_are_busy():
    """Who fills `in_use` is the interesting half.

    The leaf test proves the sweep obeys the set it is given. It says nothing
    about where the set comes from in production — and that matters here
    because this host POOLS uids and frees them on kill, so a uid-keyed set
    would hand a live item's cache to whoever inherits its uid next. It has to
    come from the live sandboxes' own ITEM keys."""

    class _Backend:
        def __init__(self) -> None:
            self.swept: list[tuple[set[str], int | None]] = []

        def cache_keys_in_use(self) -> set[str]:
            return {"busy-item"}

        def sweep_uv_cache(self, *, in_use: set[str], max_bytes: int | None = None) -> list[str]:
            self.swept.append((in_use, max_bytes))
            return ["evicted-item"]

    backend = _Backend()
    app = make_host_app(backend, advertise_url="http://h")

    removed = await app.state.controller.sweep_uv_cache(max_bytes=999)

    assert removed == ["evicted-item"]
    assert backend.swept == [({"busy-item"}, 999)], (
        f"the ceiling and the live set must both reach the backend: {backend.swept}"
    )


async def test_a_backend_that_keeps_no_uv_cache_sweeps_nothing():
    """`MockSandbox` and any future backend without a cache must answer the
    sweeper with an empty list, not an AttributeError — that would kill the
    tick, and with it the idle reap and the tool-cache sweep that share it."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")

    assert await app.state.controller.sweep_uv_cache(max_bytes=1) == []


async def test_the_idle_tick_sweeps_the_uv_cache_with_the_configured_ceiling():
    """The top link, entered where the pod enters it.

    Everything below was reachable through its own call, which is how a ceiling
    could be read from the env, stored on the settings, and then handed to
    nobody. This drives one tick of the real `_reaper_loop`."""

    class _Controller:
        idle_ttl = 600.0

        def __init__(self) -> None:
            self.uv_ceilings: list[int | None] = []

        async def reap_idle(self) -> list[str]:
            return []

        async def sweep_tool_cache(self, *, max_bytes: int | None = None) -> list[str]:
            return []

        async def sweep_uv_cache(self, *, max_bytes: int | None = None) -> list[str]:
            self.uv_ceilings.append(max_bytes)
            return []

    ticks = 0

    async def one_tick_then_stop(_interval: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks > 1:  # the body has run once; end the loop the way SIGTERM does
            raise asyncio.CancelledError

    controller = _Controller()
    with (
        patch.object(asyncio, "sleep", one_tick_then_stop),
        contextlib.suppress(asyncio.CancelledError),
    ):
        await _reaper_loop(controller, uv_cache_max_bytes=4096)

    assert controller.uv_ceilings == [4096], (
        f"the configured ceiling must reach the sweep on the tick: {controller.uv_ceilings}"
    )


async def test_switching_the_reaper_off_says_which_ceilings_go_with_it():
    """`SANDBOX_HOST_IDLE_TTL=0` is documented as switching off idle reaping.
    It also switches off BOTH cache sweeps, because they ride that task — so an
    operator who set a ceiling gets no eviction and, until now, no explanation.

    Same class the app side just closed for `sandbox.uv_cache_max_bytes`: a
    number that parses, is stored, and is handed to nobody. Said once, at the
    point where the decision is actually made, rather than only in a YAML
    comment nobody reads at 3am.
    """
    said: list[str] = []

    class _Off:
        idle_ttl = 0.0

    task = _reaper_task(
        _Off(),
        tool_cache_max_bytes=None,
        uv_cache_max_bytes=8_589_934_592,
        say=said.append,
    )

    assert task is None, "ttl 0 means no reaper — that part is intended"
    assert any("SANDBOX_HOST_UV_CACHE_MAX_BYTES" in s for s in said), (
        f"and the ceiling it silently disables must be named: {said}"
    )
    assert not any("TOOL_CACHE" in s for s in said), (
        f"but only the ceilings that were actually set: {said}"
    )


async def test_the_reaper_stays_quiet_when_it_is_running():
    """The control. Warning on the healthy path is how an operator learns to
    ignore the message on the deployments where it is true."""
    said: list[str] = []

    class _On:
        idle_ttl = 600.0

    task = _reaper_task(
        _On(),
        tool_cache_max_bytes=4096,
        uv_cache_max_bytes=4096,
        say=said.append,
    )
    assert task is not None
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert said == [], f"the ceilings ARE applied here; nothing to report: {said}"
