"""Sandbox host operations — health, readiness, graceful drain, idle-reaper."""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

from workspace_app.sandbox.host.app import check_cgroup_ready, make_host_app
from workspace_app.sandbox.mock import MockSandbox


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
