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
import functools
import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .local_process import _HOME, LocalProcessSandbox
from .protocol import EnforcedLimits, SandboxHandle, SandboxSpec

# cgroup v2 cpu.max uses a fixed 100ms accounting period.
_CPU_PERIOD = 100_000
# #775: uv's cache root, a sibling of the sandbox dirs on the same scratch
# volume — same filesystem as the venvs, which is what lets uv hardlink into
# them instead of copying.
_UV_CACHE = ".uv-cache"
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


def _fmt_bytes(nbytes: int) -> str:
    """A resolved byte count → what cgroup v2's `memory.max` wants. 0 means
    unbounded and must reach the cgroup as `max`."""
    return "max" if nbytes <= 0 else str(nbytes)


def _cpu_max(cores: float) -> str:
    """Fractional cores → cgroup v2 `cpu.max` ("<quota> <period>")."""
    return f"{int(cores * _CPU_PERIOD)} {_CPU_PERIOD}"


def _acl_argv(workspace: Path, uid: int) -> list[str]:
    """`setfacl` argv granting `uid` rwx on the workspace AND as the default ACL.
    Defence-in-depth since #504: app/host-written files are now chowned to `uid`
    (real ownership, see `_own` / `reown`), so the default ACL is a belt-and-
    suspenders fallback for any residual root-written path — no longer the sole
    mechanism keeping the sandbox able to touch those files."""
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
        # Kept as NUMBERS beside the cgroup spellings above: `effective` answers
        # "what does this sandbox cost", and `"100000 100000"` is a cpu.max line,
        # not a core count. Same two values, so the fallback below and the one in
        # `create` cannot disagree about which one won.
        self._cpu_cores = cpu_cores
        self._memory_bytes = 0 if self._memory_max == "max" else int(self._memory_max)

    def effective(self, cpu_cores: float | None, memory_bytes: int | None) -> EnforcedLimits:
        """The ceilings `create` would really write for these arguments."""
        return EnforcedLimits(
            cpu_cores=self._cpu_cores if cpu_cores is None else cpu_cores,
            memory_bytes=self._memory_bytes if memory_bytes is None else memory_bytes,
        )

    def create(
        self,
        name: str,
        *,
        cpu_cores: float | None = None,
        memory_bytes: int | None = None,
        pids_max: int | None = None,
    ) -> Path:
        cg = self._root / name
        cg.mkdir(parents=True, exist_ok=False)
        # Per-sandbox ceilings from the request; `None` falls back to this
        # host's configured default PER DIMENSION, so a spec stating only memory
        # keeps the host's cpu and pids. 0 is not None — it means explicitly
        # unbounded, and `_fmt_bytes` turns it into the cgroup's own `max`
        # (writing a literal 0 would OOM-kill every process instantly).
        memory = self._memory_max if memory_bytes is None else _fmt_bytes(memory_bytes)
        cpu = self._cpu_max if cpu_cores is None else _cpu_max(cpu_cores)
        pids = self._pids_max if pids_max is None else str(pids_max)
        (cg / "memory.max").write_text(memory)
        (cg / "cpu.max").write_text(cpu)
        (cg / "pids.max").write_text(pids)
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


def _run_chown(path: Path, uid: int) -> None:
    """Chown `path` to `uid`, leaving the gid unchanged (-1). The host runs as
    root (or with CAP_CHOWN) — the same power `_provision` already uses."""
    os.chown(path, uid, -1)


@dataclass(frozen=True)
class _Identity:
    uid: int
    gid: int
    cgroup: Path


# A seam for the one true system-binary boundary (`setfacl`): the default shells
# out; tests inject a spy so they need neither root nor the `acl` package.
AclRunner = Callable[[list[str]], None]
# A seam for the privileged `chown` in `_own` (#504): the default calls
# `os.chown`; tests inject a spy to assert the (path, uid) pairs non-root (the
# uid pool is pinned to the caller's own uid).
ChownRunner = Callable[[Path, int], None]


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
        chown_runner: ChownRunner | None = None,
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
        self._chown_runner: ChownRunner = chown_runner or _run_chown
        self._alloc_lock = asyncio.Lock()

    async def effective_limits(self, spec: SandboxSpec) -> EnforcedLimits:
        """What this sandbox will really be capped at — the cgroup manager's own
        answer, so the number published is the number written to `cpu.max`."""
        return self._cgroups.effective(spec.cpu_cores, spec.memory_bytes)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        async with self._alloc_lock:  # serialize uid allocation across handles
            handle = await super().create(spec)
            uid, gid = self._pool.alloc()
            ws = self._workspace(handle)
            cgroup = await asyncio.to_thread(
                functools.partial(
                    self._cgroups.create,
                    handle.id,
                    cpu_cores=spec.cpu_cores,
                    memory_bytes=spec.memory_bytes,
                    pids_max=spec.pids_max,
                )
            )
            await asyncio.to_thread(self._provision, ws, uid)
            self._identities[handle.id] = _Identity(uid=uid, gid=gid, cgroup=cgroup)
            return handle

    def _own(self, handle: SandboxHandle, target: Path) -> None:
        # #504: chown the just-written path AND every ancestor up to the
        # workspace root to the sandbox uid, so a newly-created nested path is
        # uid-owned end to end — not just the leaf (a mid-chain root-owned dir
        # still blocks git / rmdir, which check ownership, not the default ACL).
        # Chowning a component already owned by the uid is an idempotent no-op.
        # The loop only touches the workspace root and paths below it, never the
        # infra siblings (`.home`/`.tools`/`.ready`) or the shared root above.
        uid = self._identities[handle.id].uid
        workspace = self._workspace(handle)
        node = target
        while node == workspace or workspace in node.parents:
            self._chown_runner(node, uid)
            if node == workspace:
                break
            node = node.parent

    async def reown(self, handle: SandboxHandle) -> None:
        # #504: the host's bulk rsync restore (NfsArchive.restore) writes files
        # as root — no `-o` — bypassing per-write `_own`, so the restored tree
        # comes back root-owned. Recursively re-own the whole workspace to the
        # sandbox uid so git / chmod work inside. Idempotent (chowning an
        # already-uid file is a no-op); the per-write `_own` covers everything
        # that DOESN'T bypass the sandbox (upload / create_file / mkdir).
        await asyncio.to_thread(self._reown_sync, handle)

    def _reown_sync(self, handle: SandboxHandle) -> None:
        uid = self._identities[handle.id].uid
        workspace = self._workspace(handle)
        self._chown_runner(workspace, uid)
        for path in workspace.rglob("*"):
            self._chown_runner(path, uid)

    def _provision(self, workspace: Path, uid: int) -> None:
        # Own the workspace to the sandbox uid (gid left as-is via -1, so this
        # works non-root when uid == the caller) and lock it to the owner; the
        # default ACL keeps root-host-written files writable by the uid.
        os.chown(workspace, uid, -1)
        os.chmod(workspace, 0o700)
        self._acl_runner(_acl_argv(workspace, uid))
        # #393: the per-sandbox HOME (a workspace sibling) must be writable by
        # the dropped uid so the carrier launcher's HOME/caches + a user's `pip
        # --user` install land there. No default ACL — only the uid writes here.
        self._own_privately(workspace.parent / _HOME, uid)

    def _own_privately(self, path: Path, uid: int) -> None:
        """Hand one infra-area dir to the sandbox uid, 0700 — `.home` (#393) and
        the project venv (#775), both of which the dropped uid has to write and
        neither of which anyone else may read. Idempotent: chowning to the same
        uid is a no-op, which is what lets the exec path redo it."""
        os.chown(path, uid, -1)
        os.chmod(path, 0o700)

    def _ensure_venv(self, handle: SandboxHandle, root: Path) -> Path:
        """The base makes the dir; here it also has to be OWNED correctly.

        `uv sync` is the thing that fills it, and it runs as the sandbox uid via
        `setpriv` — so a directory this pod owns is one uv cannot write, and the
        profile's whole environment fails to build. Idempotent, like `.home`:
        chowning to the same derived uid is a no-op, which is what lets the exec
        path redo it every time."""
        venv = super()._ensure_venv(handle, root)
        self._own_privately(venv, self._identities[handle.id].uid)
        return venv

    def _ensure_home(self, handle: SandboxHandle, root: Path) -> Path:
        """The base makes the dir; here it also has to be OWNED correctly.

        A dir the exec path has to create belongs to this service's process,
        while the command that follows drops to the sandbox uid via `setpriv` —
        a plain `mkdir` would hand it a HOME it cannot write, which is the same
        failure from the permission side."""
        home = super()._ensure_home(handle, root)
        self._own_privately(home, self._identities[handle.id].uid)
        return home

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
        # #775: uv's download cache, BESIDE the sandboxes rather than inside
        # one. A reaped sandbox is rmtree'd whole, so a cache within it would
        # buy nothing — every cold start would re-fetch the whole stack, and a
        # failed sync stops the turn by design, so an index having a bad day
        # would stop everyone.
        #
        # Per uid, never shared. The uid is derived from the item and stable
        # across rebuilds, so this outlives the reap; and shared-and-writable
        # would be a cross-item hole rather than a saving — uv HARDLINKS cache
        # files into the venv, so one item could rewrite a file another is
        # executing, and the lock's hashes are checked at install time and
        # never again. (A shared cache is only safe read-only, which is a
        # deploy-time decision, not this.)
        #
        # Set for EVERY exec, not just the sync: a user's own `uv add` must
        # land in the same cache, or the second copy is pure waste.
        cache = self._root / _UV_CACHE / str(ident.uid)
        cache.mkdir(parents=True, exist_ok=True)
        self._chown_runner(cache, ident.uid)
        cache.chmod(0o700)
        env["UV_CACHE_DIR"] = str(cache)
        wrapped = _setpriv_cgroup_argv(argv, uid=ident.uid, gid=ident.gid, cgroup=ident.cgroup)
        return wrapped, cwd, env
