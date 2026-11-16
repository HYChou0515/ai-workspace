"""Build an `IsolatedProcessSandbox` and expose it over HTTP.

The testable half of `python -m sandbox_host`: turn `SandboxHostSettings` into
the isolating backend and wrap it in the FastAPI host. The serve glue (uvicorn,
boot narration) lives in `__main__`.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import FastAPI

from .app import check_cgroup_ready, make_host_app
from .config import SandboxHostSettings
from .isolated_process import IsolatedProcessSandbox
from .nfs_archive import NfsArchive
from .tool_cache import EXT_DIR, ToolCache, _GroupHardener
from .tool_resolve import ToolResolver
from .tool_secrets import SecretsError, private_key_from_text

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


def tool_group(settings: SandboxHostSettings) -> int:
    """The group a third-party tool command holds and the only one that may
    read the third-party bundles (docs/plan-tool-identity-secrets.md).

    The bottom of the range the operator set aside for sandbox uids: already
    reserved on this host, so it needs no setting of its own and collides with
    no account — and the uid pool starts one above it, so no sandbox uid ever
    holds it. One function for both consumers: a cache hardened for a group
    the tools never join would run no third-party tool at all."""
    return settings.uid_min


def build_tool_resolver(settings: SandboxHostSettings) -> ToolResolver | None:
    """#674: the third-party tool store, or None when it cannot be run safely.

    Two things are required and neither has a sane default. Without a tools
    dir there is nowhere to unpack; without `TOOL_BUILDER_ID` there is no ABI
    anchor to gate on, and a host that cannot gate must not fetch — it would
    happily mount a bundle built for another base and hand the failure to a
    user. Absent either, the endpoint answers "no tool store configured",
    which is a diagnosable state rather than a silent one."""
    tools = resolve_tools_dir(settings)
    builder_id = os.environ.get("TOOL_BUILDER_ID")
    if tools is None or not builder_id:
        return None
    cache = ToolCache(tools / EXT_DIR, harden=_GroupHardener(tool_group(settings)))
    # Bundles this host unpacked under the old rule (readable by any uid) stay
    # on disk across a restart; bring them under the tool group before serving.
    cache.reharden_installed()
    return ToolResolver(
        cache,
        builder_id=builder_id,
        arch=platform.machine(),
        state_dir=tools,
    )


def load_tool_secret_key(settings: SandboxHostSettings) -> X25519PrivateKey | None:
    """This deployment's key for third-party tools' sealed credentials, read
    once at startup (docs/plan-tool-identity-secrets.md P4). A configured file
    that cannot be read stops the host HERE: starting without it would refuse
    every sealed tool at its first call — later, and in front of a user."""
    if not settings.tool_secret_key_file:
        return None
    try:
        text = Path(settings.tool_secret_key_file).read_text()
    except OSError as exc:
        raise SecretsError(
            f"the tool secret key file {settings.tool_secret_key_file} cannot be read ({exc})"
        ) from exc
    return private_key_from_text(text)


def build_sandbox(settings: SandboxHostSettings) -> IsolatedProcessSandbox:
    return IsolatedProcessSandbox(
        tool_secret_key=load_tool_secret_key(settings),
        # See `tool_group`: the pool starts one above it.
        tool_gid=tool_group(settings),
        uid_min=tool_group(settings) + 1,
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


def build_archive(settings: SandboxHostSettings) -> NfsArchive | None:
    """#492: the durable NFS archive, or None when `nfs_root` is unset (the
    transitional / local-dev default — no restore-on-create, no persist)."""
    if not settings.nfs_root:
        return None
    return NfsArchive(settings.nfs_root, pack=settings.archive_pack)


def build_host_app(settings: SandboxHostSettings, *, pod_ip: str | None) -> FastAPI:
    sandbox = build_sandbox(settings)
    cgroup_root = resolve_cgroup_root(settings)
    return make_host_app(
        sandbox,
        advertise_url=advertise_url(settings.bind, pod_ip),
        idle_ttl=settings.idle_ttl,
        readiness=lambda: check_cgroup_ready(cgroup_root),
        archive=build_archive(settings),
        tool_resolver=build_tool_resolver(settings),
        pack_drain_s=settings.pack_drain_s,
    )
