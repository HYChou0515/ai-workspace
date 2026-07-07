"""The sandbox contract — host-side, standalone.

This is the sandbox-host's OWN copy of the data shapes and the backend
interface. It deliberately does NOT import anything from `workspace_app`: the
host is a separate service whose only obligation is to serve an HTTP API that
*happens* to satisfy the wire contract the workspace app's `HttpSandbox` client
expects (see `docs/sandbox-host-wire.md`). The Python `Sandbox` Protocol here is
purely the host's internal seam between its FastAPI shell (`app.py`) and its
backend (`IsolatedProcessSandbox` in production, `MockSandbox` in tests).

`expose_port` is intentionally absent: the host serves no in-sandbox
network-service path in v1, and the wire API exposes no such endpoint.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Sink for streaming a command's stdout/stderr as it arrives. `exec` calls it
# once per chunk so a long-running command's output can be surfaced live; the
# same bytes also end up in `ExecResult`.
OutputSink = Callable[[bytes], None]


class SandboxNotFound(LookupError):
    """Raised when an operation references a handle no sandbox owns — either it
    was never `create()`d or it was already `kill()`ed."""


@dataclass(frozen=True)
class SandboxHandle:
    """Opaque pointer to one live sandbox, local to this host process."""

    id: str


@dataclass(frozen=True)
class SandboxSpec:
    """Everything `create()` needs to provision a sandbox. `image` /
    `exposed_ports` are accepted for wire compatibility but ignored by the
    process-isolating backend (no containers, no in-sandbox network path)."""

    image: str = "python:3.12-slim"
    env: dict[str, str] | None = None
    exposed_ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one `exec`. A non-zero `exit_code` is a normal result, not an
    error. By convention `124` means the command hit a timeout and was killed."""

    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class FileEntry:
    """One regular file inside the sandbox, returned by `walk`. `path` is
    workspace-root-relative and starts with "/". `version` is an opaque
    change-stamp (mtime:size) that differs iff the content may have changed."""

    path: str
    size: int
    version: str = ""


class Sandbox(Protocol):
    """The host's internal backend interface — the 13 operations its HTTP shell
    proxies. Implemented by `IsolatedProcessSandbox` (production) and
    `MockSandbox` (tests)."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...
    async def kill(self, handle: SandboxHandle) -> None: ...
    # #492: the sandbox's LOCAL working dir on this pod's disk — the source/target
    # of the host's rsync to/from the durable NFS archive. Raises SandboxNotFound
    # for an unknown handle.
    def workspace_dir(self, handle: SandboxHandle) -> Path: ...
    async def exec(
        self, handle: SandboxHandle, cmd: list[str], on_output: OutputSink | None = None
    ) -> ExecResult: ...
    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None: ...
    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes: ...
    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]: ...
    async def exists(self, handle: SandboxHandle, path: str) -> bool: ...
    # #366: readiness — an out-of-workspace marker; the app's mirror only trusts
    # deletions while `is_ready` holds. Never appears in walk (not a workspace file).
    async def mark_ready(self, handle: SandboxHandle) -> None: ...
    async def is_ready(self, handle: SandboxHandle) -> bool: ...
    async def delete(self, handle: SandboxHandle, path: str) -> None: ...
    async def mkdir(self, handle: SandboxHandle, path: str) -> None: ...
    async def rmdir(self, handle: SandboxHandle, path: str) -> None: ...
    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None: ...
