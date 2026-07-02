"""IsolatedProcessSandbox — per-item OS-user + cgroup isolation for the LOCAL
sandbox on a SHARED working volume (#345).

A `LocalProcessSandbox` subclass that adds the isolation a *sandbox* must have
once item dirs are siblings on one shared volume: each item's `exec` runs as its
OWN Linux uid under its OWN cgroup v2 slice, so co-tenant items can't read,
signal, or starve one another even though their dirs live side by side. NO
namespaces/jail — pure uid + cgroups, the model that works in our pods (ported
from the standalone sandbox host's `IsolatedProcessSandbox`).

#345 twist vs the host's pool-allocated version: the uid is DERIVED from the
item id (`uid_base + xxhash(item_id) % uid_range`), so it is STABLE on every pod.
That keeps file ownership on the shared vol + the cgroup consistent fleet-wide
with ZERO cross-pod coordination, makes `create` idempotent (a pod re-attaching
to an item another pod provisioned chowns to the same uid — a no-op), and means
recycle frees no uid (nothing was allocated). The host runs as root (or with
CAP_SETUID/SETGID) to setuid/chown; a host without those caps must use the plain
`LocalProcessSandbox` (the factory picks per `sandbox.isolation`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import xxhash

from .local_process import _HOME, LocalProcessSandbox, _validate_sandbox_id
from .protocol import SandboxHandle, SandboxSpec

# cgroup v2 cpu.max uses a fixed 100ms accounting period.
_CPU_PERIOD = 100_000
_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3}
# CAP_SETUID is capability bit 7 (linux/capability.h) — needed to drop to a
# foreign uid without being root.
_CAP_SETUID_BIT = 7


def _parse_size(text: str) -> str:
    """Friendly size ("512M") → the byte string cgroup `memory.max` wants.

    cgroup v2 takes a raw byte count or the literal "max" — not a suffixed
    string — so the human-facing config unit is translated here."""
    text = text.strip()
    if text == "max":
        return "max"
    unit = _SIZE_UNITS.get(text[-1].upper())
    if unit is None:
        return str(int(text))
    return str(int(text[:-1]) * unit)


def _cpu_max(cores: float) -> str:
    """Fractional cores → cgroup v2 `cpu.max` ("<quota> <period>")."""
    return f"{int(cores * _CPU_PERIOD)} {_CPU_PERIOD}"


def _derive_uid(item_id: str, *, uid_base: int, uid_range: int) -> int:
    """Map an item id to a STABLE numeric uid: ``uid_base + xxhash(item_id) %
    uid_range``. A pure function of the id, so every pod derives the SAME uid for
    an item — file ownership on the shared vol and the cgroup stay consistent
    fleet-wide with no coordination, and recycle needs no uid free (nothing was
    allocated). The wide default range keeps collisions negligible; a collision
    only lets two of OUR items share a uid (degraded isolation for that pair),
    never a privilege gain."""
    return uid_base + xxhash.xxh3_64_intdigest(item_id) % uid_range


def _acl_argv(workspace: Path, uid: int) -> list[str]:
    """`setfacl` argv granting `uid` rwx on the workspace AND as the default ACL,
    so files the root host later writes into it stay writable by the sandbox
    uid (it owns the dir but not those root-written files)."""
    spec = f"u:{uid}:rwx"
    return ["setfacl", "-R", "-m", spec, "-d", "-m", spec, str(workspace)]


def _setpriv_cgroup_argv(cmd: list[str], *, uid: int, gid: int, cgroup: Path) -> list[str]:
    """Wrap `cmd` so it (1) joins the per-item cgroup by writing the shell's own
    pid into `cgroup.procs`, then (2) `exec`s `setpriv` to drop to the item uid/
    gid. `cmd` rides through `"$@"`, so it is never re-quoted."""
    procs = shlex.quote(str(cgroup / "cgroup.procs"))
    script = f'echo $$ > {procs}; exec "$@"'
    return [
        "sh",
        "-c",
        script,
        "sh",
        "setpriv",
        f"--reuid={uid}",
        f"--regid={gid}",
        "--clear-groups",
        "--",
        *cmd,
    ]


def _has_cap_setuid(cap_eff_hex: str) -> bool:
    """True if an effective-capability hex mask (from /proc/self/status CapEff)
    includes CAP_SETUID. A malformed value reads as 'no cap' (conservative)."""
    try:
        return bool(int(cap_eff_hex, 16) & (1 << _CAP_SETUID_BIT))
    except ValueError:
        return False


def _read_cap_eff() -> str:
    """The process's effective capability mask as a hex string ("0" when the
    status file is unreadable — e.g. non-Linux — so detection fails closed)."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "0"


def isolation_supported(cgroup_root: str | None) -> tuple[bool, str]:
    """Whether this host can actually run isolated sandboxes: it needs the power
    to drop to a foreign uid (root, or CAP_SETUID) AND a writable cgroup v2 root.
    Returns ``(ok, reason)`` — the reason names the missing piece so the factory's
    fail-loud (explicit opt-in on an unsupported host) is actionable."""
    if os.geteuid() != 0 and not _has_cap_setuid(_read_cap_eff()):
        return False, "no CAP_SETUID (not root and the cap is not in CapEff)"
    root = Path(cgroup_root) if cgroup_root else _detect_cgroup_root()
    if root is None:
        return False, "no cgroup v2 root found (mount a delegated cgroup2 subtree)"
    if not (root.is_dir() and os.access(root, os.W_OK)):
        return False, f"cgroup root {root} is not a writable directory"
    return True, "ok"


def _detect_cgroup_root() -> Path | None:
    """The unified cgroup v2 mount, or None. A v2 hierarchy is marked by a
    `cgroup.controllers` file at the mount root."""
    candidate = Path("/sys/fs/cgroup")
    return candidate if (candidate / "cgroup.controllers").exists() else None


class _CgroupManager:
    """Creates/removes a per-item cgroup v2 directory under a delegated root.

    `root` is the delegated subtree (real `/sys/fs/cgroup/...` in production, a
    tmp dir in tests — the writes are identical plain-file writes either way).
    """

    def __init__(self, root: Path, *, memory_max: str, cpu_cores: float, pids_max: int) -> None:
        self._root = root
        self._memory_max = _parse_size(memory_max)
        self._cpu_max = _cpu_max(cpu_cores)
        self._pids_max = str(pids_max)

    def create(self, name: str) -> Path:
        # #345: exist_ok=True (vs the host's exist_ok=False) so a re-create on the
        # same pod — an item whose local session was dropped while its shared dir
        # stayed live — re-attaches to its cgroup instead of raising. The limit
        # files are (re)written either way, so the caps are always current.
        cg = self._root / name
        cg.mkdir(parents=True, exist_ok=True)
        (cg / "memory.max").write_text(self._memory_max)
        (cg / "cpu.max").write_text(self._cpu_max)
        (cg / "pids.max").write_text(self._pids_max)
        return cg

    def remove(self, cg: Path) -> None:
        # cgroup.kill (v2) reaps any procs still inside; then the now-empty
        # cgroup rmdir's. Both are best-effort — a tmp-fs fake can't rmdir a
        # populated dir, and a real leak is cleaned at pod restart.
        with contextlib.suppress(OSError):
            (cg / "cgroup.kill").write_text("1")
        with contextlib.suppress(OSError):
            cg.rmdir()


def _run_setfacl(argv: list[str]) -> None:
    subprocess.run(argv, check=True, capture_output=True)


# A seam for the one true system-binary boundary (`setfacl`): the default shells
# out; tests inject a spy so they need neither root nor the `acl` package.
AclRunner = Callable[[list[str]], None]


class IsolatedProcessSandbox(LocalProcessSandbox):
    """`LocalProcessSandbox` + per-item uid/gid + cgroup isolation (no jail).

    Inherits the shared-dir file ops and the exec pump/timeout unchanged;
    overrides only `create` (derive the uid, own + ACL the workspace, make the
    cgroup), `kill` (reap the cgroup), and the `_exec_argv` seam (wrap the command
    in a cgroup-join + `setpriv` privilege drop). The uid is a pure function of
    the item id, so nothing is pooled or freed — see the module docstring.
    """

    def __init__(
        self,
        *,
        cgroup_root: str | Path,
        uid_base: int = 1_000_000,
        uid_range: int = 2_000_000_000,
        root_dir: str | Path | None = None,
        exec_timeout: float = 60.0,
        log_timeout: float = 60.0,
        tools_dir: Path | None = None,
        memory_max: str = "512M",
        cpu_cores: float = 1.0,
        pids_max: int = 512,
        acl_runner: AclRunner | None = None,
    ) -> None:
        super().__init__(
            root_dir=Path(root_dir) if root_dir is not None else None,
            exec_timeout=exec_timeout,
            log_timeout=log_timeout,
            isolate=False,  # uid + cgroup ARE the isolation; no namespaces
            tools_dir=tools_dir,
        )
        self._uid_base = uid_base
        self._uid_range = uid_range
        self._cgroup_root = Path(cgroup_root)
        self._cgroups = _CgroupManager(
            self._cgroup_root,
            memory_max=memory_max,
            cpu_cores=cpu_cores,
            pids_max=pids_max,
        )
        self._acl_runner: AclRunner = acl_runner or _run_setfacl

    def _uid_for(self, handle_id: str) -> int:
        return _derive_uid(handle_id, uid_base=self._uid_base, uid_range=self._uid_range)

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        handle = await super().create(spec, sandbox_id)
        uid = self._uid_for(handle.id)
        ws = self._workspace(handle)
        await asyncio.to_thread(self._cgroups.create, handle.id)
        await asyncio.to_thread(self._provision, ws, uid)
        return handle

    def _provision(self, workspace: Path, uid: int) -> None:
        # Own the workspace to the item uid (gid left as-is via -1, so this works
        # non-root when uid == the caller) and lock it to the owner; the default
        # ACL keeps root-host-written files writable by the uid. Idempotent — a
        # re-create chowns to the same derived uid (a no-op).
        os.chown(workspace, uid, -1)
        os.chmod(workspace, 0o700)
        self._acl_runner(_acl_argv(workspace, uid))
        # #393: the per-sandbox HOME (a workspace sibling) must be writable by
        # the item uid so the carrier launcher's HOME/caches + a user's `pip
        # --user` install land there. No default ACL — only the uid writes here.
        # Idempotent — a re-create chowns to the same derived uid (a no-op).
        home = workspace.parent / _HOME
        os.chown(home, uid, -1)
        os.chmod(home, 0o700)

    async def kill(self, handle: SandboxHandle) -> None:
        # The cgroup path is DERIVED from the id (no per-pod identity map), so a
        # pod can reap an item it serves without having been the one that created
        # it. Best-effort (remove suppresses OSError); then the inherited kill
        # rmtree's the shared dir + raises SandboxNotFound for an unknown handle.
        with contextlib.suppress(ValueError):
            cg = self._cgroup_root / _validate_sandbox_id(handle.id)
            await asyncio.to_thread(self._cgroups.remove, cg)
        await super().kill(handle)

    def _exec_argv(
        self, handle: SandboxHandle, cmd: list[str]
    ) -> tuple[list[str], Path, dict[str, str]]:
        argv, cwd, env = super()._exec_argv(handle, cmd)
        uid = self._uid_for(handle.id)
        cgroup = self._cgroup_root / handle.id
        env["TMPDIR"] = str(cwd)  # per-item tmp inside the workspace
        wrapped = _setpriv_cgroup_argv(argv, uid=uid, gid=uid, cgroup=cgroup)
        return wrapped, cwd, env
