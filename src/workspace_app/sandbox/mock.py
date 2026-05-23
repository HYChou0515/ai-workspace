import uuid

from .protocol import ExecResult, FileEntry, SandboxHandle, SandboxNotFound, SandboxSpec


class MockSandbox:
    def __init__(self) -> None:
        self._fs: dict[str, dict[str, bytes]] = {}
        self._exposed: dict[str, list[int]] = {}

    def _require(self, handle: SandboxHandle) -> dict[str, bytes]:
        if handle.id not in self._fs:
            raise SandboxNotFound(handle.id)
        return self._fs[handle.id]

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        self._fs[handle.id] = {}
        self._exposed[handle.id] = list(spec.exposed_ports)
        return handle

    async def kill(self, handle: SandboxHandle) -> None:
        self._require(handle)
        del self._fs[handle.id]
        self._exposed.pop(handle.id, None)

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        self._require(handle)
        ports = self._exposed.setdefault(handle.id, [])
        if container_port not in ports:
            ports.append(container_port)
        return ("127.0.0.1", container_port)

    def exposed_ports(self, handle: SandboxHandle) -> list[int]:
        """Test-only spy: which ports has the agent asked to expose?"""
        self._require(handle)
        return list(self._exposed.get(handle.id, []))

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

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        fs = self._require(handle)
        prefix = root if root.endswith("/") else root + "/"
        if root == "/" or root == "":
            return [FileEntry(path=p, size=len(d)) for p, d in fs.items()]
        return [FileEntry(path=p, size=len(d)) for p, d in fs.items() if p.startswith(prefix)]
