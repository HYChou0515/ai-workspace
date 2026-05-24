"""Sandbox Protocol — the contract every execution backend must satisfy.

A Sandbox is an isolated place to run the agent's / user's shell commands and
hold a working copy of the workspace files. Implementations: `MockSandbox`
(in-memory, tests), `LocalProcessSandbox` (subprocess + temp dir, optionally
user-namespace-jailed), `DockerSandbox` (one container per handle).

Conventions shared by all methods:

- **Handles**: `create()` returns a `SandboxHandle`; every other method takes
  one. An unknown handle (never created, or already `kill()`ed) raises
  `SandboxNotFound`.
- **Paths** are POSIX, rooted at the workspace root. A leading `/` means "the
  workspace root", NOT the host root — e.g. `/data/x.csv` is `data/x.csv`
  inside the sandbox. Implementations resolve `/`-paths to the sandbox's
  working directory (a chroot, a container WORKDIR, or a temp dir).
- **Async**: every method is a coroutine; blocking work is offloaded
  (e.g. `asyncio.to_thread`) so the event loop isn't stalled.

To write a new backend, implement every method below honouring the docstring
contracts; nothing else in the app needs to change (it's injected via
`create_app(sandbox=...)`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

# Sink for streaming a command's stdout as it arrives. `exec` calls it once per
# chunk (typically a line / a read() block) so a long-running command's output
# can be surfaced live; the same bytes also end up in `ExecResult.stdout`.
OutputSink = Callable[[bytes], None]


class SandboxNotFound(LookupError):
    """Raised when an operation references a handle no sandbox owns — either it
    was never `create()`d or it was already `kill()`ed."""


@dataclass(frozen=True)
class SandboxHandle:
    """Opaque pointer to one live sandbox. `id` is unique per `create()`; do
    not parse it — treat it as a token to pass back to the other methods."""

    id: str


@dataclass(frozen=True)
class SandboxSpec:
    """Everything `create()` needs to provision a sandbox."""

    image: str = "python:3.12-slim"
    """Container image (DockerSandbox). Ignored by backends that don't use
    images (LocalProcessSandbox runs on the host's interpreters)."""

    env: dict[str, str] | None = None
    """Extra environment variables for commands run in the sandbox."""

    exposed_ports: tuple[int, ...] = ()
    """In-sandbox TCP ports that must be reachable from the backend, declared
    **up front** because some backends (Docker) can't publish a port on an
    already-running container. Leave empty (the default) when nothing inside
    the sandbox needs to be reached over the network — then `expose_port` is
    simply never called. See `Sandbox.expose_port`."""


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one `exec`. A non-zero `exit_code` is a normal result, not an
    error — `exec` only raises for an unknown handle, never for a command that
    ran and failed."""

    exit_code: int
    """Process exit status. By convention `124` means the command hit the
    backend's wall-clock timeout and was killed."""

    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class FileEntry:
    """One regular file inside the sandbox, returned by `Sandbox.walk`.

    `path` is workspace-root-relative and starts with "/", so it round-trips
    with FileStore keys without further normalization. `mtime` is the file's
    last-modified epoch seconds when the adapter has it; 0 if unknown
    (e.g. MockSandbox)."""

    path: str
    size: int
    mtime: float = 0.0


class Sandbox(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        """Provision a fresh, empty sandbox and return its handle. Each handle
        has its own isolated filesystem (no sharing between handles). Any
        `spec.exposed_ports` must be arranged here (e.g. Docker publishes them
        at container start) — they cannot be added later."""
        ...

    async def kill(self, handle: SandboxHandle) -> None:
        """Tear the sandbox down and release its resources (temp dir /
        container). The handle is invalid afterwards — further calls with it
        raise `SandboxNotFound`. Idempotency is not required."""
        ...

    async def exec(
        self, handle: SandboxHandle, cmd: list[str], on_output: OutputSink | None = None
    ) -> ExecResult:
        """Run `cmd` (an argv list — NOT a shell string; use
        `["sh", "-c", "..."]` if you need shell features) with the workspace
        root as the working directory, and return its `ExecResult`.

        Contract:
        - stdin is `/dev/null` (a program reading input gets EOF, never hangs).
        - A non-zero exit is returned in `exit_code`, not raised.
        - An unknown handle raises `SandboxNotFound`.
        - If `on_output` is given, it is called with stdout byte chunks as they
          arrive (live streaming); the complete stdout is still in the result.
        - Implementations SHOULD bound runtime with a wall-clock timeout; on
          timeout, kill the process and return `exit_code=124` while preserving
          whatever stdout was captured before the kill (don't discard it)."""
        ...

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        """Write `data` to `remote_path` (workspace-root-relative) in the
        sandbox, creating parent directories as needed. Overwrites an existing
        file. Used by SandboxSync to push FileStore writes in before `exec`."""
        ...

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        """Read and return the bytes of `remote_path` (workspace-root-relative).
        Raises `FileNotFoundError` if it doesn't exist. Used by SandboxSync to
        pull sandbox changes back into the FileStore."""
        ...

    async def walk(self, handle: SandboxHandle, root: str) -> list[FileEntry]:
        """List every **regular file** under `root` (recursive), as `FileEntry`
        with `/`-rooted paths. Directories and symlinks are excluded (only real
        files round-trip to the FileStore). `root` is workspace-root-relative;
        "/" walks the whole workspace."""
        ...

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        """Map a TCP port **listening inside the sandbox** to an address the
        backend process can connect to, returned as `(host, port)`. This does
        NOT open the port — the in-sandbox service must already be listening.

        - Network-isolated backends (Docker): `container_port` must have been
          declared in `SandboxSpec.exposed_ports` at `create()` time; raise
          `ValueError` if it wasn't (you can't publish a port post-hoc). Return
          the published host-side `(host, port)`.
        - Backends with no network isolation (LocalProcessSandbox): the
          in-sandbox port IS the same port on the host — return
          `("127.0.0.1", container_port)` unchanged.

        If a sandbox needs no in-sandbox services reached over the network,
        leave `exposed_ports` empty and never call this. (Reserved for the v2
        "kernel inside the sandbox" path; v1 spawns the Jupyter kernel on the
        host and doesn't use it.)"""
        ...
