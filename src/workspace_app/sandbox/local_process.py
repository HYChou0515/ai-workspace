"""LocalProcessSandbox — runs commands as subprocesses on the host.

For trusted single-host deployments (e.g. running the whole app inside a VM
or devcontainer). When unprivileged user namespaces are available (the
common case on modern Linux), `exec` runs each command inside a user+mount
namespace chrooted onto the sandbox directory, so that:

  * the workspace root is `/` — the agent's `/`-rooted file paths (the
    write_file/read_file convention) resolve the same way in the shell as
    they do via the FileStore tools; `python /script.py` just works.
  * the host filesystem is not reachable, and system dirs (`/usr`, `/etc`)
    are bind-mounted read-only so the agent can't tamper with the host.

Where user namespaces are unavailable it transparently falls back to a plain
subprocess in the sandbox directory (no isolation, absolute `/` paths hit the
real root) — set `isolate=False` to force this.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
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
mkdir -p "$ROOT/usr" "$ROOT/proc" "$ROOT/dev" "$ROOT/etc" "$ROOT/tmp"
mount --bind /usr "$ROOT/usr"; mount -o remount,bind,ro "$ROOT/usr"
mount --bind /etc "$ROOT/etc"; mount -o remount,bind,ro "$ROOT/etc"
for l in bin sbin lib lib64; do
  [ -L "$ROOT/$l" ] || [ -e "$ROOT/$l" ] || ln -s "usr/$l" "$ROOT/$l"
done
mount -t proc proc "$ROOT/proc" 2>/dev/null || true
mount -t tmpfs tmpfs "$ROOT/tmp" 2>/dev/null || true
for d in null zero full random urandom tty; do
  if [ -e "/dev/$d" ]; then : > "$ROOT/dev/$d"; mount --bind "/dev/$d" "$ROOT/dev/$d"; fi
done
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
exec /usr/sbin/chroot "$ROOT" "$@"
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


class LocalProcessSandbox:
    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        exec_timeout: float = 60.0,
        isolate: bool | None = None,
    ) -> None:
        self._root = root_dir or Path(tempfile.gettempdir()) / "workspace-app-sandbox"
        self._root.mkdir(parents=True, exist_ok=True)
        self._dirs: dict[str, Path] = {}
        # Hard cap on a single command — a hung/interactive program (vim,
        # top) is killed rather than blocking the request forever.
        self._exec_timeout = exec_timeout
        # Isolation: None → on iff the host supports unprivileged user
        # namespaces; otherwise honour the explicit choice.
        self._isolate = _userns_supported() if isolate is None else isolate

    def _require(self, handle: SandboxHandle) -> Path:
        path = self._dirs.get(handle.id)
        if path is None:
            raise SandboxNotFound(handle.id)
        return path

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        path = self._root / handle.id
        path.mkdir(parents=True, exist_ok=False)
        self._dirs[handle.id] = path
        return handle

    async def kill(self, handle: SandboxHandle) -> None:
        path = self._require(handle)
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        del self._dirs[handle.id]

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        cwd = self._require(handle)
        argv = _jail_argv(str(cwd), cmd) if self._isolate else cmd
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            # /dev/null stdin: a program reading input gets EOF instead of
            # blocking on a terminal it doesn't have.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Unbuffer Python so a long-running script's prints stream live to
            # on_output rather than sitting in a pipe buffer until it exits.
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        # stdout/stderr are PIPE above, so the StreamReaders are always present.
        assert proc.stdout is not None and proc.stderr is not None
        out_buf: list[bytes] = []
        err_buf: list[bytes] = []

        async def _pump(stream: asyncio.StreamReader, buf: list[bytes], sink: OutputSink | None):
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                buf.append(chunk)
                if sink is not None:
                    sink(chunk)

        readers = asyncio.gather(
            _pump(proc.stdout, out_buf, on_output),
            _pump(proc.stderr, err_buf, None),
        )
        timed_out = False
        try:
            await asyncio.wait_for(readers, self._exec_timeout)
            await proc.wait()
        except TimeoutError:
            timed_out = True
            readers.cancel()
            with contextlib.suppress(BaseException):
                await readers
            proc.kill()
            with contextlib.suppress(BaseException):
                await proc.wait()
        finally:
            # The jail leaves /dev device-node files behind (bind targets);
            # drop them so they don't reverse-sync into the workspace.
            if self._isolate:
                await asyncio.to_thread(shutil.rmtree, cwd / "dev", ignore_errors=True)

        stdout = b"".join(out_buf)
        if timed_out:
            # Keep the partial output the command produced before the kill.
            note = f"timed out after {self._exec_timeout:g}s and was killed\n".encode()
            return ExecResult(exit_code=124, stdout=stdout, stderr=b"".join(err_buf) + note)
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=b"".join(err_buf),
        )

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        cwd = self._require(handle)
        target = self._resolve(cwd, remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        cwd = self._require(handle)
        target = self._resolve(cwd, remote_path)
        return await asyncio.to_thread(target.read_bytes)

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        self._require(handle)
        return ("127.0.0.1", container_port)

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        cwd = self._require(handle)
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
            entries.append(FileEntry(path=f"/{rel}", size=stat.st_size, mtime=stat.st_mtime))
        return entries

    @staticmethod
    def _resolve(cwd: Path, remote_path: str) -> Path:
        # Treat absolute paths as relative-to-cwd so the agent can use
        # canonical-looking paths without escaping the sandbox.
        p = remote_path.lstrip("/")
        return cwd / p
