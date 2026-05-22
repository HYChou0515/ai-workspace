"""LocalProcessSandbox — runs commands as subprocesses on the host.

For trusted single-host deployments (e.g. running the whole app inside a VM
or devcontainer where the VM boundary is the actual sandbox). NOT for
multi-tenant or untrusted-user scenarios — there is no isolation between
the agent and the host.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

from .protocol import ExecResult, FileEntry, SandboxHandle, SandboxNotFound, SandboxSpec


class LocalProcessSandbox:
    def __init__(self, *, root_dir: Path | None = None) -> None:
        self._root = root_dir or Path(tempfile.gettempdir()) / "workspace-app-sandbox"
        self._root.mkdir(parents=True, exist_ok=True)
        self._dirs: dict[str, Path] = {}

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

    async def exec(self, handle: SandboxHandle, cmd: list[str]) -> ExecResult:
        cwd = self._require(handle)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
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
