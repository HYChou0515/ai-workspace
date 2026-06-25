"""LocalProcessSandbox — runs commands as subprocesses on the host.

For trusted single-host deployments (e.g. running the whole app inside a VM
or devcontainer). When unprivileged user namespaces are available (the
common case on modern Linux), `exec` runs each command inside a user+mount
namespace chrooted onto the sandbox directory, so that:

  * the user **workspace is `/root`** — the agent's cwd and `$HOME` (`~`). File
    ops + `walk` are scoped here. The sandbox root (the chroot `/`) is the
    **infra area**: system overlays + provisioned tools live there, OUTSIDE the
    workspace, so they're never walked, synced, or shown in the file tree.
  * the host filesystem is not reachable, and system dirs (`/usr`, `/etc`)
    are bind-mounted read-only so the agent can't tamper with the host.

Where user namespaces are unavailable it transparently falls back to a plain
subprocess in the workspace subdir (no isolation) — set `isolate=False` to
force this.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from functools import cache
from pathlib import Path

from .protocol import (
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)

# Bootstrap run (as namespace-root) before chroot: overlay the host's system
# dirs read-only onto the sandbox root, wire up a usable /dev + ephemeral
# /tmp, then chroot in and exec the user command. $1 is the jail root; the
# remaining args are the command. Device nodes are bind-mounted onto plain
# files (an unprivileged tmpfs is `nodev`, so nodes there can't be opened);
# the resulting /dev files are cleaned up by `exec` afterwards.
_JAIL_BOOTSTRAP = r"""
ROOT="$1"; shift
mkdir -p "$ROOT/usr" "$ROOT/proc" "$ROOT/dev" "$ROOT/etc" "$ROOT/tmp" "$ROOT/root"
mount --bind /usr "$ROOT/usr"; mount -o remount,bind,ro "$ROOT/usr"
mount --bind /etc "$ROOT/etc"; mount -o remount,bind,ro "$ROOT/etc"
# Provisioned tools: a shared host dir bind-mounted read-only at /.tools (a
# sibling of /root, so it's outside the workspace and never walked/synced).
if [ -n "$SANDBOX_TOOLS_DIR" ]; then
  mkdir -p "$ROOT/.tools"
  mount --bind "$SANDBOX_TOOLS_DIR" "$ROOT/.tools"; mount -o remount,bind,ro "$ROOT/.tools"
fi
for l in bin sbin lib lib64; do
  [ -L "$ROOT/$l" ] || [ -e "$ROOT/$l" ] || ln -s "usr/$l" "$ROOT/$l"
done
mount -t proc proc "$ROOT/proc" 2>/dev/null || true
mount -t tmpfs tmpfs "$ROOT/tmp" 2>/dev/null || true
for d in null zero full random urandom tty; do
  if [ -e "/dev/$d" ]; then : > "$ROOT/dev/$d"; mount --bind "/dev/$d" "$ROOT/dev/$d"; fi
done
# `python` shim selection. Two-tier:
#   1. If the `python-stack` venv carrier was provisioned (its prebuilt
#      bundle bind-mounted at /.tools/python-stack with the data-science
#      stack inside .venv/), prefer its launcher — the agent's raw
#      `exec(["python", "script.py"])` then sees pandas / numpy / scipy /
#      matplotlib for free, matching the SOP's "sandbox preinstalled
#      data stack" promise without depending on the host's site-packages.
#   2. Otherwise fall back to /usr/bin/python3 from the bind-mounted /usr.
#      (A Debian host's /usr/bin/python is often the legacy python2
#      symlink, so we always shim explicitly rather than inherit it.)
# The shim lives on the ephemeral tmpfs so it never touches the workspace.
mkdir -p "$ROOT/tmp/.jailbin"
# Shim BOTH `python` and `python3` (and the major-minor flavour names the
# tools' bundled launchers might use): agents commonly type `python3 -` in
# heredocs, and a bare `python` shim alone would let `python3` fall through
# to /usr/bin/python3 — the host Python with no pandas/numpy/scipy/matplotlib.
if [ -x "$ROOT/.tools/python-stack/launch" ]; then
  for n in python python3 python3.10 python3.11 python3.12 python3.13; do
    ln -sf /.tools/python-stack/launch "$ROOT/tmp/.jailbin/$n"
  done
elif [ -e /usr/bin/python3 ]; then
  ln -sf /usr/bin/python3 "$ROOT/tmp/.jailbin/python"
  ln -sf /usr/bin/python3 "$ROOT/tmp/.jailbin/python3"
fi
export PATH="/tmp/.jailbin:/usr/bin:/bin:/usr/sbin:/sbin"
# IMPORTANT — login-shell PATH guard. The agent commonly runs commands as
# `bash -lc "python3 -c …"`; the `-l` makes bash source /etc/profile, which
# on Debian/Ubuntu hard-resets PATH to "/usr/local/sbin:/usr/local/bin:..."
# and silently drops our /tmp/.jailbin first-on-PATH shim. The result was
# ModuleNotFoundError on pandas because python3 then resolved to the host's
# /usr/bin/python3 instead of our python-stack launcher. Overlay a tmpfs on
# /etc/profile.d and drop a single script that re-prepends /tmp/.jailbin
# so login shells inherit the shim. /etc is bind-mounted read-only from the
# host, so we can't write into the real /etc/profile.d — the tmpfs overlay
# is the workaround. Host's profile.d scripts are shadowed inside the jail,
# which is fine (we don't need ssh-agent/locale-config setup in a sandbox).
mount -t tmpfs tmpfs "$ROOT/etc/profile.d" 2>/dev/null || true
cat > "$ROOT/etc/profile.d/jailbin.sh" <<'PROFILED'
PATH="/tmp/.jailbin:$PATH"
export PATH
PROFILED
chmod 644 "$ROOT/etc/profile.d/jailbin.sh"
# The workspace is /root (the agent's ~/cwd); the sandbox root holds infra
# (system overlays, provisioned tools) that the workspace walk never sees.
exec /usr/sbin/chroot "$ROOT" /bin/sh -ec 'cd /root; export HOME=/root; exec "$@"' sh "$@"
"""


def _jail_argv(root: str, cmd: list[str]) -> list[str]:
    """Wrap `cmd` so it runs inside an unprivileged user+mount namespace
    chrooted onto `root`. `--kill-child` makes a SIGKILL of the wrapper tear
    down the jailed process too (so exec timeouts still reap it)."""
    return [
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--fork",
        "--kill-child",
        "/bin/sh",
        "-ec",
        _JAIL_BOOTSTRAP,
        "sh",
        root,
        *cmd,
    ]


@cache
def _userns_supported() -> bool:
    """True if this host allows unprivileged user namespaces (so the jail
    can be built without root). Cached — the answer can't change at runtime."""
    try:
        proc = subprocess.run(
            ["unshare", "--user", "--map-root-user", "true"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# The user workspace is this subdir of the sandbox root (the agent's ~/cwd).
# MUST match the `/root` the jail bootstrap cds into.
_WORKSPACE = "root"
# Provisioned tools are made available here (a sibling of the workspace, so
# they're outside what walk/sync see). MUST match the jail bootstrap's mount.
_TOOLS = ".tools"


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the command's whole process group, then leave reaping to the
    caller. `start_new_session=True` at spawn made the child a group leader, so
    its pid IS the pgid — killing the group takes down any backgrounded
    grandchildren too (#74). A `ProcessLookupError` just means the group
    already exited between wait and kill — nothing left to do."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


class LocalProcessSandbox:
    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        exec_timeout: float = 60.0,
        log_timeout: float = 60.0,
        isolate: bool | None = None,
        tools_dir: Path | None = None,
    ) -> None:
        self._root = root_dir or Path(tempfile.gettempdir()) / "workspace-app-sandbox"
        self._root.mkdir(parents=True, exist_ok=True)
        # Shared, prebuilt provisioned-tools dir, made available at /.tools
        # (outside the workspace): read-only bind-mount when jailed, symlink when
        # not. One shared dir for all sandboxes — no per-sandbox copy.
        self._tools_dir = tools_dir
        self._dirs: dict[str, Path] = {}
        # Two peer timeouts (#70), each a hard cap; 0 disables that one:
        #   exec_timeout — TOTAL wall-clock for the command (the original cap).
        #   log_timeout  — IDLE cap: kill if no stdout/stderr output for this
        #                  long (a long job that hangs stops emitting logs). A
        #                  long job sets exec_timeout=0 and relies on log_timeout.
        self._exec_timeout = exec_timeout
        self._log_timeout = log_timeout
        # Isolation: None → on iff the host supports unprivileged user
        # namespaces; otherwise honour the explicit choice.
        self._isolate = _userns_supported() if isolate is None else isolate

    def _require(self, handle: SandboxHandle) -> Path:
        """The sandbox root — the chroot root / infra area (system overlays,
        provisioned tools). The user workspace is the `_workspace` subdir."""
        path = self._dirs.get(handle.id)
        if path is None:
            raise SandboxNotFound(handle.id)
        return path

    def _workspace(self, handle: SandboxHandle) -> Path:
        """The user workspace — a subdir of the sandbox root (the agent's
        `~`/cwd). File ops + walk are scoped here, so tools/caches living in the
        sandbox root (the infra area, outside this) are never seen or synced."""
        return self._require(handle) / _WORKSPACE

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        path = self._root / handle.id
        # Create the workspace subdir (and its parent, the sandbox/infra root).
        (path / _WORKSPACE).mkdir(parents=True, exist_ok=False)
        # Unjailed: expose the shared tools dir via a symlink (jailed uses a
        # read-only bind-mount, set up per-exec in the bootstrap instead).
        if self._tools_dir is not None and not self._isolate:
            (path / _TOOLS).symlink_to(self._tools_dir)
        self._dirs[handle.id] = path
        return handle

    async def kill(self, handle: SandboxHandle) -> None:
        path = self._require(handle)
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        del self._dirs[handle.id]

    def _exec_argv(
        self, handle: SandboxHandle, cmd: list[str]
    ) -> tuple[list[str], Path, dict[str, str]]:
        """Build the `(argv, cwd, env)` for one command. The seam subclasses
        override to wrap `cmd` (e.g. `IsolatedProcessSandbox` prepends a
        `setpriv` + cgroup-join wrapper); the exec pump/timeout machinery below
        stays shared. Validates the handle (raises `SandboxNotFound`)."""
        root = self._require(handle)
        ws = root / _WORKSPACE
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        if self._isolate:
            # chroot onto the sandbox root; the bootstrap cds into /root + sets
            # HOME. The subprocess cwd is the root (the unshare wrapper runs there).
            argv = _jail_argv(str(root), cmd)
            sub_cwd = root
            # The bootstrap read-only bind-mounts this at /.tools (outside the
            # workspace) when set.
            if self._tools_dir is not None:
                env["SANDBOX_TOOLS_DIR"] = str(self._tools_dir)
        else:
            # No chroot: run directly in the workspace subdir, HOME → workspace.
            argv = cmd
            sub_cwd = ws
            env["HOME"] = str(ws)
        return argv, sub_cwd, env

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        argv, sub_cwd, env = self._exec_argv(handle, cmd)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=sub_cwd,
                # /dev/null stdin: a program reading input gets EOF instead of
                # blocking on a terminal it doesn't have.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Unbuffer Python so a long-running script's prints stream live to
                # on_output rather than sitting in a pipe buffer until it exits.
                env=env,
                # New session ⇒ the child leads its own process group (pgid==pid),
                # so a timeout / cancel can SIGKILL the WHOLE group — the command
                # plus any backgrounded grandchildren — instead of orphaning them
                # in the background (#74).
                start_new_session=True,
            )
        except FileNotFoundError:
            # `create_subprocess_exec` raises when the binary is missing.
            # Protocol contract says "non-zero exit returned, not raised", so
            # translate to POSIX-standard exit 127 (command not found) with a
            # stderr message — the /exec endpoint and the agent's exec tool
            # then see a normal command failure, not a 500.
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=f"{cmd[0]}: command not found\n".encode(),
            )
        except PermissionError as exc:
            # The binary exists but isn't executable (no x-bit, or the jail
            # blocks it). POSIX exit 126 = "found but not executable".
            return ExecResult(
                exit_code=126,
                stdout=b"",
                stderr=f"{cmd[0]}: {exc.strerror or 'permission denied'}\n".encode(),
            )
        # stdout/stderr are PIPE above, so the StreamReaders are always present.
        assert proc.stdout is not None and proc.stderr is not None
        out_buf: list[bytes] = []
        err_buf: list[bytes] = []
        loop = asyncio.get_running_loop()
        start = loop.time()
        last_output = start  # bumped on every chunk; drives the idle (#70) timeout

        async def _pump(stream: asyncio.StreamReader, buf: list[bytes], sink: OutputSink | None):
            nonlocal last_output
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                last_output = loop.time()
                buf.append(chunk)
                if sink is not None:
                    sink(chunk)

        readers = asyncio.gather(
            _pump(proc.stdout, out_buf, on_output),
            # stderr streams to the same live sink (issue #23) — progress bars /
            # warnings / logs a tool writes to stderr show up live, not just at
            # the end. The result still keeps stdout/stderr separate.
            _pump(proc.stderr, err_buf, on_output),
        )

        async def _terminate() -> None:
            """Stop the pump tasks and SIGKILL the command's whole process
            group, then reap. Shared by the timeout and cancel paths."""
            readers.cancel()
            with contextlib.suppress(BaseException):
                await readers
            _kill_process_group(proc)
            with contextlib.suppress(BaseException):
                await proc.wait()

        async def _watchdog() -> str:
            """Return which deadline tripped: `exec` (total wall-clock) or `log`
            (idle — no output for log_timeout). Parks forever if both are
            disabled (0); the readers-vs-watchdog race below ends it when the
            command exits on its own. Re-checks after each sleep so output that
            arrives mid-wait pushes the idle deadline back (#70)."""
            while True:
                now = loop.time()
                waits: list[float] = []
                if self._exec_timeout > 0:
                    waits.append(self._exec_timeout - (now - start))
                if self._log_timeout > 0:
                    waits.append(self._log_timeout - (now - last_output))
                # Both timeouts disabled (0) ⇒ no deadline; park and re-check
                # (the readers-vs-watchdog race ends this when the command exits).
                delay = min(waits) if waits else 3600.0
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                now = loop.time()
                if self._exec_timeout > 0 and now - start >= self._exec_timeout:
                    return "exec"
                return "log"

        watchdog = asyncio.create_task(_watchdog())
        timed_out: str | None = None
        try:
            done, _ = await asyncio.wait({readers, watchdog}, return_when=asyncio.FIRST_COMPLETED)
            if readers in done:
                await proc.wait()  # both streams hit EOF ⇒ the process exited
            else:
                timed_out = watchdog.result()  # a deadline tripped
                await _terminate()
        except asyncio.CancelledError:
            # #74: when the awaiting turn is stopped, take the running command
            # (and any grandchildren it spawned) down with it — don't leave it
            # running in the background. Then re-raise so cancellation propagates.
            await _terminate()
            raise
        finally:
            watchdog.cancel()
            with contextlib.suppress(BaseException):
                await watchdog
            # The jail leaves /dev device-node files behind (bind targets) at
            # the sandbox root; drop them. (They're outside the workspace now,
            # so they wouldn't reverse-sync anyway — belt and suspenders.) In the
            # isolate path `sub_cwd` IS the sandbox root.
            if self._isolate:
                await asyncio.to_thread(shutil.rmtree, sub_cwd / "dev", ignore_errors=True)

        stdout = b"".join(out_buf)
        if timed_out is not None:
            # Keep the partial output the command produced before the kill.
            if timed_out == "exec":
                note = f"timed out after {self._exec_timeout:g}s (total) and was killed\n"
            else:
                note = f"no output for {self._log_timeout:g}s; assumed hung and killed\n"
            return ExecResult(
                exit_code=124, stdout=stdout, stderr=b"".join(err_buf) + note.encode()
            )
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=b"".join(err_buf),
        )

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, remote_path)
        return await asyncio.to_thread(target.read_bytes)

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        cwd = self._workspace(handle)
        return await asyncio.to_thread(self._resolve(cwd, path).is_file)

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        if not await asyncio.to_thread(target.is_file):
            raise FileNotFoundError(path)
        await asyncio.to_thread(target.unlink)

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        await asyncio.to_thread(lambda: target.mkdir(parents=True, exist_ok=True))

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        if not await asyncio.to_thread(target.is_dir):
            raise FileNotFoundError(path)
        await asyncio.to_thread(shutil.rmtree, target)

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        cwd = self._workspace(handle)
        s, d = self._resolve(cwd, src), self._resolve(cwd, dst)
        if not await asyncio.to_thread(s.exists):
            raise FileNotFoundError(src)
        await asyncio.to_thread(lambda: d.parent.mkdir(parents=True, exist_ok=True))
        await asyncio.to_thread(s.rename, d)

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        self._require(handle)
        return ("127.0.0.1", container_port)

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        cwd = self._workspace(handle)
        base = self._resolve(cwd, root) if root.strip("/") else cwd
        return await asyncio.to_thread(self._walk_sync, cwd, base)

    @staticmethod
    def _walk_sync(cwd: Path, base: Path) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(cwd).as_posix()
            stat = p.stat()
            # mtime(ns)+size — cheap, no read; ns granularity avoids same-second collisions.
            version = f"{stat.st_mtime_ns}-{stat.st_size}"
            entries.append(FileEntry(path=f"/{rel}", size=stat.st_size, version=version))
        return entries

    @staticmethod
    def _resolve(cwd: Path, remote_path: str) -> Path:
        # Treat absolute paths as relative-to-cwd so the agent can use
        # canonical-looking paths without escaping the sandbox.
        p = remote_path.lstrip("/")
        return cwd / p
