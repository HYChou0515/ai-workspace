from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..agent.context import AgentToolContext
from ..filestore.protocol import FileExists, FileNotFound, FileStore
from ..kernels import KernelService
from ..rca.prompts import load_system_prompt
from ..rca.templates import list_profiles, seed_investigation
from ..resources import (
    AgentConfig,
    Conversation,
    Investigation,
    Message,
    Severity,
    Status,
    register_all,
)
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..sync import SandboxSync
from .activity import ActivityLog
from .events import AgentEvent, CellEvent, RunCancelled, RunError, to_sse
from .registry import InvestigationRegistry, InvestigationSession
from .runner import AgentRunner
from .search import InvalidQuery, compile_query, path_selected, search_text


class _MessageBody(BaseModel):
    content: str


class _CellExecuteBody(BaseModel):
    code: str


class _ExecBody(BaseModel):
    cmd: list[str]


class _MoveBody(BaseModel):
    # `from` is a Python keyword — accept it on the wire via alias.
    from_: str = Field(alias="from")
    to: str


class _MkdirBody(BaseModel):
    path: str


class _SearchBody(BaseModel):
    query: str
    regex: bool = False
    caseSensitive: bool = False
    wholeWord: bool = False
    include: str = ""
    exclude: str = ""


class _ReplaceBody(_SearchBody):
    replacement: str = ""


class _CloseInvestigationBody(BaseModel):
    # null → pure close (tear the session down, leave status untouched).
    status: Literal["resolved", "abandoned"] | None = None


class _InvestigationCreateBody(BaseModel):
    title: str
    owner: str
    description: str = ""
    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING
    product: str = ""
    members: list[str] = []
    topics: list[str] = []
    attached_agent_config_id: str | None = None
    template_profile: str = "default"


def _seed_agent_configs(spec: SpecStar) -> None:
    """Create the default RCA agent configs once, if none exist yet, so the
    agent picker always has options. Models route through LiteLLM."""
    from specstar import QB

    rm = spec.get_resource_manager(AgentConfig)
    if rm.count_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        return
    prompt = load_system_prompt()
    # RCA workflow quick-prompts — the agent panel renders these as chips.
    suggestions = [
        "Show the SPC analysis",
        "Run a Pareto of defect modes",
        "Sketch a fishbone",
        "Draft a 5-Why",
        "Draft the report",
    ]
    rm.create(
        AgentConfig(
            name="RCA · Qwen3 (local)",
            model="ollama_chat/qwen3:14b",
            system_prompt=prompt,
            suggestions=suggestions,
        )
    )
    rm.create(
        AgentConfig(
            name="RCA · Claude Opus",
            model="claude-opus-4-7",
            system_prompt=prompt,
            suggestions=suggestions,
        )
    )


def create_app(
    *,
    spec: SpecStar | None = None,
    sandbox: Sandbox,
    filestore: FileStore,
    runner: AgentRunner,
    spa_dist: Path | None = None,
    idle_timeout: timedelta = timedelta(hours=8),
    idle_check_interval: timedelta = timedelta(seconds=60),
) -> FastAPI:
    if spec is None:
        spec = SpecStar()
        spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    register_all(spec)

    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    kernels = KernelService()
    activity = ActivityLog()

    async def _idle_killer() -> None:
        """Periodically reap sandboxes whose last_active is past the
        threshold. The reaper sleeps the check_interval between sweeps
        — short for tests, ~60 s in production."""
        try:
            while True:
                await asyncio.sleep(idle_check_interval.total_seconds())
                await registry.kill_idle(idle_timeout)
        except asyncio.CancelledError:
            return

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        killer = asyncio.create_task(_idle_killer())
        try:
            yield
        finally:
            killer.cancel()
            with contextlib.suppress(BaseException):
                await killer
            await kernels.shutdown_all()
            await registry.close_all()

    app = FastAPI(title="RCA 3.0", lifespan=lifespan)

    @app.get("/templates")
    async def get_templates() -> list[str]:
        """Template profile names the New Investigation picker offers."""
        return list_profiles()

    @app.get("/activity")
    async def get_activity() -> list[dict]:
        """Recent activity feed (newest first) for the notifications popover."""
        return activity.entries()

    # Register custom POST /investigation BEFORE spec.apply — FastAPI's
    # route matcher uses first-registered-wins, so our seeded-create
    # handler takes priority over specstar's stock CRUD POST.
    @app.post("/investigation")
    async def create_investigation(body: _InvestigationCreateBody) -> dict:
        inv = Investigation(
            title=body.title,
            owner=body.owner,
            description=body.description,
            severity=body.severity,
            status=body.status,
            product=body.product,
            members=list(body.members),
            topics=list(body.topics),
            attached_agent_config_id=body.attached_agent_config_id,
        )
        inv_rm = spec.get_resource_manager(Investigation)
        rev = inv_rm.create(inv)
        try:
            await seed_investigation(filestore, rev.resource_id, inv, body.template_profile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        activity.record(
            "investigation_created",
            f"Created investigation “{inv.title}”",
            {"investigation_id": rev.resource_id},
        )
        # Mirror specstar's auto-POST response shape (flat RevisionInfo dict).
        return {
            "resource_id": rev.resource_id,
            "uid": str(rev.uid),
            "revision_id": rev.revision_id,
            "created_time": rev.created_time.isoformat(),
            "updated_time": rev.updated_time.isoformat(),
            "created_by": rev.created_by,
            "updated_by": rev.updated_by,
        }

    spec.apply(app)

    # Seed a couple of default AgentConfigs so the agent picker is never
    # empty. The investigation's attached config (model + prompt) drives
    # the live agent — see _resolve_agent_config below.
    _seed_agent_configs(spec)

    conv_rm = spec.get_resource_manager(Conversation)

    def _resolve_agent_config(investigation_id: str) -> AgentConfig | None:
        """The AgentConfig attached to this investigation, if any."""
        inv_rm = spec.get_resource_manager(Investigation)
        try:
            inv = inv_rm.get(investigation_id).data
        except ResourceIDNotFoundError:
            return None
        if not isinstance(inv, Investigation) or not inv.attached_agent_config_id:
            return None
        cfg_rm = spec.get_resource_manager(AgentConfig)
        try:
            cfg = cfg_rm.get(inv.attached_agent_config_id).data
        except ResourceIDNotFoundError:
            return None
        return cfg if isinstance(cfg, AgentConfig) else None

    def _conversation_for(investigation_id: str) -> tuple[str, Conversation]:
        # Linear scan over all Conversation resources for this
        # investigation. Acceptable at v1 scale; swap to indexed lookup
        # when N grows.
        from specstar import QB

        for r in conv_rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            data = r.data
            assert isinstance(data, Conversation)
            if data.investigation_id == investigation_id:
                return r.info.resource_id, data  # ty: ignore[unresolved-attribute]
        rev = conv_rm.create(Conversation(investigation_id=investigation_id))
        got = conv_rm.get(rev.resource_id).data
        assert isinstance(got, Conversation)
        return rev.resource_id, got

    async def _cancel_prior_turn(session: InvestigationSession) -> None:
        """Cancel the session's in-flight turn and wait for it to wind down.

        Called inside session.lock so the cancel→replace transition is
        serialized. The cancelled task drains its own event queue and
        emits RunCancelled to its subscriber (the old StreamingResponse)
        before exiting.
        """
        prev = session.current_turn
        if prev is None or prev.done():
            return
        prev.cancel()
        with contextlib.suppress(BaseException):
            await prev

    async def _drive_run(
        content: str,
        ctx: AgentToolContext,
        queue: asyncio.Queue[AgentEvent | None],
    ) -> None:
        """Pump events from runner.run into the per-turn queue. Translate
        cancellation and any other failure into a terminal event so the
        subscriber stream always closes cleanly."""
        try:
            async for ev in runner.run(content, ctx):
                await queue.put(ev)
        except asyncio.CancelledError:
            await queue.put(RunCancelled())
            raise
        except Exception as exc:  # noqa: BLE001
            await queue.put(RunError(message=f"{type(exc).__name__}: {exc}"))
        finally:
            await queue.put(None)  # sentinel: stream closed

    @app.post("/investigations/{investigation_id}/messages")
    async def send_message(investigation_id: str, body: _MessageBody) -> StreamingResponse:
        rid, conv = _conversation_for(investigation_id)
        conv.messages.append(Message(role="user", content=body.content))
        conv_rm.update(rid, conv)

        session = await registry.session(investigation_id)
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async with session.lock:
            await _cancel_prior_turn(session)
            ctx = AgentToolContext(
                investigation_id=investigation_id,
                sandbox=sandbox,
                filestore=filestore,
                sync=sync,
                sandbox_spec=SandboxSpec(),
                handle=session.handle,
                # Route lazy-create through the registry so session.handle
                # is set (so idle-kill/close_all can find it) and the
                # restore-after-create hook fires.
                ensure_sandbox_via=lambda: registry.ensure_handle(session),
                # Drive the turn with the investigation's attached agent.
                agent_config=_resolve_agent_config(investigation_id),
            )
            session.current_turn = asyncio.create_task(_drive_run(body.content, ctx, queue))

        async def gen() -> AsyncIterator[str]:
            while True:
                item = await queue.get()
                if item is None:
                    activity.record(
                        "agent_turn_complete",
                        "Agent finished a turn",
                        {"investigation_id": investigation_id},
                    )
                    return
                yield to_sse(item)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete(
        "/investigations/{investigation_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_message(investigation_id: str) -> Response:
        session = await registry.session(investigation_id)
        async with session.lock:
            await _cancel_prior_turn(session)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/investigations/{investigation_id}/close",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def close_investigation(investigation_id: str, body: _CloseInvestigationBody) -> Response:
        inv_rm = spec.get_resource_manager(Investigation)
        try:
            current = inv_rm.get(investigation_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(current, Investigation)
        if body.status is not None:
            # Resolve / Abandon — change status, then tear the session down.
            current.status = Status.RESOLVED if body.status == "resolved" else Status.ABANDONED
            inv_rm.update(investigation_id, current)
            activity.record(
                "investigation_closed",
                f"Closed “{current.title}” as {body.status}",
                {"investigation_id": investigation_id},
            )
        else:
            # Pure close — leave the investigation status untouched, just
            # release its sandbox/kernels (the workspace shuts down).
            activity.record(
                "session_closed",
                f"Closed the workspace for “{current.title}”",
                {"investigation_id": investigation_id},
            )
        await registry.close_session(investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Files API (plan-backend §3.8) ----

    @app.get("/investigations/{investigation_id}/files")
    async def list_files(investigation_id: str, prefix: str = "") -> list[dict]:
        paths = await filestore.ls(investigation_id, prefix)
        out: list[dict] = []
        for p in sorted(paths):
            data = await filestore.read(investigation_id, p)
            out.append({"path": p, "size": len(data)})
        return out

    @app.get("/investigations/{investigation_id}/dirs")
    async def list_dirs(investigation_id: str) -> list[str]:
        """Directory paths (incl. empty ones) for the file tree."""
        return sorted(await filestore.listdir(investigation_id))

    @app.put(
        "/investigations/{investigation_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def write_file(investigation_id: str, path: str, request: Request) -> Response:
        body = await request.body()
        norm = "/" + path.lstrip("/")
        await filestore.write(investigation_id, norm, body)
        activity.record(
            "file_written",
            f"Wrote {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # POST /files/mkdir and /move and /copy are registered before the
    # {path:path} routes so their literal segments can't be swallowed as a
    # path (distinct methods anyway, but keeping them first documents intent).
    @app.post(
        "/investigations/{investigation_id}/files/mkdir",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def make_dir(investigation_id: str, body: _MkdirBody) -> Response:
        norm = "/" + body.path.strip("/")
        try:
            await filestore.mkdir(investigation_id, norm)
        except FileExists as exc:
            raise HTTPException(status_code=409, detail=f"file exists at {norm}") from exc
        activity.record(
            "dir_created",
            f"Created folder {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    async def _transfer(investigation_id: str, src: str, dst: str, *, copy: bool) -> None:
        """Move or copy a file OR a directory subtree. Raises HTTPException
        on missing source / occupied target / moving a dir into itself."""
        if dst == src or dst.startswith(src + "/"):
            raise HTTPException(status_code=400, detail="cannot move a path into itself")
        if await filestore.is_dir(investigation_id, src):
            occupied = await filestore.exists(investigation_id, dst) or await filestore.is_dir(
                investigation_id, dst
            )
            if occupied:
                raise HTTPException(status_code=409, detail=f"target exists: {dst}")
            under = src + "/"
            for p in sorted(await filestore.ls(investigation_id, under)):
                data = await filestore.read(investigation_id, p)
                await filestore.write(investigation_id, dst + p[len(src) :], data)
            await filestore.mkdir(investigation_id, dst)
            for d in await filestore.listdir(investigation_id, under):
                await filestore.mkdir(investigation_id, dst + d[len(src) :])
            if not copy:
                await filestore.rmdir(investigation_id, src)
            return
        try:
            data = await filestore.read(investigation_id, src)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if await filestore.exists(investigation_id, dst) or await filestore.is_dir(
            investigation_id, dst
        ):
            raise HTTPException(status_code=409, detail=f"target exists: {dst}")
        await filestore.write(investigation_id, dst, data)
        if not copy:
            await filestore.delete(investigation_id, src)

    @app.post(
        "/investigations/{investigation_id}/files/move",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def move_file(investigation_id: str, body: _MoveBody) -> Response:
        src = "/" + body.from_.strip("/")
        dst = "/" + body.to.strip("/")
        await _transfer(investigation_id, src, dst, copy=False)
        activity.record(
            "file_moved",
            f"Moved {src} → {dst}",
            {"investigation_id": investigation_id, "path": dst},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/investigations/{investigation_id}/files/copy",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def copy_file(investigation_id: str, body: _MoveBody) -> Response:
        src = "/" + body.from_.strip("/")
        dst = "/" + body.to.strip("/")
        await _transfer(investigation_id, src, dst, copy=True)
        activity.record(
            "file_copied",
            f"Copied {src} → {dst}",
            {"investigation_id": investigation_id, "path": dst},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Global text search / replace (VSCode search panel) ----

    async def _search_files(investigation_id: str, body: _SearchBody):
        try:
            pattern = compile_query(
                body.query,
                regex=body.regex,
                case_sensitive=body.caseSensitive,
                whole_word=body.wholeWord,
            )
        except InvalidQuery as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        paths = sorted(await filestore.ls(investigation_id))
        results: list[tuple[str, bytes, list]] = []
        for p in paths:
            if not path_selected(p, body.include, body.exclude):
                continue
            data = await filestore.read(investigation_id, p)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # skip binary
            matches = search_text(text, pattern)
            if matches:
                results.append((p, data, matches))
        return pattern, results

    @app.post("/investigations/{investigation_id}/search")
    async def search(investigation_id: str, body: _SearchBody) -> list[dict]:
        if not body.query:
            return []
        _pattern, results = await _search_files(investigation_id, body)
        return [
            {
                "path": p,
                "matches": [{"line": m.line, "col": m.col, "text": m.text} for m in matches],
            }
            for p, _data, matches in results
        ]

    @app.post("/investigations/{investigation_id}/replace")
    async def replace(investigation_id: str, body: _ReplaceBody) -> dict:
        if not body.query:
            return {"replaced": 0}
        pattern, results = await _search_files(investigation_id, body)
        replaced = 0
        # Every path in `results` matched per-line via search_text, so the
        # same pattern's subn over the full text always replaces ≥1 — no
        # need to guard on n.
        for p, data, _matches in results:
            text = data.decode("utf-8")
            new_text, n = pattern.subn(body.replacement, text)
            await filestore.write(investigation_id, p, new_text.encode("utf-8"))
            replaced += n
            activity.record(
                "file_written",
                f"Replaced {n} in {p}",
                {"investigation_id": investigation_id, "path": p},
            )
        return {"replaced": replaced}

    @app.delete(
        "/investigations/{investigation_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_file(investigation_id: str, path: str) -> Response:
        norm = "/" + path.lstrip("/")
        if await filestore.is_dir(investigation_id, norm):
            await filestore.rmdir(investigation_id, norm)
            activity.record(
                "dir_deleted",
                f"Deleted folder {norm}",
                {"investigation_id": investigation_id, "path": norm},
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        try:
            await filestore.delete(investigation_id, norm)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        activity.record(
            "file_deleted",
            f"Deleted {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/investigations/{investigation_id}/files/{path:path}")
    async def read_file(investigation_id: str, path: str) -> Response:
        try:
            data = await filestore.read(investigation_id, "/" + path.lstrip("/"))
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Best-effort text/plain when valid UTF-8; otherwise octet-stream.
        try:
            data.decode("utf-8")
            media_type = "text/plain; charset=utf-8"
        except UnicodeDecodeError:
            media_type = "application/octet-stream"
        return Response(content=data, media_type=media_type)

    # ---- Notebook cell execution (plan-backend §7.3) ----

    @app.post(
        "/investigations/{investigation_id}/notebooks/{notebook_path:path}/cells/{idx}/execute"
    )
    async def execute_cell(
        investigation_id: str,
        notebook_path: str,
        idx: int,
        body: _CellExecuteBody,
    ) -> StreamingResponse:
        handle = await kernels.get_or_start(investigation_id, notebook_path)

        async def gen() -> AsyncIterator[str]:
            ev: CellEvent
            async for ev in kernels.execute_cell(handle, body.code):
                yield to_sse(ev)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete(
        "/investigations/{investigation_id}/notebooks/{notebook_path:path}/cells/{idx}/execute",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def interrupt_cell(investigation_id: str, notebook_path: str, idx: int) -> Response:
        handle = kernels.peek(investigation_id, notebook_path)
        if handle is not None:
            await kernels.interrupt(handle)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/investigations/{investigation_id}/notebooks/{notebook_path:path}/kernel/restart",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def restart_kernel(investigation_id: str, notebook_path: str) -> Response:
        handle = kernels.peek(investigation_id, notebook_path)
        if handle is not None:
            await kernels.restart(handle)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Direct sandbox shell — backs the FE Terminal pane ----

    @app.post("/investigations/{investigation_id}/exec")
    async def exec_in_sandbox(investigation_id: str, body: _ExecBody) -> dict[str, object]:
        if not body.cmd:
            raise HTTPException(status_code=422, detail="cmd must be non-empty")
        session = await registry.session(investigation_id)
        handle = await registry.ensure_handle(session)
        result = await sandbox.exec(handle, body.cmd)
        # Best-effort sync any new files back so the sidebar can pick
        # them up on next refresh. Stale handle (kernel killed during
        # the call) is swallowed — the user can re-run.
        with contextlib.suppress(Exception):
            await sync.reverse(investigation_id, handle)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
        }

    # Re-customize the OpenAPI schema now that *all* custom routes are
    # registered. specstar.apply(app) ran earlier and cached a schema that
    # only saw the routes existing at that moment; without this second
    # pass the custom `/investigations/*/messages|files|notebooks|close`
    # routes wouldn't appear in /openapi.json (the routes themselves
    # still work — they're in app.routes — but FE / Swagger discovery
    # would be incomplete).
    spec.openapi(app)

    # Mount the built SPA last so API routes registered above take precedence
    # over the catch-all static handler. If no build exists, skip silently —
    # the API alone is still usable (e.g. via curl or the specstar admin UI).
    if spa_dist is None:
        spa_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if spa_dist.is_dir() and (spa_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=spa_dist, html=True), name="spa")

    return app
