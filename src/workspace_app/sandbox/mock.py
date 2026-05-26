import hashlib
import uuid

from .protocol import (
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)


def _version(data: bytes) -> str:
    """Content hash — exact for the in-memory store, stateless, and changes
    iff the bytes change (so a same-content re-upload doesn't churn)."""
    return hashlib.sha256(data).hexdigest()[:16]


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

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        fs = self._require(handle)
        result = self._exec_result(fs, cmd)
        # Stream the (whole) stdout to the sink in one shot — enough for tests
        # that assert live output is forwarded.
        if on_output is not None and result.stdout:
            on_output(result.stdout)
        return result

    @staticmethod
    def _exec_result(fs: dict[str, bytes], cmd: list[str]) -> ExecResult:
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
        if remote_path not in fs:
            raise FileNotFoundError(remote_path)
        return fs[remote_path]

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        fs = self._require(handle)
        prefix = root if root.endswith("/") else root + "/"
        if root in ("/", ""):
            items = list(fs.items())
        else:
            items = [(p, d) for p, d in fs.items() if p.startswith(prefix)]
        return [FileEntry(path=p, size=len(d), version=_version(d)) for p, d in items]

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        return path in self._require(handle)

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        fs = self._require(handle)
        if path not in fs:
            raise FileNotFoundError(path)
        del fs[path]

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        # The flat store has only implicit dirs (via file paths) and the Sandbox
        # Protocol exposes no is_dir/listdir, so an empty dir is unobservable —
        # validate the handle and no-op. Real backends create it for real.
        self._require(handle)

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        fs = self._require(handle)
        base = path.rstrip("/")
        prefix = base + "/"
        victims = [p for p in fs if p == base or p.startswith(prefix)]
        if not victims:
            raise FileNotFoundError(path)
        for p in victims:
            del fs[p]

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        fs = self._require(handle)
        s, d = src.rstrip("/"), dst.rstrip("/")
        if s in fs:  # single file
            fs[d] = fs.pop(s)
            return
        prefix = s + "/"
        moved = [p for p in fs if p.startswith(prefix)]
        if not moved:
            raise FileNotFoundError(src)
        for p in moved:
            fs[d + p[len(s) :]] = fs.pop(p)
