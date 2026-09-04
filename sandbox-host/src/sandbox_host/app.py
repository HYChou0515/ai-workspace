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
import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .artifact import ArtifactError
from .nfs_archive import NfsArchive
from .protocol import ExecResult, Sandbox, SandboxHandle, SandboxNotFound, SandboxSpec
from .tool_cache import ToolCache
from .tool_resolve import ToolResolver

logger = logging.getLogger(__name__)

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
        # `/healthz` publishes the ceilings this host applies when a spec states
        # nothing. Its absence means the app cannot know what a sandbox costs its
        # owner and charges zero — visibly under-counting rather than guessing.
        "resource-defaults",
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
    # This sandbox's resource ceilings, resolved by the app from the item's App.
    # Absent / null ⇒ this host's own configured defaults, which is also what an
    # older app (sending neither field) gets.
    cpu_cores: float | None = None
    memory_bytes: int | None = None
    pids_max: int | None = None
    # #674: this turn's third-party bundles, {local name: bundle sha}.
    tools: dict[str, str] | None = None


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


class _ReadManyRequest(BaseModel):
    paths: list[str]


class _ReadManyReply(BaseModel):
    """Many files in one answer, in the order asked.

    `data` is base64 because a workspace file is arbitrary bytes and this reply
    is JSON. `None` means THAT PATH is absent — an answer about the path, not a
    failed batch, so a caller can raise for one it demanded and skip one a
    listing merely named. A client that does not know this endpoint reads files
    one at a time exactly as before; nothing above the sandbox can tell which
    happened."""

    files: list[str | None]


class _DiskUsageReply(BaseModel):
    bytes: int


class _SizeReply(BaseModel):
    size: int | None


class _WalkReply(BaseModel):
    entries: list[_FileEntryModel]
    # Directories from the SAME traversal. An empty one appears in no file path,
    # so a client cannot derive it from `entries` — that derivation is why a
    # folder holding no files never reached the file tree. Defaulted so an older
    # client that ignores the field, and an older host that omits it, both stay
    # on the previous (files-only) behaviour instead of failing.
    dirs: list[str] = []


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
    # This command's own TOTAL wall-clock budget, when the caller has one
    # (`uv sync` does — a cold start must be waited for, not killed at the
    # instance default). Absent on an older client, which then gets the
    # instance default exactly as before.
    exec_timeout: float | None = None


def _frame(obj: dict[str, object]) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


async def _exec_ndjson(
    sandbox: Sandbox,
    handle: SandboxHandle,
    cmd: list[str],
    env: Mapping[str, str] | None = None,
    exec_timeout: float | None = None,
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
            result = await sandbox.exec(
                handle, cmd, on_output=on_output, env=env, exec_timeout=exec_timeout
            )
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
        handle = await self.sandbox.create(spec, item_id=item_id)
        self._last_active[handle.id] = self.clock()
        # Recorded whether or not an archive is wired. It began as `persist`'s
        # private lookup, so it was only filled on the archive path — but it is
        # also the only name the APP knows a sandbox by, and `GET /sandboxes`
        # exists so the app can check its records against what is really running.
        # Keyed on every create, or that listing is blank on any deployment
        # without an archive.
        if item_id is not None:
            self._item_of[handle.id] = item_id
        # #492: restore the durable archive into the fresh local dir (no-op when
        # nothing archived yet — a brand-new item starts empty), then mark the
        # sandbox ready. rsync restore is SYNCHRONOUS here, so by the time create
        # returns the dir is complete and authoritative — the host owns readiness
        # in the archive path (the app no longer runs its own restore). mark_ready
        # is written LAST so a crash mid-restore leaves it absent and persist
        # (gated on ready) can't push a half-restored dir back over the archive.
        if self._archive is not None and item_id is not None:
            await self._archive.restore(item_id, self.sandbox.workspace_dir(handle))
            # #504: the bulk rsync restore writes files as root (no `-o`), so the
            # restored tree comes back root-owned. Re-own it to the sandbox uid
            # BEFORE marking ready, so the dropped exec uid can git/chmod them.
            await self.sandbox.reown(handle)
            await self.sandbox.mark_ready(handle)
        return handle

    def live(self) -> list[dict[str, object]]:
        """Every sandbox this host is running, and which item it serves.

        Driven by `_last_active`, which is the same dict the idle reaper walks —
        so this listing cannot miss a sandbox the reaper can still see. The item
        name is looked up beside it and may be absent (an anonymous create), which
        is reported as `None` rather than skipped: a sandbox nobody can name is
        exactly the kind worth showing."""
        # No last-active timestamp: this host's clock is `time.monotonic()`,
        # which is process-relative and means nothing to another service. A
        # field a reader cannot interpret is worse than an absent one.
        return [{"remote_id": rid, "item_id": self._item_of.get(rid)} for rid in self._last_active]

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
        """Forget it only once it is really gone.

        Popping first meant a kill that raised left a sandbox still running and
        invisible to BOTH the idle reaper (`_last_active`) and `GET /sandboxes`
        — so nothing would ever retry it and nothing could report it, which
        defeats the one mechanism the app has for finding an orphan. An
        already-unknown handle raises `SandboxNotFound` from the backend before
        anything is dropped, which is the same answer as before."""
        await self.sandbox.kill(SandboxHandle(id=rid))
        self._last_active.pop(rid, None)
        self._item_of.pop(rid, None)

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

    async def sweep_uv_cache(self, *, max_bytes: int | None = None) -> list[str]:
        """#775: reclaim the download caches of items nothing is running here.

        Rides the idle reaper for the same reason `sweep_tool_cache` does — a
        sandbox ending is when a cache stops being written to. The in-use set
        comes from the live sandboxes themselves, so a cache a sync is filling
        right now can never be evicted, however full the disk.

        The key is the ITEM, never the uid: uids here are pooled and freed on
        kill, so a uid-keyed cache would be handed to the next tenant of that
        uid — and "no live sandbox for it" is precisely the moment that happens,
        which is the moment a sweeper would also call it collectable."""
        sweep = getattr(self.sandbox, "sweep_uv_cache", None)
        in_use = getattr(self.sandbox, "cache_keys_in_use", None)
        if sweep is None or in_use is None:
            return []
        return await asyncio.to_thread(sweep, in_use=in_use(), max_bytes=max_bytes)

    async def reap_idle(self) -> list[str]:
        """Kill sandboxes with no activity for `idle_ttl` — the backstop for an
        app pod that crashed without calling kill (`idle_ttl <= 0` disables it).
        Per-handle, distinct from the per-command exec/idle timeouts."""
        if self.idle_ttl <= 0:
            return []
        now = self.clock()
        stale = [r for r, t in self._last_active.items() if now - t > self.idle_ttl]
        reaped = []
        for rid in stale:
            # Per item: one sandbox whose teardown fails used to abort the whole
            # sweep, so every idle sandbox after it survived the pass — and the
            # next pass hits the same one first and stops in the same place.
            try:
                await self.kill(rid)
            except Exception:  # noqa: BLE001 - one bad sandbox must not strand the rest
                logger.warning("host: idle reap failed for %s", rid, exc_info=True)
                continue
            reaped.append(rid)
        return reaped


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
        # `defaults` is what this host caps a sandbox at when the app states
        # nothing. The app charges a sandbox's owner for what it holds and
        # cannot read this service's `SANDBOX_HOST_*`, so without this it could
        # only charge the request — and an App that declared nothing held a core
        # for free. Asked of the SANDBOX rather than re-read from settings, so
        # the number published is the one the cgroup gets.
        #
        # Never allowed to fail the response. This endpoint is the deployment's
        # LIVENESS probe (`deploy/sandbox-host.example.yaml`), so an exception
        # here would turn "cannot answer a resource question" into a crashloop.
        # A backend that cannot say publishes nothing, the app then charges
        # nothing, and that is a visible under-count rather than an outage.
        defaults: dict[str, object] = {"cpu_cores": None, "memory_bytes": None}
        try:
            enforced = await sandbox.effective_limits(SandboxSpec())
            defaults = {
                "cpu_cores": enforced.cpu_cores,
                "memory_bytes": enforced.memory_bytes,
            }
        except Exception:  # noqa: BLE001 — liveness must not depend on this
            logger.warning("healthz: backend could not report its limits", exc_info=True)
        return {
            "status": "ok",
            "version": _version(),
            "capabilities": sorted(_CAPABILITIES),
            "defaults": defaults,
        }

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
        # `dict[str, Any]`, not `object`: the values are payload dicts this
        # function indexes back into two lines later. `object` made that
        # assignment untypeable — invisible while the checker was excluded.
        resolved: dict[str, dict[str, Any]] = {}
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
                "author": tool.author,
                "description": tool.description,
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
            if tool.env is not None:
                # Only when the author declared something. A key that always
                # appeared would turn every pre-#750 artifact's silence into
                # "needs nothing" — and the app cannot tell the difference from
                # anywhere else, because it never reads a manifest (#696 is the
                # same shape of loss, in the other direction).
                resolved[name]["env"] = [
                    {"name": e.name, "description": e.description, "required": e.required}
                    for e in tool.env
                ]
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

    @app.get("/sandboxes")
    async def list_sandboxes() -> dict[str, object]:
        """What is ACTUALLY running here, keyed by item.

        The app keeps records — a heartbeat that bills people, an address that
        routes, a panel offering a Close button — and until this existed it had
        no way to check any of them against reality. A stale record read exactly
        like a true one, so clearing a record became the only available way to
        say "gone", including when it was not: that is how closing an
        environment could report success while the sandbox kept running, and how
        clearing a heartbeat could tell every replica's reaper that a directory
        somebody was working in was idle."""
        # `advertise_url` for the same reason `create` returns it: the Service in
        # front of this deployment load-balances, so this answer is THIS pod's,
        # and anything the app then wants to do to a listed sandbox has to reach
        # this pod. Without it the app could see an orphan and not be able to
        # kill it. For the same reason the listing is evidence that something
        # EXISTS and never evidence that something does not — another pod's
        # sandboxes are simply not in this answer.
        return {"sandboxes": [{**s, "pod_url": advertise_url} for s in controller.live()]}

    @app.post("/sandboxes")
    async def create(body: _CreateBody) -> Response:
        if controller.draining:
            # Draining (SIGTERM): stop taking new sandboxes; existing ones run
            # on until idle or the pod's termination grace deadline.
            return JSONResponse(status_code=503, content={"error": "draining"})
        spec = SandboxSpec(
            image=body.image,
            env=body.env,
            exposed_ports=tuple(body.exposed_ports),
            tools=body.tools,
            cpu_cores=body.cpu_cores,
            memory_bytes=body.memory_bytes,
            pids_max=body.pids_max,
        )
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

    @app.post("/sandboxes/{rid}/files")
    async def download_many(rid: str, body: _ReadManyRequest) -> _ReadManyReply:
        """Read a batch of paths in one round trip.

        The app reads a whole record type / listing at a time, and doing that a
        file at a time made the round trips the cost — a 68-record listing spent
        ~70 of them where one would do. POST because the path list is a body,
        not a query string a proxy will truncate."""
        import base64

        handle = SandboxHandle(id=rid)
        out: list[str | None] = []
        for path in body.paths:
            try:
                out.append(base64.b64encode(await sandbox.download(handle, path)).decode())
            except FileNotFoundError:
                out.append(None)
        return _ReadManyReply(files=out)

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
        walked = await sandbox.walk(SandboxHandle(id=rid), root)
        return _WalkReply(
            entries=[
                _FileEntryModel(path=e.path, size=e.size, version=e.version) for e in walked.files
            ],
            dirs=walked.dirs,
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
            _exec_ndjson(sandbox, SandboxHandle(id=rid), body.cmd, body.env, body.exec_timeout),
            media_type="application/x-ndjson",
        )

    return app
