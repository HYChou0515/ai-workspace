"""Standalone host config — read from `SANDBOX_HOST_*` environment variables.

The host is a separate service: it does NOT read the workspace app's config
loader/schema. Env-var driven (12-factor) so the k8s Deployment sets it inline,
and `load_settings` takes an explicit env mapping so it's covered without
touching the process environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxHostSettings:
    """Wraps an `IsolatedProcessSandbox`: each handle runs as a pooled numeric
    uid (`uid_min..uid_max`) under a cgroup capped by `memory_max` (e.g. "512M")
    / `cpu_cores` / `pids_max`. `cgroup_root` is the delegated cgroup v2 subtree
    (None = auto-detect this pod's own). `tools_dir` is the prebuilt-tools dir
    bind-mounted read-only into each sandbox at `/.tools` (None = no tools).
    `idle_ttl` reaps sandboxes orphaned by an app-pod crash (0 = off)."""

    bind: str = "0.0.0.0:8000"
    uid_min: int = 100000
    uid_max: int = 199999
    memory_max: str = "512M"
    cpu_cores: float = 1.0
    pids_max: int = 512
    cgroup_root: str | None = None
    root: str | None = None
    exec_timeout: float = 60.0
    log_timeout: float = 60.0
    tools_dir: str | None = None
    idle_ttl: float = 1800.0


def load_settings(env: Mapping[str, str]) -> SandboxHostSettings:
    """Build settings from a `SANDBOX_HOST_*` env mapping; unset keys keep the
    dataclass default."""

    def s(name: str, default: str) -> str:
        return env.get(name, default)

    def i(name: str, default: int) -> int:
        v = env.get(name)
        return int(v) if v is not None else default

    def f(name: str, default: float) -> float:
        v = env.get(name)
        return float(v) if v is not None else default

    def opt(name: str) -> str | None:
        return env.get(name)

    return SandboxHostSettings(
        bind=s("SANDBOX_HOST_BIND", "0.0.0.0:8000"),
        uid_min=i("SANDBOX_HOST_UID_MIN", 100000),
        uid_max=i("SANDBOX_HOST_UID_MAX", 199999),
        memory_max=s("SANDBOX_HOST_MEMORY_MAX", "512M"),
        cpu_cores=f("SANDBOX_HOST_CPU_CORES", 1.0),
        pids_max=i("SANDBOX_HOST_PIDS_MAX", 512),
        cgroup_root=opt("SANDBOX_HOST_CGROUP_ROOT"),
        root=opt("SANDBOX_HOST_ROOT"),
        exec_timeout=f("SANDBOX_HOST_EXEC_TIMEOUT", 60.0),
        log_timeout=f("SANDBOX_HOST_LOG_TIMEOUT", 60.0),
        tools_dir=opt("SANDBOX_HOST_TOOLS_DIR"),
        idle_ttl=f("SANDBOX_HOST_IDLE_TTL", 1800.0),
    )
