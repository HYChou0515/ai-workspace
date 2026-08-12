"""Sandbox host operations — health, readiness, graceful drain, idle-reaper."""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

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
