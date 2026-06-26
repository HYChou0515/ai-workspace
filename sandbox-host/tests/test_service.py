"""service builders — wire SandboxHostSettings → host app, incl. tools delivery."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from sandbox_host.config import SandboxHostSettings
from sandbox_host.isolated_process import IsolatedProcessSandbox
from sandbox_host.service import (
    advertise_url,
    build_host_app,
    build_sandbox,
    resolve_tools_dir,
)


def test_advertise_url_prefers_pod_ip():
    assert advertise_url("0.0.0.0:8000", "10.1.2.3") == "http://10.1.2.3:8000"


def test_advertise_url_falls_back_to_loopback():
    assert advertise_url("0.0.0.0:9000", None) == "http://127.0.0.1:9000"


def test_build_sandbox_threads_host_settings(tmp_path):
    hs = SandboxHostSettings(
        uid_min=200000,
        uid_max=200009,
        cgroup_root=str(tmp_path / "cg"),
        root=str(tmp_path / "sb"),
        memory_max="32M",
        cpu_cores=0.25,
        pids_max=32,
    )
    sb = build_sandbox(hs)
    assert isinstance(sb, IsolatedProcessSandbox)
    # uid range threaded through to the pool
    assert sb._pool.alloc()[0] in range(200000, 200010)


def test_resolve_tools_dir_none_when_unset():
    assert resolve_tools_dir(SandboxHostSettings()) is None


def test_resolve_tools_dir_path_when_set():
    assert resolve_tools_dir(SandboxHostSettings(tools_dir="/opt/tools")) == Path("/opt/tools")


def test_build_sandbox_delivers_configured_tools_dir(tmp_path):
    """#251: a configured tools_dir actually reaches the sandbox (the old host
    never wired this, so http-sandbox agents had no prebuilt tools)."""
    tools = tmp_path / "tools"
    tools.mkdir()
    hs = SandboxHostSettings(cgroup_root=str(tmp_path / "cg"), tools_dir=str(tools))
    sb = build_sandbox(hs)
    assert sb._tools_dir == tools


def test_build_sandbox_no_tools_dir_by_default(tmp_path):
    sb = build_sandbox(SandboxHostSettings(cgroup_root=str(tmp_path / "cg")))
    assert sb._tools_dir is None


def test_build_host_app_is_fastapi(tmp_path):
    app = build_host_app(SandboxHostSettings(cgroup_root=str(tmp_path / "cg")), pod_ip="10.0.0.5")
    assert isinstance(app, FastAPI)


async def test_build_host_app_readyz_runs_cgroup_check(tmp_path, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "sandbox_host.service.check_cgroup_ready",
        lambda root: seen.setdefault("root", root),
    )
    app = build_host_app(SandboxHostSettings(cgroup_root=str(tmp_path / "cg")), pod_ip=None)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        assert (await c.get("/readyz")).status_code == 200
    assert str(seen["root"]).endswith("cg")  # the configured cgroup_root threaded in


@pytest.mark.parametrize("pod_ip", [None, "10.0.0.9"])
def test_build_host_app_advertise_url_uses_pod_ip(tmp_path, pod_ip):
    # advertise_url is wired from settings.bind + pod_ip; just assert it builds.
    app = build_host_app(
        SandboxHostSettings(bind="0.0.0.0:8123", cgroup_root=str(tmp_path / "cg")), pod_ip=pod_ip
    )
    assert isinstance(app, FastAPI)
