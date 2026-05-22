from dataclasses import dataclass
from typing import Protocol


class SandboxNotFound(LookupError):
    """Raised when an operation references a handle that no sandbox owns."""


@dataclass(frozen=True)
class SandboxHandle:
    id: str


@dataclass(frozen=True)
class SandboxSpec:
    image: str = "python:3.12-slim"
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


class Sandbox(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...
    async def kill(self, handle: SandboxHandle) -> None: ...
    async def exec(self, handle: SandboxHandle, cmd: list[str]) -> ExecResult: ...
    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None: ...
    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes: ...
