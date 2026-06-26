"""IsolatedProcessSandbox — the production backend this host runs.

A `LocalProcessSandbox` subclass that adds the isolation a *sandbox* must have
(otherwise it would be no better than the plain local backend): each handle runs
as its own Linux uid/gid under its own cgroup v2, so sandboxes cannot read,
signal, or starve one another. NO namespaces/jail — isolation is pure uid +
cgroups, the model that works in our pods.

Per handle, `create` allocates a numeric uid/gid from a pool, owns the workspace
to it (`chmod 700` + a default POSIX ACL so files the root host later writes stay
writable by the uid), and makes a cgroup with memory/cpu/pids caps; `exec` wraps
the command so it joins the cgroup and drops privilege via `setpriv`; `kill`
frees the uid and removes the cgroup. The file ops + the exec pump/timeout are
inherited from `LocalProcessSandbox` unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .local_process import LocalProcessSandbox
from .protocol import SandboxHandle, SandboxSpec

# cgroup v2 cpu.max uses a fixed 100ms accounting period.
_CPU_PERIOD = 100_000
_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3}


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


def _acl_argv(workspace: Path, uid: int) -> list[str]:
    """`setfacl` argv granting `uid` rwx on the workspace AND as the default ACL,
    so files the root host later writes into it stay writable by the sandbox
    uid (it owns the dir but not those root-written files)."""
    spec = f"u:{uid}:rwx"
    return ["setfacl", "-R", "-m", spec, "-d", "-m", spec, str(workspace)]


def _setpriv_cgroup_argv(cmd: list[str], *, uid: int, gid: int, cgroup: Path) -> list[str]:
    """Wrap `cmd` so it (1) joins the per-handle cgroup by writing the shell's
    own pid into `cgroup.procs`, then (2) `exec`s `setpriv` to drop to the
    sandbox uid/gid. `cmd` rides through `"$@"`, so it is never re-quoted."""
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


class _CgroupManager:
    """Creates/removes a per-handle cgroup v2 directory under a delegated root.

    `root` is the delegated subtree (real `/sys/fs/cgroup/...` in production, a
    tmp dir in tests — the writes are identical plain-file writes either way).
    """

    def __init__(self, root: Path, *, memory_max: str, cpu_cores: float, pids_max: int) -> None:
        self._root = root
        self._memory_max = _parse_size(memory_max)
        self._cpu_max = _cpu_max(cpu_cores)
        self._pids_max = str(pids_max)

    def create(self, name: str) -> Path:
        cg = self._root / name
        cg.mkdir(parents=True, exist_ok=False)
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


class _UidPool:
    """Hands out distinct numeric uid/gid pairs from an inclusive range.

    Bare numbers — no `/etc/passwd` entry is needed to `setuid`/`chown` to a
    uid. gid == uid (symmetric, and irrelevant to file isolation once the
    workspace is `chmod 700`). Freed ids are reused; exhaustion is loud."""

    def __init__(self, uid_min: int, uid_max: int) -> None:
        self._free: list[int] = list(range(uid_min, uid_max + 1))
        self._used: set[int] = set()

    def alloc(self) -> tuple[int, int]:
        if not self._free:
            raise RuntimeError("uid pool exhausted")
        uid = self._free.pop()
        self._used.add(uid)
        return uid, uid

    def free(self, uid: int, gid: int) -> None:
        if uid in self._used:
            self._used.discard(uid)
            self._free.append(uid)


def _run_setfacl(argv: list[str]) -> None:
    subprocess.run(argv, check=True, capture_output=True)


@dataclass(frozen=True)
class _Identity:
    uid: int
    gid: int
    cgroup: Path


# A seam for the one true system-binary boundary (`setfacl`): the default shells
# out; tests inject a spy so they need neither root nor the `acl` package.
AclRunner = Callable[[list[str]], None]


class IsolatedProcessSandbox(LocalProcessSandbox):
    """`LocalProcessSandbox` + per-handle uid/gid + cgroup isolation (no jail).

    Inherits the file ops and the exec pump/timeout unchanged; overrides only
    `create` (allocate identity, own + ACL the workspace, make the cgroup),
    `kill` (free the identity, reap the cgroup), and the `_exec_argv` seam (wrap
    the command in a cgroup-join + `setpriv` privilege drop). The host process
    must run as root to setuid/chown.
    """

    def __init__(
        self,
        *,
        uid_min: int,
        uid_max: int,
        cgroup_root: str | Path,
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
        self._pool = _UidPool(uid_min, uid_max)
        self._cgroups = _CgroupManager(
            Path(cgroup_root),
            memory_max=memory_max,
            cpu_cores=cpu_cores,
            pids_max=pids_max,
        )
        self._identities: dict[str, _Identity] = {}
        self._acl_runner: AclRunner = acl_runner or _run_setfacl
        self._alloc_lock = asyncio.Lock()

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        async with self._alloc_lock:  # serialize uid allocation across handles
            handle = await super().create(spec)
            uid, gid = self._pool.alloc()
            ws = self._workspace(handle)
            cgroup = await asyncio.to_thread(self._cgroups.create, handle.id)
            await asyncio.to_thread(self._provision, ws, uid)
            self._identities[handle.id] = _Identity(uid=uid, gid=gid, cgroup=cgroup)
            return handle

    def _provision(self, workspace: Path, uid: int) -> None:
        # Own the workspace to the sandbox uid (gid left as-is via -1, so this
        # works non-root when uid == the caller) and lock it to the owner; the
        # default ACL keeps root-host-written files writable by the uid.
        os.chown(workspace, uid, -1)
        os.chmod(workspace, 0o700)
        self._acl_runner(_acl_argv(workspace, uid))

    async def kill(self, handle: SandboxHandle) -> None:
        ident = self._identities.pop(handle.id, None)
        if ident is not None:
            await asyncio.to_thread(self._cgroups.remove, ident.cgroup)
            self._pool.free(ident.uid, ident.gid)
        await super().kill(handle)

    def _exec_argv(
        self, handle: SandboxHandle, cmd: list[str]
    ) -> tuple[list[str], Path, dict[str, str]]:
        argv, cwd, env = super()._exec_argv(handle, cmd)
        ident = self._identities[handle.id]
        env["TMPDIR"] = str(cwd)  # per-handle tmp inside the workspace
        wrapped = _setpriv_cgroup_argv(argv, uid=ident.uid, gid=ident.gid, cgroup=ident.cgroup)
        return wrapped, cwd, env
