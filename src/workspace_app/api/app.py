from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import msgspec
from agents.tracing import set_trace_processors
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..agent.context import AgentToolContext
from ..agent.provision import ToolDef
from ..files import WorkspaceFiles
from ..filestore.protocol import FileExists, FileNotFound, FileStore
from ..kb.chunker import Chunker, FixedTokenChunker
from ..kb.cited import record_citations
from ..kb.embedder import Embedder, HashEmbedder
from ..kb.ingest import Ingestor
from ..kb.llm import ILlm
from ..kb.retriever import Retriever
from ..kernels import KernelService
from ..monitor import IMonitor, InMemoryMonitor, MonitorProcessor
from ..rca.prompts import load_system_prompt
from ..rca.templates import compose_system_prompt, list_profiles, seed_investigation
from ..resources import (
    AgentConfig,
    Conversation,
    Investigation,
    Message,
    Severity,
    Status,
    register_all,
)
from ..resources.kb import EMBED_DIM, Citation, Collection
from ..sandbox.protocol import OutputSink, Sandbox, SandboxSpec
from ..sync import SandboxSync
from ..users import MockUserDirectory, UserDirectory
from .activity import ActivityLog
from .events import (
    AgentEvent,
    CellEvent,
    to_sse,
)
from .kb_chat_routes import answer_question, kb_progress, register_kb_chat_routes
from .kb_routes import register_kb_routes
from .notifications import notify, register_notification_routes
from .registry import InvestigationRegistry
from .runner import AgentRunner
from .search import InvalidQuery, compile_query, path_selected, search_text
from .turns import ChatTurnEngine, TurnMessage, history_items


def _to_rca_message(m: TurnMessage) -> Message:
    """Map a turn's neutral output to the RCA Conversation model: assistant
    answers are authored by the agent + carry reasoning; tool messages keep the
    call's id/name/args."""
    if m.role == "assistant":
        return Message(
            role="assistant",
            content=m.content,
            author="RCA Agent",
            reasoning=m.reasoning,
            created_at=m.created_at,
            metrics=m.metrics,
        )
    return Message(
        role="tool",
        content=m.content,
        tool_call_id=m.tool_call_id,
        tool_name=m.tool_name,
        tool_args=m.tool_args,
        created_at=m.created_at,
    )


def _now_ms() -> int:
    """Epoch milliseconds — stamped on persisted messages so the agent log's
    timestamps survive a reload (FE `Date` works in ms)."""
    return round(datetime.now(UTC).timestamp() * 1000)


class _SpaStaticFiles(StaticFiles):
    """Serve the built SPA with an HTML5 history fallback: any path that
    isn't a real file resolves to index.html, so refreshing a client-side
    route (e.g. /investigations/{id}) boots the app instead of 404-ing.
    API routes are registered before this mount, so they take precedence."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        from starlette.exceptions import HTTPException as StarletteHTTPException

        served_index = path in ("", ".", "/", "index.html")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            served_index = True  # history fallback → index.html
            response = await super().get_response("index.html", scope)
        # index.html must always be revalidated so a rebuild's new hashed-asset
        # references are picked up; the hashed assets themselves stay cacheable.
        if served_index:
            response.headers["Cache-Control"] = "no-cache"
        return response


class _MessageBody(BaseModel):
    content: str


class _MentionBody(BaseModel):
    user_ids: list[str]
    note: str = ""


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
    kb_embedder: Embedder | None = None,
    kb_chunker: Chunker | None = None,
    kb_llm: ILlm | None = None,
    get_user_id: Callable[[], str] | None = None,
    users: UserDirectory | None = None,
    monitor: IMonitor | None = None,
    spa_dist: Path | None = None,
    root_path: str = "",
    idle_timeout: timedelta = timedelta(hours=8),
    idle_check_interval: timedelta = timedelta(seconds=60),
    mirror_interval: timedelta = timedelta(seconds=5),
    read_file_max_lines: int = 2000,
    read_file_max_chars: int = 200_000,
    history_max_messages: int = 40,
    tool_defs: list[ToolDef] | None = None,
) -> FastAPI:
    # Current-user seam: real deploys inject a reader of the auth middleware;
    # the default is the single dev tenant. UserDirectory resolves ids → people.
    if get_user_id is None:
        get_user_id = lambda: "default-user"  # noqa: E731
    if users is None:
        users = MockUserDirectory()
    if spec is None:
        spec = SpecStar()
        spec.configure(default_now=lambda: datetime.now(UTC))
    # Single-source the current user: specstar stamps created_by with the SAME
    # get_user_id the access layer checks against, so a request's owner can't
    # diverge from who we think they are. (A caller-configured static
    # default_user is deliberately overridden; the clock is left untouched —
    # must precede register_all.)
    spec.configure(default_user=get_user_id)
    register_all(spec)

    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    # The single chokepoint for workspace file ops (agent tools + file routes):
    # routes to the live sandbox (single source of truth) when one is up for the
    # investigation, else to the FileStore snapshot. registry.peek_handle reads
    # liveness without waking — only exec wakes a cold sandbox.
    files = WorkspaceFiles(filestore, sandbox, registry.peek_handle)
    kernels = KernelService()
    activity = ActivityLog()
    # Live telemetry monitor, fed by the OpenAI Agents SDK's own tracing — every
    # run's LLM generations (with token usage), tool calls and agent steps flow
    # through MonitorProcessor in real time (issue #11). Registering replaces
    # the SDK's default (OpenAI-backend) exporter, which we don't use locally.
    monitor = monitor if monitor is not None else InMemoryMonitor()
    set_trace_processors([MonitorProcessor(monitor)])

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

    async def _mirror_sweeper() -> None:
        """Throttle: every ~mirror_interval, persist any warm sandbox the agent
        wrote to since the last sweep into the FileStore snapshot. Coalesces a
        burst of agent writes into one mirror; a crash loses at most a window."""
        try:
            while True:
                await asyncio.sleep(mirror_interval.total_seconds())
                await registry.mirror_warm()
        except asyncio.CancelledError:
            return

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bg = [asyncio.create_task(_idle_killer()), asyncio.create_task(_mirror_sweeper())]
        try:
            yield
        finally:
            for t in bg:
                t.cancel()
            for t in bg:
                with contextlib.suppress(BaseException):
                    await t
            await kernels.shutdown_all()
            await registry.close_all()

    # root_path lives on the app (not just uvicorn.run) so OpenAPI servers and
    # any generated URLs respect a reverse-proxy sub-path mount.
    app = FastAPI(title="RCA 3.0", lifespan=lifespan, root_path=root_path)

    register_notification_routes(app, spec, get_user_id)

    @app.get("/me")
    async def get_me() -> dict:
        """The signed-in user (resolved from the auth seam via the directory)."""
        return users.get(get_user_id()).to_dict()

    @app.get("/users")
    async def list_users() -> list[dict]:
        """The user directory — small enough to fetch whole and filter on the FE
        (mention / share pickers)."""
        return [u.to_dict() for u in users.all_users()]

    @app.get("/templates")
    async def get_templates() -> list[str]:
        """Template profile names the New Investigation picker offers."""
        return list_profiles()

    @app.get("/activity")
    async def get_activity() -> list[dict]:
        """Recent activity feed (newest first) for the notifications popover."""
        return activity.entries()

    @app.get("/monitor")
    async def get_monitor(limit: int | None = None, group_id: str | None = None) -> list[dict]:
        """Recent LLM/agent telemetry events (from the SDK trace stream),
        optionally scoped to one investigation via `group_id`."""
        return monitor.recent(limit=limit, group_id=group_id)

    @app.get("/monitor/stream")
    async def stream_monitor(group_id: str | None = None) -> StreamingResponse:
        """Live SSE feed of telemetry events as the SDK emits them."""
        return StreamingResponse(monitor.sse(group_id=group_id), media_type="text/event-stream")

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
            template_profile=body.template_profile,
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

    # KB chatbot subsystem: ingestion + collection/document/render routes.
    # Embedder/Chunker are swappable; defaults are offline-friendly (production
    # injects a LiteLLM embedder for real semantic search).
    embedder = kb_embedder or HashEmbedder(dim=EMBED_DIM)
    ingestor = Ingestor(spec, chunker=kb_chunker or FixedTokenChunker(), embedder=embedder)
    register_kb_routes(app, spec, ingestor)
    # The chat agent shares the injected runner; its retriever uses the same
    # embedder as ingestion so query and document vectors are comparable.
    # When a KB llm is wired, the retriever gains multi-query + HyDE + rerank.
    kb_retriever = Retriever(spec, embedder=embedder, llm=kb_llm)
    # One turn engine drives every chat surface (RCA workspace + KB chat): one
    # cancellable in-flight turn per conversation, SSE streaming, cancel hook.
    turn_engine = ChatTurnEngine(runner)
    register_kb_chat_routes(app, spec, turn_engine, kb_retriever, get_user_id, history_max_messages)

    async def _ask_kb(
        question: str, emit: OutputSink | None = None, origin_id: str | None = None
    ) -> str:
        """RCA → KB bridge: run the KB agent over every collection and return
        its synthesized, cited answer. Wired onto each RCA turn's context so
        the ask_knowledge_base tool can reach the KB agent. When `emit` is given
        (the RCA run's output sink), the KB agent's searches/reasoning are
        relayed to it live as tool-log lines; `origin_id` is the calling
        investigation, so its citations are logged like the KB chat's."""
        from specstar import QB

        coll_rm = spec.get_resource_manager(Collection)
        ids = [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in coll_rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        ]

        def relay(ev: AgentEvent) -> None:
            if emit is None:
                return
            line = kb_progress(ev)
            if line:
                emit(line.encode())

        def log_cites(cites: list[Citation]) -> None:
            record_citations(
                spec, cites, origin_kind="rca", origin_id=origin_id or "", cited_by=get_user_id()
            )

        return await answer_question(
            runner, kb_retriever, ids, question, on_event=relay, on_citations=log_cites
        )

    conv_rm = spec.get_resource_manager(Conversation)

    def _record_mention(
        investigation_id: str,
        inv_title: str,
        user_ids: list[str],
        note: str,
        *,
        actor: str | None,
        author: str,
    ) -> None:
        """Append a `role="mention"` entry to the conversation (a human-to-human
        "come look", NOT an agent turn) and notify each mentioned user. `actor`
        is the summoner (a user id, or None when the agent did it)."""
        rid, conv = _conversation_for(investigation_id)
        conv.messages.append(
            Message(
                role="mention",
                content=note,
                author=author,
                mentions=list(user_ids),
                created_at=_now_ms(),
            )
        )
        conv_rm.update(rid, conv)
        for uid in user_ids:
            if uid == actor:
                continue  # don't summon yourself
            notify(
                spec,
                recipient=uid,
                kind="mention",
                title=f'You were mentioned in "{inv_title}"',
                body=note,
                link=f"/investigations/{investigation_id}",
                actor=actor,
            )

    def _agent_mention(investigation_id: str, user_ids: list[str], note: str) -> None:
        """The agent's `mention_user` tool reaches this — same summon, authored
        by the agent."""
        inv_rm = spec.get_resource_manager(Investigation)
        inv = inv_rm.get(investigation_id).data
        assert isinstance(inv, Investigation)
        _record_mention(investigation_id, inv.title, user_ids, note, actor=None, author="RCA Agent")

    def _first_agent_config() -> AgentConfig | None:
        """The default agent (issue #2): the first config in the store, by
        creation time. ``None`` only if the store has no configs at all."""
        from specstar import QB

        cfg_rm = spec.get_resource_manager(AgentConfig)
        # Earliest config directly via the query (created_time is a meta sort
        # key) — don't load every config to pick one.
        revs = list(cfg_rm.list_resources(QB.all().sort("created_time").limit(1).build()))
        if not revs:
            return None
        first = revs[0]
        assert isinstance(first.data, AgentConfig)
        return first.data

    def _resolve_agent_config(investigation_id: str) -> AgentConfig | None:
        """The AgentConfig that drives this investigation's turn: the one
        attached to it, else the store's default (first config, issue #2). The
        investigation's template appendix is composed onto the prompt so the
        agent is told about *this* template's starting files."""
        inv_rm = spec.get_resource_manager(Investigation)
        try:
            inv = inv_rm.get(investigation_id).data
        except ResourceIDNotFoundError:
            inv = None
        is_inv = isinstance(inv, Investigation)
        template = inv.template_profile if is_inv else "default"

        cfg: AgentConfig | None = None
        attached_id = inv.attached_agent_config_id if is_inv else None
        if attached_id:
            cfg_rm = spec.get_resource_manager(AgentConfig)
            try:
                attached = cfg_rm.get(attached_id).data
            except ResourceIDNotFoundError:
                attached = None
            if isinstance(attached, AgentConfig):
                cfg = attached
        if cfg is None:  # no attached config (or it was deleted) → store default
            cfg = _first_agent_config()
        if cfg is None:  # empty store — let the runner use its own default
            return None
        composed = compose_system_prompt(cfg.system_prompt, template)
        return msgspec.structs.replace(cfg, system_prompt=composed)

    def _conversation_for(investigation_id: str) -> tuple[str, Conversation]:
        # Indexed lookup by investigation_id (indexed in register_all) — not a
        # full scan.
        from specstar import QB

        for r in conv_rm.list_resources((QB["investigation_id"] == investigation_id).build()):
            data = r.data
            assert isinstance(data, Conversation)
            return r.info.resource_id, data  # ty: ignore[unresolved-attribute]
        rev = conv_rm.create(Conversation(investigation_id=investigation_id))
        got = conv_rm.get(rev.resource_id).data
        assert isinstance(got, Conversation)
        return rev.resource_id, got

    @app.get("/investigations/{investigation_id}/export")
    async def export_investigation(investigation_id: str) -> Response:
        """Download the investigation's full conversation as JSON — every message
        with its reasoning, tool calls (name/args/output), citations, metrics and
        timestamps, plus the case metadata. Read-only (won't create a
        conversation) and curl-friendly, so it doubles as a debug dump."""
        from specstar import QB

        inv_rm = spec.get_resource_manager(Investigation)
        meta: dict[str, object] = {"id": investigation_id}
        try:
            inv = inv_rm.get(investigation_id).data
        except ResourceIDNotFoundError:
            inv = None
        if isinstance(inv, Investigation):
            meta = {
                "id": investigation_id,
                "title": inv.title,
                "owner": inv.owner,
                "severity": inv.severity.value,
                "status": inv.status.value,
                "product": inv.product,
                "topics": list(inv.topics),
            }

        messages: list = []
        for r in conv_rm.list_resources((QB["investigation_id"] == investigation_id).build()):
            assert isinstance(r.data, Conversation)
            messages = msgspec.to_builtins(r.data.messages)
            break

        payload = {"investigation": meta, "exported_at": _now_ms(), "messages": messages}
        filename = f"investigation-{investigation_id}.json"
        return Response(
            content=json.dumps(payload, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/investigations/{investigation_id}/messages")
    async def send_message(investigation_id: str, body: _MessageBody) -> StreamingResponse:
        rid, conv = _conversation_for(investigation_id)
        conv.messages.append(Message(role="user", content=body.content, created_at=_now_ms()))
        conv_rm.update(rid, conv)

        session = await registry.session(investigation_id)
        ctx = AgentToolContext(
            investigation_id=investigation_id,
            sandbox=sandbox,
            filestore=filestore,
            files=files,
            sync=sync,
            sandbox_spec=SandboxSpec(),
            handle=session.handle,
            # Route lazy-create through the registry so session.handle is set
            # (so idle-kill/close_all can find it) and the restore-after-create
            # hook fires.
            ensure_sandbox_via=lambda: registry.ensure_handle(session),
            # Drive the turn with the investigation's attached agent.
            agent_config=_resolve_agent_config(investigation_id),
            # Lets the agent's ask_knowledge_base tool reach the KB agent.
            ask_kb=_ask_kb,
            # Lets the agent's mention_user tool summon a human to this case.
            mention=_agent_mention,
            # read_file truncation caps (deploy config).
            read_file_max_lines=read_file_max_lines,
            read_file_max_chars=read_file_max_chars,
            # Cross-turn memory: prior dialogue (excludes the user msg just added).
            history=history_items(conv.messages[:-1], max_messages=history_max_messages),
            # Provisionable tools (installed into the sandbox on create; the
            # runner exposes the allowed ones). Deploy config.
            tool_defs=tool_defs or [],
        )

        def persist(produced: list[TurnMessage]) -> None:
            # Persist the agent's reply + tool outputs so re-entering the
            # workspace shows them, not just the user's own messages.
            if produced:
                rid2, conv2 = _conversation_for(investigation_id)
                conv2.messages.extend(_to_rca_message(m) for m in produced)
                conv_rm.update(rid2, conv2)
            activity.record(
                "agent_turn_complete",
                "Agent finished a turn",
                {"investigation_id": investigation_id},
            )

        return await turn_engine.stream(investigation_id, body.content, ctx, on_complete=persist)

    @app.delete(
        "/investigations/{investigation_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_message(investigation_id: str) -> Response:
        await turn_engine.cancel(investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/investigations/{investigation_id}/mentions",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def mention_users(investigation_id: str, body: _MentionBody) -> Response:
        """@-mention people in the chat — a pure "come look" summon (does NOT
        run the agent): records a mention entry + notifies each user."""
        inv_rm = spec.get_resource_manager(Investigation)
        try:
            inv = inv_rm.get(investigation_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(inv, Investigation)
        me = get_user_id()
        _record_mention(investigation_id, inv.title, body.user_ids, body.note, actor=me, author=me)
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
            # Notify the owner + watchers (except whoever did it).
            actor = get_user_id()
            for uid in {current.owner, *current.members} - {actor}:
                notify(
                    spec,
                    recipient=uid,
                    kind="status",
                    title=f"{current.title} → {body.status}",
                    link=f"/investigations/{investigation_id}",
                    actor=actor,
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
        turn_engine.forget(investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Files API (plan-backend §3.8) ----

    @app.get("/investigations/{investigation_id}/files")
    async def list_files(investigation_id: str, prefix: str = "") -> list[dict]:
        paths = await files.ls(investigation_id, prefix)
        out: list[dict] = []
        for p in sorted(paths):
            data = await files.read(investigation_id, p)
            out.append({"path": p, "size": len(data)})
        return out

    @app.get("/investigations/{investigation_id}/dirs")
    async def list_dirs(investigation_id: str) -> list[str]:
        """Directory paths (incl. empty ones) for the file tree."""
        return sorted(await files.listdir(investigation_id))

    @app.post("/investigations/{investigation_id}/files/refresh")
    async def refresh_files(investigation_id: str) -> dict:
        """Force-mirror the live sandbox to the snapshot now (don't wait for the
        ≤window throttle sweep) — the explicit 'refresh' action. No-op cold."""
        await registry.flush(investigation_id)
        return {"ok": True}

    @app.put(
        "/investigations/{investigation_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def write_file(investigation_id: str, path: str, request: Request) -> Response:
        body = await request.body()
        norm = "/" + path.lstrip("/")
        await files.write(investigation_id, norm, body)
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
            await files.mkdir(investigation_id, norm)
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
        if await files.is_dir(investigation_id, src):
            occupied = await files.exists(investigation_id, dst) or await files.is_dir(
                investigation_id, dst
            )
            if occupied:
                raise HTTPException(status_code=409, detail=f"target exists: {dst}")
            under = src + "/"
            for p in sorted(await files.ls(investigation_id, under)):
                data = await files.read(investigation_id, p)
                await files.write(investigation_id, dst + p[len(src) :], data)
            await files.mkdir(investigation_id, dst)
            for d in await files.listdir(investigation_id, under):
                await files.mkdir(investigation_id, dst + d[len(src) :])
            if not copy:
                await files.rmdir(investigation_id, src)
            return
        try:
            data = await files.read(investigation_id, src)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if await files.exists(investigation_id, dst) or await files.is_dir(investigation_id, dst):
            raise HTTPException(status_code=409, detail=f"target exists: {dst}")
        await files.write(investigation_id, dst, data)
        if not copy:
            await files.delete(investigation_id, src)

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
        paths = sorted(await files.ls(investigation_id))
        results: list[tuple[str, bytes, list]] = []
        for p in paths:
            if not path_selected(p, body.include, body.exclude):
                continue
            data = await files.read(investigation_id, p)
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
            await files.write(investigation_id, p, new_text.encode("utf-8"))
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
        if await files.is_dir(investigation_id, norm):
            await files.rmdir(investigation_id, norm)
            activity.record(
                "dir_deleted",
                f"Deleted folder {norm}",
                {"investigation_id": investigation_id, "path": norm},
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        try:
            await files.delete(investigation_id, norm)
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
            data = await files.read(investigation_id, "/" + path.lstrip("/"))
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
        # The sandbox is the source of truth, so the file routes already see any
        # files the command created; mirror them to the snapshot now for
        # durability. Stale handle (killed mid-call) is swallowed — re-run.
        with contextlib.suppress(Exception):
            await registry.flush(investigation_id)
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
        app.mount("/", _SpaStaticFiles(directory=spa_dist, html=True), name="spa")

    return app
