"""Build an `IsolatedProcessSandbox` and expose it over HTTP.

The testable half of `python -m sandbox_host`: turn `SandboxHostSettings` into
the isolating backend and wrap it in the FastAPI host. The serve glue (uvicorn,
boot narration) lives in `__main__`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .app import check_cgroup_ready, make_host_app
from .config import SandboxHostSettings
from .isolated_process import IsolatedProcessSandbox

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


def resolve_cgroup_root(settings: SandboxHostSettings) -> Path:
    return Path(settings.cgroup_root or _DEFAULT_CGROUP_ROOT)


def resolve_tools_dir(settings: SandboxHostSettings) -> Path | None:
    """The prebuilt-tools dir bind-mounted at `/.tools` in each sandbox. Explicit
    in config (no magic default — the host is its own image) and lenient: unset
    ⇒ the host simply serves without tools (#251)."""
    return Path(settings.tools_dir) if settings.tools_dir else None


def build_sandbox(settings: SandboxHostSettings) -> IsolatedProcessSandbox:
    return IsolatedProcessSandbox(
        uid_min=settings.uid_min,
        uid_max=settings.uid_max,
        cgroup_root=resolve_cgroup_root(settings),
        root_dir=settings.root,
        exec_timeout=settings.exec_timeout,
        log_timeout=settings.log_timeout,
        # #251: actually deliver the agent's prebuilt tools into the sandbox.
        # (The old host never wired this, so http-sandbox agents had no tools.)
        tools_dir=resolve_tools_dir(settings),
        memory_max=settings.memory_max,
        cpu_cores=settings.cpu_cores,
        pids_max=settings.pids_max,
    )


def build_host_app(settings: SandboxHostSettings, *, pod_ip: str | None) -> FastAPI:
    sandbox = build_sandbox(settings)
    cgroup_root = resolve_cgroup_root(settings)
    return make_host_app(
        sandbox,
        advertise_url=advertise_url(settings.bind, pod_ip),
        idle_ttl=settings.idle_ttl,
        readiness=lambda: check_cgroup_ready(cgroup_root),
    )
