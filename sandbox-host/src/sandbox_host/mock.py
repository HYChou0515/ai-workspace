"""In-memory `Sandbox` used by the host's wire-server tests — no real
subprocess/uid/cgroup needed to exercise `app.py`'s routing + error mapping."""

import hashlib
import uuid
from collections.abc import Mapping
from pathlib import Path

from .protocol import (
    EnforcedLimits,
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
    WalkResult,
)


def _parent(path: str) -> str:
    """The directory holding `path` ("" when it sits at the workspace root)."""
    return path.rstrip("/").rsplit("/", 1)[0]


def _version(data: bytes) -> str:
    """Content hash — exact for the in-memory store, stateless, and changes
    iff the bytes change (so a same-content re-upload doesn't churn)."""
    return hashlib.sha256(data).hexdigest()[:16]


class MockSandbox:
    def __init__(self, *, cpu_cores: float | None = None, memory_bytes: int | None = None) -> None:
        # What this stand-in claims to enforce when a spec states nothing.
        # None = "caps nothing", the truth for a mock; a test standing in for a
        # real backend (all of which DO cap) passes the ceilings it models.
        self._cpu_cores = cpu_cores
        self._memory_bytes = memory_bytes
        # One entry per `exec`, in call order — what the caller asked to add.
        self.exec_envs: list[dict[str, str]] = []
        self._fs: dict[str, dict[str, bytes]] = {}
        # Directories, tracked explicitly rather than implied by the file paths:
        # an empty one implies nothing, and it is exactly the case that broke.
        # A real filesystem keeps a directory after its last file is deleted, so
        # entries here are removed only by `rmdir`/`rename`, never by `delete`.
        self._dirs: dict[str, set[str]] = {}
        # #366: readiness kept outside the file store so it never shows in walk.
        self._ready: set[str] = set()
        # Kept outside the file store like `_ready`: on a real backend the
        # user-env file sits beside the workspace, never inside it.
        self._user_env: dict[str, str] = {}
        # #504: records handle ids the controller asked to reown (post-restore),
        # so wiring tests can assert the chown-the-restored-tree step ran.
        self.reowned: list[str] = []

    def _require(self, handle: SandboxHandle) -> dict[str, bytes]:
        if handle.id not in self._fs:
            raise SandboxNotFound(handle.id)
        return self._fs[handle.id]

    async def effective_limits(self, spec: SandboxSpec) -> EnforcedLimits:
        return EnforcedLimits(
            cpu_cores=self._cpu_cores if spec.cpu_cores is None else spec.cpu_cores,
            memory_bytes=self._memory_bytes if spec.memory_bytes is None else spec.memory_bytes,
        )

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        self._fs[handle.id] = {}
        return handle

    def workspace_dir(self, handle: SandboxHandle) -> Path:
        # #492: a nominal per-handle path — the in-memory mock has no real dir,
        # but the host controller only passes it to the (fake) archive in tests.
        self._require(handle)
        return Path("/mock-sandbox") / handle.id / "root"

    async def kill(self, handle: SandboxHandle) -> None:
        self._require(handle)
        del self._fs[handle.id]
        self._dirs.pop(handle.id, None)
        self._ready.discard(handle.id)
        self._user_env.pop(handle.id, None)

    async def reown(self, handle: SandboxHandle) -> None:
        # #504: no real ownership in-memory; just record that it was requested.
        self._require(handle)
        self.reowned.append(handle.id)

    async def mark_ready(self, handle: SandboxHandle) -> None:
        self._require(handle)
        self._ready.add(handle.id)

    async def is_ready(self, handle: SandboxHandle) -> bool:
        self._require(handle)
        return handle.id in self._ready

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        fs = self._require(handle)
        # Recorded, not applied: there is no process here. Swallowing it would
        # let the wire test pass while the real hop dropped it.
        self.exec_envs.append(dict(env) if env else {})
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
        self._register_dirs(handle, _parent(remote_path))

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        fs = self._require(handle)
        if remote_path not in fs:
            raise FileNotFoundError(remote_path)
        return fs[remote_path]

    async def walk(self, handle: SandboxHandle, root: str) -> WalkResult:
        fs = self._require(handle)
        dirs = self._dirs.setdefault(handle.id, set())
        prefix = root if root.endswith("/") else root + "/"
        if root in ("/", ""):
            items = list(fs.items())
            under = sorted(dirs)
        else:
            items = [(p, d) for p, d in fs.items() if p.startswith(prefix)]
            under = sorted(p for p in dirs if p.startswith(prefix))
        return WalkResult(
            files=[FileEntry(path=p, size=len(d), version=_version(d)) for p, d in items],
            dirs=under,
        )

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        return path in self._require(handle)

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        fs = self._require(handle)
        if path not in fs:
            raise FileNotFoundError(path)
        del fs[path]

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        # Directories are tracked for real. This used to be a no-op, on the
        # grounds that a flat store has only the dirs its file paths imply — but
        # that made an EMPTY dir inexpressible here, so no test using this double
        # could observe the one case the real backends get asked about.
        self._require(handle)
        self._register_dirs(handle, path.rstrip("/"))

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        fs = self._require(handle)
        dirs = self._dirs.setdefault(handle.id, set())
        base = path.rstrip("/")
        prefix = base + "/"
        victims = [p for p in fs if p == base or p.startswith(prefix)]
        gone = {p for p in dirs if p == base or p.startswith(prefix)}
        if not victims and not gone:
            raise FileNotFoundError(path)
        for p in victims:
            del fs[p]
        dirs -= gone

    def _register_dirs(self, handle: SandboxHandle, path: str) -> None:
        """Record `path` and every ancestor as directories, like `mkdir -p`."""
        dirs = self._dirs.setdefault(handle.id, set())
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            seg = "/" + "/".join(parts[:i])
            if seg != "/":
                dirs.add(seg)

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        fs = self._require(handle)
        dirs = self._dirs.setdefault(handle.id, set())
        s, d = src.rstrip("/"), dst.rstrip("/")
        if s in fs:  # single file
            fs[d] = fs.pop(s)
            self._register_dirs(handle, _parent(d))
            return
        prefix = s + "/"
        moved = [p for p in fs if p.startswith(prefix)]
        moved_dirs = {p for p in dirs if p == s or p.startswith(prefix)}
        if not moved and not moved_dirs:
            raise FileNotFoundError(src)
        for p in moved:
            fs[d + p[len(s) :]] = fs.pop(p)
        dirs -= moved_dirs
        for p in moved_dirs:
            self._register_dirs(handle, d + p[len(s) :])
