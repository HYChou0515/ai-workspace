"""FastAPI sandbox host — exposes ONE injected `Sandbox` over HTTP.

The host is backend-agnostic: it holds a single `Sandbox` instance (production
injects `IsolatedProcessSandbox`; tests inject `MockSandbox`) and proxies each
operation to it. The matching client is the workspace app's `HttpSandbox` — the
two share NO Python modules, only the HTTP wire contract (`docs/sandbox-host-wire.md`).

`create` returns the host's own directly-addressable URL (`advertise_url`, set
from the pod's `POD_IP`) plus the backend's local handle id; the client encodes
both into its opaque handle so every later call routes straight back to this pod.

Errors are mapped to a structured `{"error": <type>, "detail": <msg>}` body with
HTTP 404 so the client can re-raise the matching exception type (`SandboxNotFound`
vs `FileNotFoundError`).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .artifact import ArtifactError
from .nfs_archive import NfsArchive
from .protocol import ExecResult, Sandbox, SandboxHandle, SandboxNotFound, SandboxSpec
from .tool_cache import ToolCache
from .tool_resolve import ToolResolver

# What THIS build does, advertised on /healthz. Which code a running host
# carries is otherwise invisible — `image: sandbox-host:latest` reads the same
# before and after a rebuild, and an old host answers every request perfectly
# well; it just behaves like the old code. Diagnosing that from the app side
# meant three layers of guessing once already (a sandbox whose `$HOME` was never
# set is indistinguishable, over the wire, from one where it was set right).
#
# Capabilities rather than a version number: they are what a caller actually
# wants to know, and they live in the same commit as the behaviour they name, so
# they cannot drift the way a hand-maintained compatibility table does. Add one
# when you add a behaviour the app (or a human triaging a sandbox) would
# otherwise have to infer; never remove one without removing the behaviour.
_CAPABILITIES = frozenset(
    {
        # Every exec gets HOME pointed at the sandbox's own `.home`, created and
        # owned to the exec uid at that moment (#393/#600, and the per-exec
        # guarantee that followed). Its absence means `soffice` and anything else
        # that writes a profile to $HOME will fail on this host.
        "per-exec-home",
        # `create` restores the item's durable archive and marks readiness before
        # returning; `persist` is gated on that marker (#492).
        "host-managed-archive",
    }
)


def _version() -> str:
    """This build's version — the installed package's, `unknown` when it cannot
    be read (a source checkout without an install)."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        return _pkg_version("sandbox-host")
    except PackageNotFoundError:  # pragma: no cover - only in an odd checkout
        return "unknown"


# A readiness probe: raises with a reason when the host can't safely serve.
ReadinessCheck = Callable[[], None]

# cgroup v2 mounts a `cgroup.controllers` file at the unified-hierarchy root.
_CGROUP_V2_MARKER = Path("/sys/fs/cgroup/cgroup.controllers")


class _CreateBody(BaseModel):
    image: str = "python:3.12-slim"
    env: dict[str, str] | None = None
    exposed_ports: tuple[int, ...] = ()
    # #492: the workspace item this sandbox serves. When the host has an NFS
    # archive, create restores `{nfs_root}/{item_id}` into the fresh sandbox and
    # later persists it back. Optional (older clients omit it ⇒ no archive).
    item_id: str | None = None


class _PersistBody(BaseModel):
    # #492: `delete` ⇒ rsync --delete (turn-end / reap reconcile, at a quiesced
    # ready sandbox); False ⇒ additive-only mid-turn durability checkpoint.
    delete: bool = False


class _CreateReply(BaseModel):
    pod_url: str
    remote_id: str


class _ExistsReply(BaseModel):
    exists: bool


class _ReadyReply(BaseModel):
    ready: bool


class _FileEntryModel(BaseModel):
    path: str
    size: int
    version: str = ""


class _DiskUsageReply(BaseModel):
    bytes: int


class _SizeReply(BaseModel):
    size: int | None


class _WalkReply(BaseModel):
    entries: list[_FileEntryModel]


class _MkdirBody(BaseModel):
    path: str


class _RenameBody(BaseModel):
    src: str
    dst: str


class _ResolveToolsBody(BaseModel):
    """`{local name: manifest url}` — the whole set an app wants, so the
    host answers about all of them in one round trip."""

    tools: dict[str, str] = {}


class _ExecBody(BaseModel):
    cmd: list[str]
    # The item's user-set variables for THIS command. Absent on an older client;
    # the backend treats absent and empty the same way.
    env: dict[str, str] | None = None


def _frame(obj: dict[str, object]) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


async def _exec_ndjson(
    sandbox: Sandbox,
    handle: SandboxHandle,
    cmd: list[str],
    env: Mapping[str, str] | None = None,
) -> AsyncIterator[bytes]:
    """Run `exec` and yield NDJSON frames as output arrives.

    `{"o": b64}` per live chunk (forwarded to the caller's `on_output`; stdout
    and stderr interleaved, mirroring the backend's single sink), then a final
    `{"exit", "out", "err"}` with the separated buffers, or `{"error", "detail"}`
    if `exec` raised (the response status is already 200, so errors must travel
    in-band as a frame). The live bytes are re-sent in the final frame so the
    client can rebuild the separated `ExecResult` — small for typical output,
    and faithful to the protocol's two outputs.
    """
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    def on_output(chunk: bytes) -> None:
        queue.put_nowait(("o", chunk))

    async def run() -> None:
        try:
            result = await sandbox.exec(handle, cmd, on_output=on_output, env=env)
            queue.put_nowait(("done", result))
        except Exception as exc:  # noqa: BLE001 — relayed in-band as an error frame
            queue.put_nowait(("error", exc))

    task = asyncio.create_task(run())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "o":
                assert isinstance(payload, bytes)
                yield _frame({"o": base64.b64encode(payload).decode()})
            elif kind == "done":
                assert isinstance(payload, ExecResult)
                yield _frame(
                    {
                        "exit": payload.exit_code,
                        "out": base64.b64encode(payload.stdout).decode(),
                        "err": base64.b64encode(payload.stderr).decode(),
                    }
                )
                return
            else:  # "error"
                assert isinstance(payload, Exception)
                yield _frame({"error": type(payload).__name__, "detail": str(payload)})
                return
    finally:
        await task


def _error(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": type(exc).__name__, "detail": str(exc)})


def check_cgroup_ready(cgroup_root: Path, *, controllers_marker: Path = _CGROUP_V2_MARKER) -> None:
    """Fail loud unless this pod can isolate: cgroup v2 must be mounted and the
    delegated `cgroup_root` (or its parent, before first use) must be writable.
    Feeds both the boot check and `/readyz` (isolation is the whole point —
    never serve without it)."""
    if not controllers_marker.exists():
        raise RuntimeError(
            f"cgroup v2 not mounted ({controllers_marker} absent) — the sandbox "
            "host needs unified cgroups to cap memory/cpu/pids"
        )
    target = cgroup_root if cgroup_root.exists() else cgroup_root.parent
    if not os.access(target, os.W_OK):
        raise RuntimeError(
            f"cgroup_root {cgroup_root} not writable — is the cgroup subtree delegated to this pod?"
        )


class _HostController:
    """Owns the host's operational state: which sandboxes are live (for the
    idle-reaper), whether we're draining, and the activity clock. Create/kill
    flow through it so the reaper can see every handle."""

    def __init__(
        self,
        sandbox: Sandbox,
        *,
        idle_ttl: float,
        tool_cache: ToolCache | None = None,
        clock: Callable[[], float],
        archive: NfsArchive | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.idle_ttl = idle_ttl
        self._tool_cache = tool_cache
        self.clock = clock
        self.draining = False
        self._last_active: dict[str, float] = {}
        # #492: when an NFS archive is wired, a create with an `item_id` restores
        # that item's durable working dir into the fresh sandbox, and `persist`
        # rsyncs it back. `_item_of` maps a live handle → its item so persist/reap
        # know which archive dir to write.
        self._archive = archive
        self._item_of: dict[str, str] = {}

    def start_draining(self) -> None:
        self.draining = True

    def touch(self, rid: str) -> None:
        if rid in self._last_active:
            self._last_active[rid] = self.clock()

    async def create(self, spec: SandboxSpec, item_id: str | None = None) -> SandboxHandle:
        handle = await self.sandbox.create(spec)
        self._last_active[handle.id] = self.clock()
        # #492: restore the durable archive into the fresh local dir (no-op when
        # nothing archived yet — a brand-new item starts empty), then mark the
        # sandbox ready. rsync restore is SYNCHRONOUS here, so by the time create
        # returns the dir is complete and authoritative — the host owns readiness
        # in the archive path (the app no longer runs its own restore). mark_ready
        # is written LAST so a crash mid-restore leaves it absent and persist
        # (gated on ready) can't push a half-restored dir back over the archive.
        if self._archive is not None and item_id is not None:
            self._item_of[handle.id] = item_id
            await self._archive.restore(item_id, self.sandbox.workspace_dir(handle))
            # #504: the bulk rsync restore writes files as root (no `-o`), so the
            # restored tree comes back root-owned. Re-own it to the sandbox uid
            # BEFORE marking ready, so the dropped exec uid can git/chmod them.
            await self.sandbox.reown(handle)
            await self.sandbox.mark_ready(handle)
        return handle

    async def persist(self, rid: str, *, delete: bool) -> None:
        """#492: rsync the sandbox's live working dir → its durable NFS archive.
        A no-op when no archive is wired, this handle has no item mapping, or the
        sandbox is not ready (a half-restored dir must never overwrite the
        archive — the #492 Q9 `.ready` gate on persist, so `--delete` can't wipe
        durable data)."""
        item = self._item_of.get(rid)
        if self._archive is None or item is None:
            return
        handle = SandboxHandle(id=rid)
        if not await self.sandbox.is_ready(handle):
            return
        await self._archive.persist(item, self.sandbox.workspace_dir(handle), delete=delete)

    async def kill(self, rid: str) -> None:
        self._last_active.pop(rid, None)
        self._item_of.pop(rid, None)
        await self.sandbox.kill(SandboxHandle(id=rid))

    async def sweep_tool_cache(self, *, max_bytes: int | None = None) -> list[str]:
        """#674: reclaim third-party bundles nothing is running any more.

        Runs beside the idle reaper because that is when bundles stop being
        referenced — a sandbox ending is what frees one. The in-use set is read
        from the live sandboxes' own views, so a bundle a turn is using now can
        never be evicted, however full the cache."""
        cache, in_use = self._tool_cache, getattr(self.sandbox, "tools_in_use", None)
        if cache is None or in_use is None:
            return []
        return await asyncio.to_thread(cache.sweep, in_use=in_use(), max_bytes=max_bytes)

    async def reap_idle(self) -> list[str]:
        """Kill sandboxes with no activity for `idle_ttl` — the backstop for an
        app pod that crashed without calling kill (`idle_ttl <= 0` disables it).
        Per-handle, distinct from the per-command exec/idle timeouts."""
        if self.idle_ttl <= 0:
            return []
        now = self.clock()
        stale = [r for r, t in self._last_active.items() if now - t > self.idle_ttl]
        for rid in stale:
            await self.kill(rid)
        return stale


def make_host_app(
    sandbox: Sandbox,
    *,
    advertise_url: str,
    idle_ttl: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    readiness: ReadinessCheck | None = None,
    archive: NfsArchive | None = None,
    tool_resolver: ToolResolver | None = None,
) -> FastAPI:
    app = FastAPI()
    controller = _HostController(
        sandbox,
        idle_ttl=idle_ttl,
        clock=clock,
        archive=archive,
        tool_cache=tool_resolver.cache if tool_resolver is not None else None,
    )
    app.state.controller = controller

    @app.middleware("http")
    async def _track_activity(request: Request, call_next):
        # Any call targeting an existing sandbox counts as activity, so the
        # reaper only collects genuinely-orphaned handles.
        parts = request.url.path.split("/")
        if len(parts) > 2 and parts[1] == "sandboxes":
            controller.touch(parts[2])
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"status": "ok", "version": _version(), "capabilities": sorted(_CAPABILITIES)}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            if readiness is not None:
                readiness()
        except Exception as exc:  # noqa: BLE001 — surface any reason as not-ready
            return JSONResponse(status_code=503, content={"ready": False, "detail": str(exc)})
        return JSONResponse(status_code=200, content={"ready": True})

    @app.post("/tools/resolve")
    async def resolve_tools(body: _ResolveToolsBody) -> dict[str, object]:
        """#674: make third-party tools available, and describe them.

        One request carries every tool an app declares, and the reply is
        deliberately PARTIAL rather than all-or-nothing: a refusal is reported
        per tool so the app can drop that one and run the turn. Failing the
        whole request would let one author's expired artifact quietly disable
        every other tool in the workspace, which is the opposite of what an
        operator would want at 3am.

        Blocking work (an HTTP GET, a sha, a 150MB unpack) goes to a thread —
        this is the host's event loop, and other sandboxes are using it.
        """
        resolved: dict[str, object] = {}
        refused: dict[str, str] = {}
        for name, url in body.tools.items():
            if tool_resolver is None:
                refused[name] = "this host has no tool store configured"
                continue
            try:
                tool = await asyncio.to_thread(tool_resolver.resolve, name, url)
            except ArtifactError as exc:
                refused[name] = str(exc)
                continue
            resolved[name] = {
                "sha": tool.sha,
                "version": tool.version,
                "stale": tool.stale,
                "commands": [
                    {
                        "name": c.name,
                        "description": c.description,
                        "params_json_schema": c.params_json_schema,
                    }
                    for c in tool.commands
                ],
            }
        return {"tools": resolved, "refused": refused}

    @app.post("/drain", status_code=202)
    async def drain() -> dict[str, bool]:
        # Called by the pod's PreStop hook before SIGTERM: stop accepting new
        # sandboxes so the pod can scale down without stranding fresh work.
        controller.start_draining()
        return {"draining": True}

    @app.exception_handler(SandboxNotFound)
    async def _not_found(_request: Request, exc: SandboxNotFound) -> JSONResponse:
        return _error(exc)

    @app.exception_handler(FileNotFoundError)
    async def _file_not_found(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        return _error(exc)

    @app.post("/sandboxes")
    async def create(body: _CreateBody) -> Response:
        if controller.draining:
            # Draining (SIGTERM): stop taking new sandboxes; existing ones run
            # on until idle or the pod's termination grace deadline.
            return JSONResponse(status_code=503, content={"error": "draining"})
        spec = SandboxSpec(image=body.image, env=body.env, exposed_ports=tuple(body.exposed_ports))
        handle = await controller.create(spec, body.item_id)
        return JSONResponse(_CreateReply(pod_url=advertise_url, remote_id=handle.id).model_dump())

    @app.post("/sandboxes/{rid}/persist", status_code=204)
    async def persist(rid: str, body: _PersistBody) -> None:
        # #492: rsync the sandbox's live working dir → its durable NFS archive.
        # Host-local, so no app↔host network in the bulk path (can't hang).
        await controller.persist(rid, delete=body.delete)

    @app.delete("/sandboxes/{rid}", status_code=204)
    async def kill(rid: str) -> None:
        await controller.kill(rid)

    @app.put("/sandboxes/{rid}/file", status_code=204)
    async def upload(rid: str, path: str, request: Request) -> None:
        data = await request.body()
        await sandbox.upload(SandboxHandle(id=rid), data, path)

    @app.get("/sandboxes/{rid}/file")
    async def download(rid: str, path: str) -> Response:
        data = await sandbox.download(SandboxHandle(id=rid), path)
        return Response(content=data, media_type="application/octet-stream")

    @app.get("/sandboxes/{rid}/exists")
    async def exists(rid: str, path: str) -> _ExistsReply:
        ok = await sandbox.exists(SandboxHandle(id=rid), path)
        return _ExistsReply(exists=ok)

    @app.get("/sandboxes/{rid}/disk-usage")
    async def disk_usage(rid: str) -> _DiskUsageReply:
        """#538: the workspace's size as ONE number. The app's quota asks the
        host rather than walking the tree and adding entries up itself — the
        answer comes from the side that owns the disk, so every app pod looking
        at this sandbox gets the same figure."""
        return _DiskUsageReply(bytes=await sandbox.disk_usage(SandboxHandle(id=rid)))

    @app.get("/sandboxes/{rid}/size")
    async def size_of(rid: str, path: str) -> _SizeReply:
        return _SizeReply(size=await sandbox.size_of(SandboxHandle(id=rid), path))

    @app.post("/sandboxes/{rid}/mark-ready", status_code=204)
    async def mark_ready(rid: str) -> None:
        await sandbox.mark_ready(SandboxHandle(id=rid))

    @app.get("/sandboxes/{rid}/ready")
    async def is_ready(rid: str) -> _ReadyReply:
        ok = await sandbox.is_ready(SandboxHandle(id=rid))
        return _ReadyReply(ready=ok)

    @app.get("/sandboxes/{rid}/walk")
    async def walk(rid: str, root: str) -> _WalkReply:
        entries = await sandbox.walk(SandboxHandle(id=rid), root)
        return _WalkReply(
            entries=[_FileEntryModel(path=e.path, size=e.size, version=e.version) for e in entries]
        )

    @app.delete("/sandboxes/{rid}/file", status_code=204)
    async def delete(rid: str, path: str) -> None:
        await sandbox.delete(SandboxHandle(id=rid), path)

    @app.post("/sandboxes/{rid}/mkdir", status_code=204)
    async def mkdir(rid: str, body: _MkdirBody) -> None:
        await sandbox.mkdir(SandboxHandle(id=rid), body.path)

    @app.delete("/sandboxes/{rid}/dir", status_code=204)
    async def rmdir(rid: str, path: str) -> None:
        await sandbox.rmdir(SandboxHandle(id=rid), path)

    @app.post("/sandboxes/{rid}/rename", status_code=204)
    async def rename(rid: str, body: _RenameBody) -> None:
        await sandbox.rename(SandboxHandle(id=rid), body.src, body.dst)

    @app.post("/sandboxes/{rid}/exec")
    async def exec_(rid: str, body: _ExecBody) -> StreamingResponse:
        return StreamingResponse(
            _exec_ndjson(sandbox, SandboxHandle(id=rid), body.cmd, body.env),
            media_type="application/x-ndjson",
        )

    return app
