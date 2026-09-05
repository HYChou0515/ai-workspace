"""Sandbox Protocol — the contract every execution backend must satisfy.

A Sandbox is an isolated place to run the agent's / user's shell commands and
hold a working copy of the workspace files. Implementations: `MockSandbox`
(in-memory, tests), `LocalProcessSandbox` (subprocess + temp dir, optionally
user-namespace-jailed), `DockerSandbox` (one container per handle), `HttpSandbox`
(client to a separate sandbox host pod — see `docs/sandbox-host.md`; the host
wraps an `IsolatedProcessSandbox` that isolates each handle by uid + cgroup).

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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Sink for streaming a command's stdout as it arrives. `exec` calls it once per
# chunk (typically a line / a read() block) so a long-running command's output
# can be surfaced live; the same bytes also end up in `ExecResult.stdout`.
OutputSink = Callable[[bytes], None]


class SandboxNotFound(LookupError):
    """Raised when an operation references a handle no sandbox owns — either it
    was never `create()`d or it was already `kill()`ed."""


class SandboxBusy(RuntimeError):
    """Raised when the sandbox is ALIVE but not responding in time — the host is
    overloaded / the op is mid-transfer and keeps timing out (#492).

    Distinct from `SandboxNotFound` on purpose: a busy sandbox must NOT be rebuilt
    (that would spin up a second live sandbox = split-brain) and its item must NOT
    fall back to a cold durable write (the item is globally warm, so a durable
    write would be reconciled away by the host's `--delete` mirror). The only safe
    responses are *retry* (the http client does this internally, with an escalating
    per-attempt timeout so a busy host isn't hammered) and, once retries are spent,
    *fail loud* — surface it so the caller retries later rather than corrupting
    state. Only the http backend distinguishes this; id-addressable local backends
    have no "reachable but slow" state and never raise it."""


@dataclass(frozen=True)
class SandboxHandle:
    """Opaque pointer to one live sandbox. `id` is unique per `create()`; do
    not parse it — treat it as a token to pass back to the other methods."""

    id: str


@dataclass(frozen=True)
class EnforcedLimits:
    """What a backend will ACTUALLY apply for a given spec — each `None` in the
    request replaced by the ceiling this backend was configured with.

    Deliberately NOT `SandboxSpec`. There, `None` means "not stated, you decide";
    here it means "this backend enforces nothing on that dimension", and the two
    answers are needed by different callers. What travels to `create` has to stay
    the REQUEST (sending a number we invented would silently override the
    backend's own configuration — for the http host, its `SANDBOX_HOST_*`). What
    the per-person tally charges has to be the DECISION, because a sandbox held
    under a 1-core cgroup costs its owner a core whether or not any App wrote
    that number down. Reading the request for both is what made `/my-resources`
    report "CPU 0" beside a live environment.

    `0` keeps the meaning it has everywhere else here: explicitly unbounded.
    """

    cpu_cores: float | None
    memory_bytes: int | None


@dataclass(frozen=True)
class RunningSandbox:
    """One sandbox a backend reports as ACTUALLY running, right now.

    `item_id` is what the app calls it — the only name it has — and is `None`
    for a sandbox created without one. An unnamed orphan is still worth showing,
    and it cannot be matched to an item: `_close_unrecorded` will never pick it,
    which is the safe direction.

    The handle is the real, addressable one, so what is found can be acted on.
    """

    handle: SandboxHandle
    item_id: str | None


@dataclass(frozen=True)
class SandboxSpec:
    """Everything `create()` needs to provision a sandbox."""

    image: str = "python:3.12-slim"
    """Container image (DockerSandbox). Ignored by backends that don't use
    images (LocalProcessSandbox runs on the host's interpreters)."""

    env: dict[str, str] | None = None
    """Extra environment variables for commands run in the sandbox."""

    tools: dict[str, str] | None = None
    """#674: third-party tools to mount, as `{local name: bundle sha}`.
    Empty and absent mean the same thing — no third-party tools — so callers
    pass whatever they have rather than converting one into the other. The sha
    comes from the resolve the app did at the start of this turn, so the bundle
    mounted here is the one whose schema the model was given. The NAME is the
    deployment's, not the author's — it is what the sandbox sees at
    `/.tools/<name>`. Backends without an artifact store ignore it."""

    cpu_cores: float | None = None
    memory_bytes: int | None = None
    pids_max: int | None = None
    """Resource ceilings for THIS sandbox, resolved from the App's declaration
    and the deploy's config (see `quota.limits`). They travel with `create`
    because a backend that caps by cgroup has to know them at provision time.

    `None` means "not stated" — the backend applies whatever it was configured
    with, which is what every pre-existing caller (a bare `SandboxSpec()`) gets.
    **0 is a different answer**: it means explicitly unbounded, the way
    `filestore.workspace_quota: 0` and a cgroup's `max` already spell it. The
    two must not collapse into one sentinel here, or an App that deliberately
    lifts a limit would silently inherit the deploy's instead.

    `memory_bytes` is a resolved BYTE COUNT, not a friendly string: parsing
    belongs at the config edge, so a typo is a boot error rather than something
    a sandbox discovers at create time. Backends with no resource control (mock,
    the plain local process sandbox) ignore these — the fields are a request, and
    an unenforcing backend is not lying about anything it claimed."""

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
    with FileStore keys without further normalization.

    `version` is an **opaque** change-stamp the backend computes however it can
    afford — a content hash, an `mtime:size` pair, a write counter — and the
    only contract is: *it differs iff the file's content may have changed.* The
    mirror diffs `version` against what it last snapshotted to decide which
    files to re-copy (so cheap backends stay cheap, and a backend with nothing
    better can fall back to a content hash). It also doubles as the
    compare-and-swap token for `write_file`. Never parse it."""

    path: str
    size: int
    version: str = ""


@dataclass(frozen=True)
class WalkResult:
    """One traversal's two halves, as `Sandbox.walk` returns them.

    Directories are plain paths, not `FileEntry`: a directory has no content, so
    `size` and the `version` change-stamp (a content hash / CAS token) would be
    meaningless for one, and a caller that received directories as file entries
    would mirror them, bill them against the quota, or try to download them.
    Keeping the halves apart makes each caller say which one it wants.

    Both halves come from ONE walk because the file tree needs both and, warm,
    that traversal crosses the network — and because an EMPTY directory appears
    in no file path, so `dirs` cannot be derived from `files` afterwards. That
    derivation is exactly why a folder holding no files was invisible."""

    files: list[FileEntry]
    dirs: list[str]


class Sandbox(Protocol):
    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        """Provision a sandbox and return its handle. Any `spec.exposed_ports`
        must be arranged here (e.g. Docker publishes them at container start) —
        they cannot be added later.

        `sandbox_id` None → a FRESH, empty sandbox with a random handle (each
        handle has its own isolated filesystem). A given `sandbox_id` makes
        create STABLE + IDEMPOTENT: the handle id IS `sandbox_id` and the same
        id re-attaches to the same underlying filesystem — so a different
        process/pod sharing the storage reattaches to (not wipes) the existing
        files. #345: the local sandbox keys an item's working dir by item id on
        a shared volume, so every pod resolves the same dir for an item."""
        ...

    async def effective_limits(self, spec: SandboxSpec) -> EnforcedLimits:
        """The ceilings this backend will REALLY apply for `spec` — each `None`
        in the request replaced by whatever it was configured with.

        A backend that caps nothing returns `None`s and nothing is charged — the
        honest answer for the mock and the plain local process.

        Async only because ONE backend has to ask: the http host applies its own
        `SANDBOX_HOST_*`, which lives in another service's environment, so the
        only truthful source is the host itself. It answers from a BRIEFLY cached
        advertisement, so the heartbeat can call this every time without a round
        trip each time — and the cache expires, because the host is a separate
        deployment whose numbers can change without restarting this one.

        This exists because "what was requested" and "what will be enforced" are
        different questions over the same fields, and the quota needs the second.
        See `EnforcedLimits`."""
        ...

    async def running_sandboxes(self) -> list[RunningSandbox] | None:
        """What this backend is REALLY running — or `None` when it cannot say.

        Everything else the app knows about live sandboxes is stored belief: a
        heartbeat that bills someone, an address that routes, a panel offering a
        Close button. None of it could be checked against the machine, so a
        stale record was indistinguishable from a true one — and clearing a
        record became the only way to express "it is gone", including when it
        was not. That is how closing an environment could report success while
        the sandbox kept running, and how a sandbox the app had lost track of
        kept costing its owner from a place they could not click.

        **Positive evidence only.** A returned entry means that sandbox exists.
        The absence of one does NOT mean it does not: the http host runs several
        replicas behind a load balancer, so an answer covers the pod that took
        the request and no more. To decide that a PARTICULAR sandbox is gone,
        probe its handle (`exists`) — that routes to the pod that owns it.

        `None` and `[]` are different answers and must not collapse: `[]` says
        nothing is running here, `None` says we failed to ask (host unreachable,
        or too old to answer). Reading `None` as `[]` would let one blip retire
        the records of every live sandbox — the unrecoverable direction.

        A backend with no way to enumerate returns `None` for the same reason.
        """
        ...

    def handle_for_id(self, sandbox_id: str) -> SandboxHandle | None:
        """#345: the handle that reaches the sandbox for `sandbox_id` on shared
        storage WITHOUT a prior `create` on this process — or None if this
        backend doesn't address sandboxes by a stable id (e.g. the HTTP host
        mints its own pod-scoped handles). Existence is NOT checked here (it's a
        pure id→handle mapping); a later file op raises `SandboxNotFound` when
        the sandbox is cold, and the caller falls back to the durable snapshot.
        Lets a file read on ANY pod route to the live shared dir instead of a
        stale snapshot, so workspace data no longer depends on sticky routing.

        Optional: callers reach it via ``getattr(sandbox, "handle_for_id", None)``
        (so a backend / test double that omits it routes to the snapshot), hence
        a pure-signature body like the rest of the Protocol — never a runtime
        default."""
        ...

    async def kill(self, handle: SandboxHandle) -> None:
        """Tear the sandbox down and release its resources (temp dir /
        container). The handle is invalid afterwards — further calls with it
        raise `SandboxNotFound`. Idempotency is not required."""
        ...

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
        env: Mapping[str, str] | None = None,
        exec_timeout: float | None = None,
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
          whatever stdout was captured before the kill (don't discard it).
        - `env` is added to the command's environment and WINS over anything the
          backend sets itself. It is per-call on purpose: the item's user-set
          variables reach the tools this way, and nothing else the sandbox runs
          — the agent's own `exec` included — has anything to inherit."""
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

    # `download_many(handle, paths) -> list[bytes | None]` is NOT on this core
    # Protocol — it is an optional capability the WorkspaceFiles facade
    # duck-types (#781), the same way FileStore's `stat_all` / CAS pair work. A
    # backend that has it answers a whole chunk in one round trip; one that does
    # not is read a file at a time, and no caller can tell which happened.
    #
    # Its contract is "N calls to `download`, in one hop": ONLY a missing file
    # becomes `None` (an answer about that path, so the facade can raise for a
    # caller that demanded it and skip it for a listing that merely named it).
    # Every other error propagates — a directory is not a missing file. Answers
    # are in the order asked. The facade bounds the size of the ask.

    async def upload_file(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None:
        """Like `upload`, but copy the content from the on-disk `local_path`
        rather than taking it as in-memory `bytes` — so a big upload streams in
        without the whole file ever sitting in RAM (issue #219). Overwrites an
        existing file; creates parent dirs."""
        ...

    async def download_to_file(
        self, handle: SandboxHandle, remote_path: str, local_path: Path
    ) -> None:
        """Like `download`, but stream the bytes of `remote_path` out to the
        on-disk `local_path` rather than returning them — so the reverse-sync
        mirror can persist a big sandbox file without it sitting in RAM (issue
        #219). Raises `FileNotFoundError` if `remote_path` doesn't exist."""
        ...

    async def walk(self, handle: SandboxHandle, root: str) -> WalkResult:
        """Traverse `root` once and return its regular files AND its
        directories, both with `/`-rooted paths. Symlinks are excluded (only
        real files round-trip to the FileStore). `root` is workspace-root-
        relative; "/" walks the whole workspace.

        `dirs` holds EVERY directory under `root`, including the ones that hold
        no files — those appear in no file path, so nothing downstream can
        recover them from `files`."""
        ...

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        """True if a **regular file** exists at `path` (directories report
        False — mirror FileStore.exists)."""
        ...

    async def disk_usage(self, handle: SandboxHandle) -> int:
        """#538: total bytes the workspace occupies, as ONE number.

        The quota asks the sandbox how big it is instead of walking it and
        adding up entries: the answer comes from the thing that owns the disk,
        so every pod looking at the same sandbox gets the same figure rather
        than each keeping its own tally in memory, and it dies with the sandbox
        rather than outliving it in some process.

        Scoped to the walked workspace, NOT the infra area beside it — the
        `.ready` marker and the per-sandbox `.home` never appear in the file
        tree, so counting them would charge the user for bytes they cannot see
        or delete. (The scratch VOLUME is a separate concern with its own cap,
        `sandbox.max_workspace_bytes`.)

        Apparent bytes, matching what `walk` reports per file, so the usage
        figure and the file tree's sizes are the same quantity."""
        ...

    async def size_of(self, handle: SandboxHandle, path: str) -> int | None:
        """#538: bytes of the regular file at `path`, or None if absent.

        The quota's overwrite credit. It has to come from the same live source
        as `disk_usage`, or the two halves of "does this write GROW the
        workspace" disagree about whether a file counts."""
        ...

    async def mark_ready(self, handle: SandboxHandle) -> None:
        """#366: mark this sandbox authoritative — its files are the complete,
        restored state. SandboxSync calls it at the END of `restore`, and the
        deletion-aware mirror only propagates deletions while `is_ready` holds
        (before + after its walk), so a mid-rebuild/half-restored sandbox can
        never make the mirror wipe the durable snapshot.

        The marker lives OUTSIDE the workspace (a real backend puts it beside,
        not inside, the walked dir), so it never appears in `walk`/`exists`/the
        file tree and no user file can forge it. Cleared when the sandbox is
        `kill()`ed (the underlying dir — and marker — is gone)."""
        ...

    async def is_ready(self, handle: SandboxHandle) -> bool:
        """#366: True once `mark_ready` ran and the sandbox still lives; False
        for a fresh/rebuilt-but-not-yet-restored sandbox. A vanished sandbox
        raises `SandboxNotFound` like every other op."""
        ...

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        """Delete the regular file at `path`. Raise `FileNotFoundError` if it
        does not exist. Parent directories are left intact."""
        ...

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        """Create the directory at `path` and any missing ancestors. Idempotent
        for an existing directory."""
        ...

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        """Remove the directory at `path` and everything beneath it. Raise
        `FileNotFoundError` if it does not exist."""
        ...

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        """Move/rename `src` to `dst` (file or directory), creating `dst`'s
        parent directories as needed. Raise `FileNotFoundError` if `src` is
        absent."""
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
