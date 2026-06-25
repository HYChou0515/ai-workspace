"""sandbox_host service builders — wire SandboxHostSettings → host app."""

from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI

from workspace_app.config.schema import SandboxHostSettings, Settings
from workspace_app.sandbox.isolated_process import IsolatedProcessSandbox
from workspace_app.sandbox_host.service import (
    advertise_url,
    build_host_app,
    build_sandbox,
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


def test_build_host_app_is_fastapi(tmp_path):
    settings = replace(
        Settings(), sandbox_host=SandboxHostSettings(cgroup_root=str(tmp_path / "cg"))
    )
    app = build_host_app(settings, pod_ip="10.0.0.5")
    assert isinstance(app, FastAPI)
