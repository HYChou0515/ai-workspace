import uuid

from .protocol import ExecResult, SandboxHandle, SandboxNotFound, SandboxSpec


class MockSandbox:
    def __init__(self) -> None:
        self._fs: dict[str, dict[str, bytes]] = {}

    def _require(self, handle: SandboxHandle) -> dict[str, bytes]:
        if handle.id not in self._fs:
            raise SandboxNotFound(handle.id)
        return self._fs[handle.id]

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        self._fs[handle.id] = {}
        return handle

    async def kill(self, handle: SandboxHandle) -> None:
        self._require(handle)
        del self._fs[handle.id]

    async def exec(self, handle: SandboxHandle, cmd: list[str]) -> ExecResult:
        fs = self._require(handle)
        match cmd:
            case ["echo", *args]:
                text = " ".join(args)
                return ExecResult(exit_code=0, stdout=(text + "\n").encode())
            case ["cat", path]:
                if path not in fs:
                    return ExecResult(
                        exit_code=1,
                        stderr=f"cat: {path}: No such file or directory\n".encode(),
                    )
                return ExecResult(exit_code=0, stdout=fs[path])
            case ["false"]:
                return ExecResult(exit_code=1)
            case [name, *_]:
                return ExecResult(
                    exit_code=127,
                    stderr=f"mock: unknown command: {name}\n".encode(),
                )
            case _:
                return ExecResult(exit_code=127, stderr=b"mock: empty command\n")

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        fs = self._require(handle)
        fs[remote_path] = data

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        fs = self._require(handle)
        return fs[remote_path]
