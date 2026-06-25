"""sandbox host — build an `IsolatedProcessSandbox` and expose it over HTTP.

The testable half of `python -m workspace_app.sandbox_host`: turn a
`SandboxHostSettings` into the isolating backend and wrap it in the generic
FastAPI host. The serve glue (uvicorn, boot narration) lives in `__main__`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from ..config.schema import SandboxHostSettings, Settings
from ..sandbox.host.app import check_cgroup_ready, make_host_app
from ..sandbox.isolated_process import IsolatedProcessSandbox

# Where a pod's own delegated cgroup v2 subtree is mounted when the operator
# doesn't pin `cgroup_root` explicitly.
_DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"


def advertise_url(bind: str, pod_ip: str | None) -> str:
    """The URL this pod tells clients to route back to. `pod_ip` (the k8s
    downward-API `POD_IP`) is directly routable in-cluster; loopback is the
    local-dev fallback. The port is taken from `bind` (`host:port`)."""
    port = bind.rsplit(":", 1)[-1]
    host = pod_ip or "127.0.0.1"
    return f"http://{host}:{port}"


def resolve_cgroup_root(host: SandboxHostSettings) -> Path:
    return Path(host.cgroup_root or _DEFAULT_CGROUP_ROOT)


def build_sandbox(
    host: SandboxHostSettings, *, tools_dir: Path | None = None
) -> IsolatedProcessSandbox:
    return IsolatedProcessSandbox(
        uid_min=host.uid_min,
        uid_max=host.uid_max,
        cgroup_root=resolve_cgroup_root(host),
        root_dir=host.root,
        exec_timeout=host.exec_timeout,
        log_timeout=host.log_timeout,
        tools_dir=tools_dir,
        memory_max=host.memory_max,
        cpu_cores=host.cpu_cores,
        pids_max=host.pids_max,
    )


def build_host_app(
    settings: Settings, *, pod_ip: str | None, tools_dir: Path | None = None
) -> FastAPI:
    host = settings.sandbox_host
    sandbox = build_sandbox(host, tools_dir=tools_dir)
    cgroup_root = resolve_cgroup_root(host)
    return make_host_app(
        sandbox,
        advertise_url=advertise_url(host.bind, pod_ip),
        idle_ttl=host.idle_ttl,
        readiness=lambda: check_cgroup_ready(cgroup_root),
    )
