from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import msgspec
from agents.tracing import set_trace_processors
from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError
from starlette.datastructures import UploadFile

from ..agent.config_catalog import AgentConfigCatalog
from ..agent.context import AgentToolContext
from ..config.schema import EnhancementSettings
from ..files import WorkspaceFiles
from ..files.zip_download import (
    DownloadPrepared,
    prepare_zip,
    prepared_path,
    safe_zip_filename,
    stream_prepared_zip,
    subtree_arcname,
    write_zip_members,
)
from ..filestore.blob_gc import register_gc_lease, run_blob_gc
from ..filestore.protocol import FileExists, FileNotFound, FileStore
from ..health import CheckRegistry, CheckResult
from ..health.replay import ReplayService
from ..health.service import HealthService
from ..kb.chunker import Chunker, FixedTokenChunker
from ..kb.cited import record_citations
from ..kb.embedder import Embedder, HashEmbedder
from ..kb.ingest import Ingestor
from ..kb.llm import ILlm
from ..kb.retriever import Enhancements, Retriever
from ..kb.vlm import IVlm, VlmDescriber
from ..kernels import KernelService
from ..monitor import IMonitor, InMemoryMonitor, MonitorProcessor
from ..observability.boot import boot_step
from ..resources import (
    AgentConfig,
    CheckRun,
    Conversation,
    Message,
)
from ..resources.kb import EMBED_DIM, Citation, Collection, KbChat, SourceDoc
from ..sandbox.protocol import OutputSink, Sandbox, SandboxSpec
from ..sync import SandboxSync
from ..tooling.registry import PackageInfo
from ..users import MockUserDirectory, UserDirectory
from ..workflow.capabilities import CollectionNotFound, ingest_to_collection, upsert_context_card
from ..workflow.credential import CredentialBroker
from ..workflow.discovery import load_preflight_callable, load_run_callable
from ..workflow.handle import WorkflowHandle
from ..workflow.inputs import resolve_inputs
from ..workflow.orchestrator import (
    NotAwaitingDecision,
    WorkflowOrchestrator,
)
from ..workflow.preflight import can_run as _preflight_can_run
from ..workflow.run import RunStatus, WorkflowRun
from .activity import ActivityLog
from .card_gen_routes import register_card_gen_routes
from .context_card_routes import register_context_card_actions, register_context_card_routes
from .events import (
    AgentEvent,
    CellEvent,
    FileChanged,
    UserMessage,
    to_sse,
)
from .health_routes import (
    register_health_routes,
    register_replay_routes,
    register_sanity_routes,
)
from .kb_chat_routes import (
    EnhancementsInput,
    answer_question,
    kb_progress,
    register_kb_chat_routes,
    to_caller_enhancements,
)
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
            stopped_reason=m.stopped_reason,  # #113: repetition-stop notice survives reload
        )
    if m.role == "error":
        # Issue #37: a terminal failure, persisted so a reloaded thread
        # shows it. `error_kind` drives the next-turn history policy.
        return Message(
            role="error",
            content=m.content,
            error_kind=m.error_kind,
            created_at=m.created_at,
        )
    return Message(
        role="tool",
        content=m.content,
        tool_call_id=m.tool_call_id,
        tool_name=m.tool_name,
        tool_args=m.tool_args,
        tool_display=m.tool_display,
        created_at=m.created_at,
    )


def _undo_cut_index(messages: list[Message], turns: int) -> int:
    """The index to truncate `messages` at to drop the last `turns` whole
    turns (issue #38). A turn is delimited by a `role="user"` prompt —
    everything after it (assistant / tool / error / mention) belongs to
    that turn until the next prompt. Returns 0 when undoing more turns
    than exist (clears the conversation)."""
    user_idxs = [i for i, m in enumerate(messages) if m.role == "user"]
    if turns >= len(user_idxs):
        return 0
    return user_idxs[-turns]


_MARKER_RE = re.compile(r"\[(\d+)\]")


def _bubble_kb_citations(content: str, seen_subagent: list[list[Citation]]) -> list[Citation]:
    """Pick KB citations to attach to an assistant message that follows
    one or more sub-agent calls (ask_knowledge_base / infer_modules /
    any future KB-citing tool) in the same turn. Two modes:

    - **Explicit quotes** — content has `[N]` markers. Each marker is
      matched to the corresponding citation from the calls SEEN SO FAR;
      most-recent call wins on collisions (two sub-agent calls both
      having `[1]` → the latest one's `[1]` is the live reference).
      Returns only the matched citations, in marker order.

    - **Implicit synthesis** — content has no `[N]` markers but a
      sub-agent did run. Common case: the agent forwards the KB result
      into a file (`write_file ./report.v1.md`) without re-quoting the
      markers in chat prose; without a fallback the chat would render
      the outer answer as citation-less even though every claim came
      from the KB. Returns the LATEST sub-agent call's citations
      (deduped by chunk).

    Empty when `seen_subagent` is empty — caller guards on that to
    avoid smearing arbitrary citations onto pre-sub-agent messages.
    """
    markers = {int(m.group(1)) for m in _MARKER_RE.finditer(content)}
    if not markers:
        # Implicit synthesis — latest call wins, dedupe by chunk.
        if not seen_subagent:
            return []
        seen: set[tuple[str, int]] = set()
        out: list[Citation] = []
        for c in seen_subagent[-1]:
            key = (c.document_id, c.start)
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        out.sort(key=lambda c: c.marker)
        return out
    picked: dict[int, Citation] = {}
    for call in reversed(seen_subagent):
        for c in call:
            if c.marker in markers and c.marker not in picked:
                picked[c.marker] = c
    return [picked[k] for k in sorted(picked)]


def _now_ms() -> int:
    """Epoch milliseconds — stamped on persisted messages so the agent log's
    timestamps survive a reload (FE `Date` works in ms)."""
    return round(datetime.now(UTC).timestamp() * 1000)


def _dt_ms(dt: datetime) -> int:
    """A specstar revision time (`updated_time`/`created_time`) → epoch ms."""
    return round(dt.timestamp() * 1000)


class _SpaStaticFiles(StaticFiles):
    """Serve the built SPA with an HTML5 history fallback: any path that
    isn't a real file resolves to index.html, so refreshing a client-side
    route (e.g. /a/{slug}/items/{id}) boots the app instead of 404-ing.
    API routes are registered before this mount, so they take precedence."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        from starlette.exceptions import HTTPException as StarletteHTTPException

        served_index = path in ("", ".", "/", "index.html")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # #177: every backend route lives under /api. An unmatched /api/*
            # request is a real API miss — let it 404 as JSON, NOT the SPA
            # history fallback (returning index.html would mask broken calls).
            if path == "api" or path.startswith("api/"):
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
    # Per-message reasoning effort from the UI selector; None → model default.
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Knowledge-search depth from the composer picker. Applies to this
    # turn's ask_knowledge_base lookups (the bridge forwards it to the
    # KB sub-agent); None → operator default.
    enhancements: EnhancementsInput | None = None


class _UndoOut(BaseModel):
    """Result of an undo: the conversation's new length + how many
    messages the undone turns removed."""

    message_count: int
    removed: int


class _FileEntry(BaseModel):
    """One workspace file in the listing (#205). ``read_only`` flags files the IDE
    must render non-editable — files under the reserved ``.readonly/`` directory,
    server-enforced (PUT is refused). A computed convention, so no per-file metadata."""

    path: str
    size: int
    read_only: bool


class _WorkspaceUsage(BaseModel):
    """A workspace's total storage usage vs its quota (#245), for the upload
    usage bar. ``used`` is the durable logical byte total; ``quota`` of 0 means
    no quota (the FE then hides the bar)."""

    used: int
    quota: int


_READONLY_DIR = ".readonly"
# #227: how often the background index-sweeper recovers stuck fan-out runs, and
# how long a run may go without progress before its missing batches are declared
# failed. The grace must exceed one batch's worst case (≈ the broker's 30-min
# consumer-ack timeout) so a slow-but-live run is never falsely failed.
_INDEX_SWEEP_INTERVAL_S = 300.0
_INDEX_STUCK_AFTER_S = 3600.0


def _is_readonly_path(path: str) -> bool:
    """#205: files under the reserved ``.readonly/`` directory are server-enforced
    read-only — the IDE renders them non-editable and a PUT is refused. A computed
    convention (like the ``/.workflow/`` journal folder, #136), so no per-file
    metadata or migration is needed; any segment named ``.readonly`` qualifies."""
    return _READONLY_DIR in path.strip("/").split("/")


async def _stream_upload_to_store(
    workspace_id: str,
    path: str,
    request: Request,
    files: WorkspaceFiles,
    max_file_size: int,
    workspace_quota: int = 0,
) -> None:
    """Stream the request body to a staging file on disk, enforcing the
    single-file cap (#219) and the per-workspace quota (#245) as bytes arrive,
    then hand the file to the store. The staging file means a multi-GB upload
    never sits whole in RAM; both caps are checked mid-stream so an over-limit
    upload is rejected without buffering it all. ``max_file_size`` of 0 disables
    the single-file cap; ``workspace_quota`` of 0 disables the total quota.

    The quota credits back the bytes of the file being overwritten (a replace,
    not an add), so re-uploading a same-size file never trips it."""
    # Headroom for this path, fetched once up front; an overwrite is a replace.
    # None ⇒ quota disabled. Computed against the durable store, so the sandbox
    # mirror (which writes the store directly, not via this endpoint) isn't gated.
    remaining = await files.remaining_quota(workspace_id, path, workspace_quota)
    fd, name = tempfile.mkstemp(prefix="ws-upload-")
    tmp = Path(name)
    try:
        size = 0
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if max_file_size and size > max_file_size:
                    raise HTTPException(status_code=413, detail="file exceeds the size limit")
                if remaining is not None and size > remaining:
                    used = await files.workspace_usage(workspace_id)
                    raise HTTPException(
                        status_code=507,
                        detail={
                            "error": "workspace_quota_exceeded",
                            "used": used,
                            "quota": workspace_quota,
                            "attempted": size,
                        },
                    )
                f.write(chunk)
        await files.write_from_path(workspace_id, path, tmp, request.headers.get("content-type"))
    finally:
        tmp.unlink(missing_ok=True)


class _CreateChatBody(BaseModel):
    # #topic-hub P7 (manual §3): open a new FREE chat in an item. Title is optional;
    # a workflow chat is opened by the run endpoint (P8), not here.
    title: str = ""


class _RenameChatBody(BaseModel):
    # #132: manual rename of a chat from the manage modal.
    title: str


class _ChatInfo(BaseModel):
    """One chat in an item's multi-chat list (manual §3)."""

    chat_id: str
    title: str
    run_id: str | None
    created_ms: int | None
    message_count: int
    is_default: bool
    name_hint: str = ""
    """First user message (whitespace-collapsed, truncated) so the FE can label an
    unnamed chat without fetching its thread (#132). "" until the first user turn."""
    status: str | None = None
    """The driving `WorkflowRun.status` for a workflow chat (running / awaiting_human /
    done / …), so the list shows a status badge without per-run polling (#132). None
    for a free chat."""
    last_activity_ms: int | None = None
    """Epoch ms of the chat's last write (specstar `updated_time`) — the recency sort
    key for the list (#132). None only if the revision time is unavailable."""


def _chat_name_hint(conv: Conversation, *, limit: int = 60) -> str:
    """First user message of a chat, whitespace-collapsed and truncated — the FE's
    fallback display name for an unnamed chat (#132). "" when no user turn yet."""
    for m in conv.messages:
        if m.role == "user" and m.content.strip():
            return " ".join(m.content.split())[:limit]
    return ""


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


class _CloseItemBody(BaseModel):
    # null → pure close; a string must be one of the App manifest's
    # `lifecycle.closing_states` (validated against the manifest, not here).
    status: str | None = None


class _DecisionBody(BaseModel):
    # #100: a human's answer at a workflow `human_gate` (manual §10). `choice` ∈
    # the gate's `allow` (e.g. approve/reject); `input` is an optional revision.
    choice: str
    input: str = ""


class _IngestBody(BaseModel):
    # #100: a deterministic node's ingest capability call (manual §8).
    collection: str
    path: str


class _CardBody(BaseModel):
    # topic-hub P9: a deterministic node's create-context-card capability call (§8).
    collection: str
    keys: list[str] = Field(default_factory=list)
    title: str = ""
    body: str = ""


class _PreflightCheckOut(BaseModel):
    # #283: one pre-flight checklist line in the launch dialog.
    label: str
    ok: bool
    severity: str  # "required" | "advisory"
    reason: str = ""


class _PhaseOut(BaseModel):
    id: str
    title: str = ""


class _PreflightPreviewOut(BaseModel):
    # #283: what the launch dialog renders BEFORE a run starts — the workflow's
    # identity + phases, plus (when the author wrote a ``preflight``) a human summary
    # of what the run will do and a checklist of its preconditions.
    workflow_id: str
    title: str
    description: str
    phases: list[_PhaseOut]
    summary: str
    checks: list[_PreflightCheckOut]
    can_run: bool
    has_preflight: bool


async def _staged_run_uploads(
    request: Request, workflow_id: str
) -> tuple[str, list[tuple[str, bytes]]]:
    """#197: parse a workflow-run trigger's optional ``multipart/form-data`` body into
    a (resolved ``workflow_id``, staged ``[(workspace_path, bytes)]``) pair so the caller
    can write the files THEN start the run.

    Each ``file`` part's filename IS its workspace path (sub-dirs allowed); ``canonical_path``
    resolves ``.``/``..`` and a path escaping the root raises 400. ALL parts are validated
    before the caller writes any, so one bad upload aborts the whole trigger (nothing is
    half-written, no run starts). The query ``workflow_id`` wins; absent it a ``workflow_id``
    form field is honoured. With no multipart body this is a no-op — the plain trigger the
    FE makes (query param only, empty body) is left completely untouched."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return workflow_id, []
    from ..kb.doc_id import canonical_path

    form = await request.form()
    if not workflow_id:
        field = form.get("workflow_id")
        if isinstance(field, str):
            workflow_id = field
    staged: list[tuple[str, bytes]] = []
    for part in form.getlist("file"):
        # A `file` field carrying a plain string (no filename) is not an upload — skip it.
        if not isinstance(part, UploadFile) or not part.filename:
            continue
        try:
            rel = canonical_path(part.filename)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"upload path escapes the workspace: {part.filename!r}",
            ) from exc
        staged.append(("/" + rel, await part.read()))
    return workflow_id, staged


async def _promote_chat_to_kb(
    *,
    ingestor: Ingestor,
    insights_collection_id: str,
    actor: str,
    investigation_id: str,
    investigation_title: str,
    messages: list[Message],
) -> list[str]:
    """Run `ingestor.ingest_chat` in a thread (the LLM call is blocking).
    Swallows exceptions — chat → knowledge is best-effort, never block /close
    or surface as a hard failure to the FE. Returns the SourceDoc ids written
    (or `[]` on error / inconclusive chat). Logs failures."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        msgs = [
            {
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name or "",
            }
            for m in messages
        ]
        return await asyncio.to_thread(
            ingestor.ingest_chat,
            collection_id=insights_collection_id,
            user=actor,
            investigation_id=investigation_id,
            investigation_title=investigation_title,
            messages=msgs,
        )
    except Exception:  # noqa: BLE001 — best-effort; don't propagate
        logger.exception("chat → knowledge promote failed for %s", investigation_id)
        return []


def _ensure_insights_collection(spec: SpecStar, name: str) -> str:
    """Idempotently ensure the chat-insights collection exists, returning its
    id. Used by the chat→knowledge promote path (P2) — every server boot
    runs this so the target collection is always available.

    `Collection.name` isn't indexed; we walk all collections at startup
    (small enumeration) rather than add an index just for this lookup."""
    from specstar import QB

    rm = spec.get_resource_manager(Collection)
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        if r.data.name == name:  # ty: ignore[unresolved-attribute]
            return r.info.resource_id  # ty: ignore[unresolved-attribute]
    return rm.create(Collection(name=name)).resource_id


def create_app(
    *,
    spec: SpecStar,
    sandbox: Sandbox,
    filestore: FileStore,
    runner: AgentRunner,
    agent_config_catalog: AgentConfigCatalog | None = None,
    kb_embedder: Embedder | None = None,
    kb_code_embedder: Embedder | None = None,  # P3.0 code-specialised embedder
    kb_chunker: Chunker | None = None,
    kb_pipeline: object | None = None,  # llama_index.core.ingestion.IngestionPipeline
    kb_chat_pipeline: object | None = None,  # P2 chat → knowledge IngestionPipeline
    # Issue #39: the parser registry routing uploads to IParsers. None
    # ⇒ the Ingestor's bundled-only fallback (no VLM). Production
    # passes factories.get_parser_registry(settings) so custom parsers
    # + the VLM-backed ones (image / PDF visual pages / slides) are
    # wired.
    kb_parser_registry: object | None = None,  # kb.parsers.ParserRegistry
    # Issue #51: the sanity-check registry. None ⇒ the diagnostics
    # endpoint serves an empty panel and startup probes are no-ops.
    # Production passes factories.get_check_registry(settings).
    check_registry: object | None = None,  # health.CheckRegistry
    # Issue #51 P4: replay diagnostics. None ⇒ the replay endpoints
    # answer 503. Production passes factories.get_replay_service(...).
    replay_service: ReplayService | None = None,
    # Model-sanity battery (Diagnostics matrix). None ⇒ the /sanity routes
    # aren't mounted (the matrix is a live-LLM probe). Production passes
    # factories.get_sanity_llm_factory / get_sanity_models. The factory is the
    # SAME (model, level) -> ILlm seam kb_search runs on.
    sanity_llm_factory: object | None = None,  # Callable[[str, str], ILlm]
    sanity_models: list[str] | None = None,
    # #231: LLM-as-judge for the sanity matrix (ai_grade/ai_note + per-model
    # verdict). None ⇒ AI scoring off. __main__ passes get_sanity_judge_llm.
    sanity_judge_llm: ILlm | None = None,
    insights_collection_name: str = "Investigations Knowledge",
    kb_llm: ILlm | None = None,
    # #175: the LLM that drafts context cards from documents (自動 context card).
    # None ⇒ a no-op drafter (the feature stays mounted but proposes nothing).
    # __main__ passes factories.get_card_drafter_llm(settings).
    card_drafter_llm: ILlm | None = None,
    # #112: the VLM describer the `read_image` agent tool uses to read a
    # workspace image. None ⇒ no VLM configured; `read_image` reports it's
    # unavailable. __main__ passes factories.get_kb_describer(settings) — the
    # same describer KB ingestion's VLM parsers use.
    vlm_describer: VlmDescriber | None = None,
    # #284: the multimodal model the `make_deck` tool drives (sees rendered
    # slides + writes pptxgenjs). None ⇒ make_deck reports it's unavailable.
    # __main__ passes factories.get_designed_pptx_vlm(settings).
    deck_vlm: IVlm | None = None,
    get_user_id: Callable[[], str] | None = None,
    users: UserDirectory | None = None,
    monitor: IMonitor | None = None,
    spa_dist: Path | None = None,
    root_path: str = "",
    idle_timeout: timedelta = timedelta(hours=8),
    idle_check_interval: timedelta = timedelta(seconds=60),
    mirror_interval: timedelta = timedelta(seconds=5),
    # P3.0: background code-repo sync sweeper interval. None ⇒ sweeper
    # disabled (manual /sync only). __main__ derives this from
    # Settings.sync_check_interval_sec.
    code_sync_check_interval: timedelta | None = None,
    read_file_max_lines: int = 2000,
    read_file_max_chars: int = 200_000,
    exec_output_max_chars: int = 30_000,
    # #219: single-file upload cap in bytes (0 ⇒ no cap). Streaming keeps RAM
    # flat, so this guards disk + sandbox-wake cost. Threaded from
    # settings.filestore.max_file_size; per-workspace total quota is #245.
    max_file_size: int = 2 * 1024 * 1024 * 1024,
    # #245: per-workspace total-size quota in bytes (0 ⇒ no quota). Gated at the
    # user-facing upload/edit endpoints; threaded from
    # settings.filestore.workspace_quota.
    workspace_quota: int = 20 * 1024 * 1024 * 1024,
    # #245: blob-GC sweeper. `gc_interval` None ⇒ off; `gc_t1`/`gc_t2` are the
    # fresh-blob grace and quarantine dwell passed to `SpecStar.gc(reconcile)`.
    gc_interval: timedelta | None = timedelta(hours=1),
    gc_t1: str = "1h",
    gc_t2: str = "24h",
    # Step budgets for the wiki agents (#50) — far higher than a chat reply's
    # ~10 turns; a maintenance pass writes several pages, the reader navigates.
    wiki_maintainer_max_turns: int = 40,
    wiki_reader_max_turns: int = 24,
    # Optional model/endpoint override for the wiki agents — point them at a
    # model that reliably calls tools (small models narrate instead of writing).
    # Empty ⇒ the bundled wiki config's default model / inherited endpoint.
    wiki_model: str = "",
    wiki_llm_base_url: str = "",
    wiki_llm_api_key: str = "",
    # #59/#82: the durable background-queue backend (a specstar message-queue
    # factory), shared by wiki maintenance AND KB indexing. None ⇒ the
    # specstar-backed Simple queue (multipod via the shared backend). __main__
    # passes the config-selected factory (message_queue.kind: simple|rabbitmq).
    message_queue_factory: object | None = None,
    # #66: the infer_modules tool's per-step config — the KB query depth +
    # reasoning effort each classification sub-agent runs with (its OWN config,
    # not the composer's), and how many steps classify concurrently.
    infer_modules_enhancements: Enhancements | None = None,
    infer_modules_reasoning_effort: str | None = None,
    infer_modules_parallelism: int = 16,
    # The KB collection NAME infer_modules' per-step classifier searches.
    # "" ⇒ search ALL collections (backward-compatible). Resolved to ids once
    # per turn (not per step).
    infer_modules_collection: str = "",
    history_max_messages: int = 40,
    # Token budget for the replayed history (#45); 0 disables it.
    history_max_context_tokens: int = 24_000,
    # Operator-level KB retrieval enhancement defaults + LLM ceilings.
    # `None` ⇒ bundled `EnhancementSettings()` (light: expand=1, hyde=0,
    # rerank=on). __main__ threads `settings.kb.retrieval.enhancements`.
    kb_retrieval_enhancements: EnhancementSettings | None = None,
    # #195: per-turn cap on `kb_search` calls for the KB chat turn + the
    # ask_knowledge_base bridge. `None` ⇒ unlimited (also what other surfaces
    # like Topic Hub use). __main__ threads `settings.kb.max_searches_per_turn`
    # (default 3); the default here stays None so tests that don't pass it keep
    # the unlimited pre-#195 behaviour.
    kb_max_searches_per_turn: int | None = None,
    packages: list[PackageInfo] | None = None,
    prebuilt_dir: Path | None = None,
    # #100: workflow run limits (manual §16/§17). Global concurrency cap (runs
    # queue as `pending` when full), a per-run max-steps budget (guards runaway
    # loops), and an optional per-run wall-clock cap (None ⇒ no limit).
    workflow_concurrency: int = 8,
    workflow_max_steps: int = 1000,
    workflow_run_timeout: timedelta | None = None,
    workflow_step_timeout: timedelta | None = None,
    # Per-item run-history retention (manual §16): keep at most this many runs, pruning
    # the oldest terminal ones when a new run starts. 0 ⇒ keep all.
    workflow_keep_last_runs: int = 0,
) -> FastAPI:
    # Current-user seam: real deploys inject a reader of the auth middleware;
    # the default is the single dev tenant. UserDirectory resolves ids → people.
    # The same `get_user_id` should have been threaded into `make_spec`
    # (via `factories.get_spec(settings, get_user_id=...)`) so specstar
    # stamps `created_by` with the same callable the access layer checks
    # against — otherwise the request's owner can diverge from who we
    # think they are.
    if get_user_id is None:
        get_user_id = lambda: "default-user"  # noqa: E731
    if users is None:
        users = MockUserDirectory()
    # `None` catalog → build one from bundled defaults so test fixtures /
    # sites that don't care about the picker still get a working KB chat
    # (`catalog.kb_chat()` populated) + a 3-entry RCA picker. Production
    # wires `factories.get_agent_config_catalog(settings)` which honours
    # config.yaml; tests typically pass `AgentConfigCatalog([...])` to
    # pin specific configs.
    from ..config.catalog_build import build_catalog as _build_bundled_catalog
    from ..config.schema import Settings as _BundledSettings

    _bundled = _build_bundled_catalog(_BundledSettings(), config_dir=None)
    catalog = agent_config_catalog if agent_config_catalog is not None else _bundled
    # #89 (P3d): fail the boot loud if any App's app.json contradicts its
    # function toggles (e.g. `exec` in tools but `sandbox:false`) — decision 11.
    from ..apps.catalog import AppCatalog, validate_all_apps

    validate_all_apps()
    # #89 (P4d): the per-turn resolve for new per-App items (RcaInvestigation, …)
    # goes through this; presets come from the same place the agent catalog's did.
    app_catalog = AppCatalog(presets=catalog.presets() or _bundled.presets())
    # Issue #32: KB chat is a list. Legacy `AgentConfigCatalog([...])`
    # construction has no kb_chats — fall back to the bundled list so
    # KB chat works in every test path.
    kb_agent_configs = catalog.kb_chats() or _bundled.kb_chats()
    assert kb_agent_configs  # bundled always populates kb_chats
    # Default for the RCA→KB ask_knowledge_base bridge (no picker
    # context — the RCA agent doesn't pick a KB model per turn).
    default_kb_agent_config = kb_agent_configs[0]
    # Same shape for infer_modules: fall back to bundled when the
    # supplied catalog didn't wire one (legacy positional-list tests).
    default_infer_modules_config = catalog.infer_modules() or _bundled.infer_modules()
    assert default_infer_modules_config  # bundled always populates infer_modules

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

    async def _code_sync_sweeper() -> None:
        """Re-clone any code Collection whose `sync_interval_hours` has
        elapsed. The actual clone runs in a worker thread so the loop stays
        responsive. Per-collection sync failures are caught inside `tick`."""
        from ..kb.code_repo import CodeRepoIngestor, CodeRepoSweeper

        assert code_sync_check_interval is not None  # gated by caller
        sweeper = CodeRepoSweeper(spec, code_repo=CodeRepoIngestor(spec, ingestor=ingestor))
        try:
            while True:
                await asyncio.sleep(code_sync_check_interval.total_seconds())
                await asyncio.to_thread(sweeper.tick)
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

    async def _index_sweeper() -> None:
        """#227: periodically recover stuck index fan-outs — a lost finalize
        trigger (winner crashed) or a dead-lettered batch — so a doc never wedges
        in 'indexing'. Queue-agnostic (CAS), idempotent, and cheap (one indexed
        query), run off the loop since it does blocking specstar I/O."""
        try:
            while True:
                await asyncio.sleep(_INDEX_SWEEP_INTERVAL_S)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        app.state.index_coordinator.sweep_stuck_runs,
                        stuck_after_seconds=_INDEX_STUCK_AFTER_S,
                    )
        except asyncio.CancelledError:
            return

    async def _blob_gc_sweeper() -> None:
        """#245: periodically reclaim orphaned blobs (deleted files' content) via
        specstar's ref-count GC, so the per-workspace quota stays honest. A CAS
        lease means only ONE pod runs the full (deleting) reconcile per window;
        the others no-op. Run off the loop — reconcile does blocking specstar I/O.
        ``gc_interval`` gates this caller (None ⇒ no task)."""
        assert gc_interval is not None
        ttl_ms = int(gc_interval.total_seconds() * 1000)
        try:
            while True:
                await asyncio.sleep(gc_interval.total_seconds())
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(run_blob_gc, spec, t1=gc_t1, t2=gc_t2, ttl_ms=ttl_ms)
        except asyncio.CancelledError:
            return

    # Issue #51: the sanity-check service — latest results in memory,
    # every executed probe persisted as a CheckRun row (audit trail).
    def _persist_check_run(result: CheckResult) -> None:
        spec.get_resource_manager(CheckRun).create(
            CheckRun(
                check_id=result.check_id,
                status=result.status,
                detail=result.detail,
                latency_ms=result.latency_ms,
                checked_at=result.checked_at,
            )
        )

    health_service = HealthService(
        check_registry if isinstance(check_registry, CheckRegistry) else CheckRegistry(),
        on_result=_persist_check_run,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Issue #51 / Q2: the fast (connectivity-grade) probes block
        # boot — an operator sees a dead embedder before the first
        # request; the full capability round runs in the background.
        # #208: each step narrates (→/✓) so a stall in the lifespan names itself
        # instead of looking like a silent hang.
        with boot_step("health: connectivity checks"):
            await asyncio.to_thread(health_service.run_fast_sync)
        # #59: every pod runs a wiki-maintenance consumer so the shared,
        # partitioned job queue drains regardless of which pod enqueued (and
        # even on pods that received no uploads). Idempotent + non-blocking.
        with boot_step("start wiki-maintenance consumer"):
            app.state.wiki_coordinator.start_consuming()
        # #82: same — every pod runs an indexing consumer draining the shared,
        # partitioned IndexJob queue (so a slow embed never starves the request path).
        with boot_step("start indexing consumer"):
            app.state.index_coordinator.start_consuming()
        # Model-sanity battery consumer (when wired) — drains SanityRun jobs.
        if app.state.sanity_coordinator is not None:
            with boot_step("start model-sanity consumer"):
                app.state.sanity_coordinator.start_consuming()
        # #175: every pod drains the shared context-card generation queue too.
        with boot_step("start context-card generation consumer"):
            app.state.card_gen_coordinator.start_consuming()
        bg = [asyncio.create_task(_idle_killer()), asyncio.create_task(_mirror_sweeper())]
        bg.append(asyncio.create_task(_index_sweeper()))  # #227 fan-out stuck-run recovery
        bg.append(asyncio.create_task(health_service.run_round()))
        if code_sync_check_interval is not None:
            bg.append(asyncio.create_task(_code_sync_sweeper()))
        if gc_interval is not None:
            # #245: seed the CAS lease, then run the orphan-blob GC on a schedule.
            register_gc_lease(spec)
            bg.append(asyncio.create_task(_blob_gc_sweeper()))
        try:
            yield
        finally:
            for t in bg:
                t.cancel()
            for t in bg:
                with contextlib.suppress(BaseException):
                    await t
            # Drain in-flight wiki maintenance before exit (bounded). Pending
            # jobs are durable — they survive to be picked up after restart.
            with contextlib.suppress(BaseException):
                await app.state.wiki_coordinator.aclose()
            with contextlib.suppress(BaseException):
                await app.state.index_coordinator.aclose()
            if app.state.sanity_coordinator is not None:
                with contextlib.suppress(BaseException):
                    await app.state.sanity_coordinator.aclose()
            with contextlib.suppress(BaseException):
                await app.state.card_gen_coordinator.aclose()
            await kernels.shutdown_all()
            await registry.close_all()

    # root_path lives on the app (not just uvicorn.run) so OpenAPI servers and
    # any generated URLs respect a reverse-proxy sub-path mount.
    # #177: the docs / openapi / redoc live under /api too, so the SPA (mounted
    # at "/") owns the entire root namespace and a hard-refreshed client route
    # can never collide with a backend route.
    app = FastAPI(
        title="RCA 3.0",
        lifespan=lifespan,
        root_path=root_path,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
    )
    # #177: EVERY backend route registers on this prefixed router — specstar's
    # CRUD routes (via apply(router=…)) and all hand-written ones. It's included
    # onto `app` exactly once, just before the SPA mount at the end of create_app.
    api = APIRouter(prefix="/api")

    register_notification_routes(api, spec, get_user_id)
    register_health_routes(api, health_service)

    @api.get("/me")
    async def get_me() -> dict:
        """The signed-in user (resolved from the auth seam via the directory)."""
        return users.get(get_user_id()).to_dict()

    @api.get("/users")
    async def list_users() -> list[dict]:
        """The user directory — small enough to fetch whole and filter on the FE
        (mention / share pickers).

        Deduped by id (#42): a real directory may list a person once per
        section/group, and a repeated id becomes a repeated React key in the FE
        picker — which breaks its filtered rendering (stale rows linger, matches
        append at the bottom, the person shows 2-4×). First occurrence wins."""
        seen: set[str] = set()
        out: list[dict] = []
        for u in users.all_users():
            if u.id not in seen:
                seen.add(u.id)
                out.append(u.to_dict())
        return out

    @api.get("/apps")
    async def list_apps() -> list[dict]:
        """#89 P4a — launcher card summaries, one per registered App."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest

        out: list[dict] = []
        for slug in discover_app_slugs():
            m = load_app_manifest(slug)
            out.append(
                {
                    "slug": m.slug,
                    "title": m.title,
                    "description": m.description,
                    "icon": m.icon,
                    "color": m.color,
                }
            )
        return out

    @api.get("/apps/{slug}")
    async def get_app_manifest(slug: str) -> dict:
        """#89 P4a — the full App manifest the dashboard + workspace drive off.
        A shipped ``icon.svg`` is inlined so the FE gets it in one fetch."""
        import contextlib
        from importlib import resources

        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.profiles import list_profiles, load_profile
        from ..apps.registry import app_model, resource_route
        from ..apps.schema import project_fields

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        m = load_app_manifest(slug)
        data = msgspec.to_builtins(m)
        data["resource_route"] = resource_route(slug)
        # The FE renders + inline-edits domain fields off this schema (kind +
        # enum options), projected from the model — never restated on the FE.
        data["fields"] = msgspec.to_builtins(project_fields(app_model(slug)))
        # The create flow's profile picker (#89 T1b): name + display strings per
        # profile, so the FE offers a choice when the App ships more than one.
        app_profiles = []
        for n in list_profiles(slug):
            p = load_profile(slug, n)
            app_profiles.append(
                {
                    "name": n,
                    "title": p.title or n,
                    "description": p.description,
                    # #198: the folder a chat attach stages files into; the FE
                    # resolves the active item's profile → this.
                    "upload_dir": p.upload_dir,
                }
            )
        data["profiles"] = app_profiles
        if m.icon.endswith(".svg"):
            with contextlib.suppress(FileNotFoundError, IsADirectoryError, OSError):
                data["icon"] = (resources.files("workspace_app.apps") / slug / m.icon).read_text(
                    "utf-8"
                )
        return data

    @api.post("/a/{slug}/items")
    async def create_app_item(slug: str, body: dict) -> dict:
        """#89 P4b — create an App's WorkItem + seed its profile's files. The
        body carries the item's fields; `owner` comes from auth and `profile`
        defaults to the App's `default_profile`."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.registry import app_model
        from ..apps.seeding import case_from_item, seed_item

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        manifest = load_app_manifest(slug)
        model = app_model(slug)
        payload = {**body, "owner": get_user_id()}
        payload.setdefault("profile", manifest.default_profile)
        try:
            item = msgspec.convert(payload, type=model)
        except msgspec.ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        rev = spec.get_resource_manager(model).create(item)
        seeded = await seed_item(
            filestore, rev.resource_id, slug, item.profile, case_from_item(item)
        )
        activity.record(
            "item_created",
            f"Created “{item.title}”",
            {"item_id": rev.resource_id},
        )
        return {
            "resource_id": rev.resource_id,
            "app": slug,
            "profile": item.profile,
            "seeded": seeded,
        }

    @api.post(
        "/a/{slug}/items/{item_id}/close",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def close_app_item(slug: str, item_id: str, body: _CloseItemBody) -> Response:
        """#89 P8 — generic, lifecycle-driven close for any App's WorkItem.
        A non-null `status` must be one of the manifest's
        `lifecycle.closing_states` and is set onto `lifecycle.status_field`;
        null leaves the item's status untouched. Either way the workspace
        session is torn down."""
        from ..apps.base import WorkItemBase
        from ..apps.catalog import discover_app_slugs
        from ..apps.manifest import load_app_manifest
        from ..apps.registry import app_model

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        manifest = load_app_manifest(slug)
        model = app_model(slug)
        rm = spec.get_resource_manager(model)
        try:
            current = rm.get(item_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(current, WorkItemBase)
        title = current.title
        if body.status is not None:
            lifecycle = manifest.lifecycle
            if lifecycle is None:  # pragma: no cover - every closable App declares lifecycle
                raise HTTPException(status_code=422, detail=f"app {slug!r} has no close lifecycle")
            if body.status not in lifecycle.closing_states:
                raise HTTPException(
                    status_code=422,
                    detail=f"{body.status!r} is not a closing state for app {slug!r}",
                )
            data = msgspec.to_builtins(current)
            data[lifecycle.status_field] = body.status
            rm.update(item_id, msgspec.convert(data, type=model))
            activity.record(
                "item_closed",
                f"Closed “{title}” as {body.status}",
                {"item_id": item_id},
            )
            # chat → knowledge: schedule insight extraction in the background so
            # the close response doesn't wait on the LLM. Only when a chat
            # pipeline is wired (LLM available).
            if kb_chat_pipeline is not None:
                _, conv_for_promote = _conversation_for(item_id)
                asyncio.create_task(
                    _promote_chat_to_kb(
                        ingestor=ingestor,
                        insights_collection_id=insights_collection_id,
                        actor=get_user_id(),
                        investigation_id=item_id,
                        investigation_title=title,
                        messages=conv_for_promote.messages,
                    )
                )
            # Notify the owner + watchers (members are Tier-2 / opt-in), except
            # whoever did it.
            actor = get_user_id()
            members = current.members
            if isinstance(members, msgspec.UnsetType):  # pragma: no cover - RCA enables members
                members = []
            for uid in {current.owner, *members} - {actor}:
                notify(
                    spec,
                    recipient=uid,
                    kind="status",
                    title=f"{title} → {body.status}",
                    link=f"/a/{slug}/{item_id}",
                    actor=actor,
                )
        else:
            # Pure close — leave status untouched, just release the workspace.
            activity.record(
                "session_closed",
                f"Closed the workspace for “{title}”",
                {"item_id": item_id},
            )
        await registry.close_session(item_id)
        turn_engine.forget(item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.get("/activity")
    async def get_activity() -> list[dict]:
        """Recent activity feed (newest first) for the notifications popover."""
        return activity.entries()

    @api.get("/monitor")
    async def get_monitor(limit: int | None = None, group_id: str | None = None) -> list[dict]:
        """Recent LLM/agent telemetry events (from the SDK trace stream),
        optionally scoped to one investigation via `group_id`."""
        return monitor.recent(limit=limit, group_id=group_id)

    @api.get("/monitor/stream")
    async def stream_monitor(group_id: str | None = None) -> StreamingResponse:
        """Live SSE feed of telemetry events as the SDK emits them."""
        return StreamingResponse(monitor.sse(group_id=group_id), media_type="text/event-stream")

    # #106: context-card create/update custom actions must register on the spec
    # BEFORE apply() so they materialise into routes (norm_keys derived in-write).
    register_context_card_actions(spec)

    # #208: the first real backend hit — specstar materialises every model's
    # schema here (create_all), so a down/unreachable Postgres hangs the whole
    # boot at this line with no message. Narrate it (and let pg_connect_timeout
    # turn the hang into a fast, clear error). Prime suspect for the silent stall.
    # #177: generate specstar's CRUD routes onto the /api router (not the app),
    # but DON'T include it yet — more hand-written routes are added to `api`
    # below; we include it once, after all routes exist, before spec.openapi.
    with boot_step("apply spec to backend (DB schema)"):
        spec.apply(app, router=api, auto_include=False)

    # KB chatbot subsystem: ingestion + collection/document/render routes.
    # Embedder/Chunker are swappable; defaults are offline-friendly (production
    # injects a LiteLLM embedder for real semantic search).
    embedder = kb_embedder or HashEmbedder(dim=EMBED_DIM)
    # Pipeline mode (P1) takes precedence; legacy chunker stays for tests +
    # offline runs that don't construct an LI pipeline.
    if kb_pipeline is not None:
        ingestor = Ingestor(
            spec,
            pipeline=kb_pipeline,  # ty: ignore[invalid-argument-type]
            chat_pipeline=kb_chat_pipeline,  # ty: ignore[invalid-argument-type]
            embedder=embedder,
            code_embedder=kb_code_embedder,
            parser_registry=kb_parser_registry,  # ty: ignore[invalid-argument-type]
        )
    else:
        ingestor = Ingestor(
            spec,
            chunker=kb_chunker or FixedTokenChunker(),
            chat_pipeline=kb_chat_pipeline,  # ty: ignore[invalid-argument-type]
            embedder=embedder,
            code_embedder=kb_code_embedder,
            parser_registry=kb_parser_registry,  # ty: ignore[invalid-argument-type]
        )
    # P2: ensure the "Investigations Knowledge" collection exists at boot so
    # the chat-promote path always has a target. Idempotent (re-uses a
    # collection with the same name).
    insights_collection_id = _ensure_insights_collection(spec, insights_collection_name)
    # Issue #50 P3: after a doc indexes, fold it into its collection's LLM wiki
    # (when use_wiki is on). The coordinator serialises maintainer runs per
    # collection so bursty uploads coalesce instead of racing the wiki pages.
    from ..kb.wiki.coordinator import WikiMaintenanceCoordinator
    from ..kb.wiki.maintainer import default_wiki_maintainer_config
    from ..kb.wiki.orchestrator import default_wiki_merge_config
    from ..kb.wiki.reader import default_wiki_reader_config

    def _wiki_cfg(purpose: str, fallback: Callable[[], AgentConfig]) -> AgentConfig:
        """The wiki agent's config (catalog purpose, else bundled default) with
        the operator's optional model/endpoint override applied — so a stronger
        tool-calling model can drive the wiki agents without re-stating their
        prompts/tools."""
        cfg = catalog.default_for(purpose) or fallback()
        if wiki_model or wiki_llm_base_url or wiki_llm_api_key:
            cfg = msgspec.structs.replace(
                cfg,
                model=wiki_model or cfg.model,
                llm_base_url=wiki_llm_base_url or cfg.llm_base_url,
                llm_api_key=wiki_llm_api_key or cfg.llm_api_key,
            )
        return cfg

    wiki_coordinator = WikiMaintenanceCoordinator(
        spec,
        runner,
        agent_config=_wiki_cfg("wiki_maintainer", default_wiki_maintainer_config),
        maintainer_max_turns=wiki_maintainer_max_turns,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
    )
    app.state.wiki_coordinator = wiki_coordinator
    # #82: indexing runs off the request path on a durable, cross-pod job queue
    # (mirrors the wiki coordinator). It chains the index→wiki hook, so the wiki
    # coordinator is handed in here rather than called from the routes.
    from ..kb.index_coordinator import IndexCoordinator

    index_coordinator = IndexCoordinator(
        spec,
        ingestor,
        wiki_coordinator=wiki_coordinator,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
    )
    # #87: a content edit (the FE's blob-upload + CAS PATCH /source-doc/{id})
    # auto-enqueues a reindex via a SourceDoc patch event_handler — wired here,
    # after the coordinator exists (the handler needs it).
    index_coordinator.install_reindex_on_edit()
    app.state.index_coordinator = index_coordinator
    # #175: 自動 context card — a background job drafts cards from a collection's
    # selected documents for human review (mirrors the wiki/index coordinators).
    from ..kb.card_drafter import LlmCardDrafter, NullCardDrafter
    from ..kb.card_gen_coordinator import CardGenCoordinator

    card_drafter = (
        LlmCardDrafter(card_drafter_llm) if card_drafter_llm is not None else NullCardDrafter()
    )
    card_gen_coordinator = CardGenCoordinator(
        spec,
        card_drafter,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
    )
    app.state.card_gen_coordinator = card_gen_coordinator
    register_card_gen_routes(api, card_gen_coordinator)
    # Model-sanity battery: a background consumer runs matrix cells (heavy live
    # LLM) off the request path, like the index/wiki coordinators. Only mounted
    # when an LLM factory is wired (it's a live probe).
    sanity_coordinator = None
    if sanity_llm_factory is not None:
        from ..health.sanity.coordinator import SanityBatteryCoordinator

        sanity_coordinator = SanityBatteryCoordinator(
            spec,
            sanity_llm_factory,  # ty: ignore[invalid-argument-type]
            judge=sanity_judge_llm,
            message_queue_factory=message_queue_factory,
        )
        register_sanity_routes(api, sanity_models or [], sanity_coordinator)
    app.state.sanity_coordinator = sanity_coordinator
    register_kb_routes(
        api,
        spec,
        ingestor,
        wiki_coordinator,
        index_coordinator=index_coordinator,
        get_user_id=get_user_id,
    )
    # #106: the exposed deterministic context-card lookup (read route, post-apply).
    register_context_card_routes(api, spec)
    # The chat agent shares the injected runner; its retriever uses the same
    # embedder as ingestion so query and document vectors are comparable.
    # When a KB llm is wired, the retriever gains multi-query + HyDE + rerank.
    kb_retriever = Retriever(
        spec,
        embedder=embedder,
        llm=kb_llm,
        code_embedder=kb_code_embedder,
        enhancement_defaults=kb_retrieval_enhancements,
    )
    # One turn engine drives the RCA workspace; one cancellable in-flight turn
    # per conversation, SSE streaming, cancel hook.
    turn_engine = ChatTurnEngine(runner)
    # Exposed for introspection / tests of the #43 broadcast stream (the shared
    # per-investigation pub/sub lives on the engine).
    app.state.turn_engine = turn_engine
    # KB chat runs through a wiki-aware runner that routes each turn across
    # chunk-RAG / wiki / both (#50 P5). It's a pure pass-through to `runner`
    # unless the query opts into the wiki AND a collection has use_wiki, so the
    # default chunk-RAG behaviour is unchanged. Its own engine keeps the RCA
    # turn lifecycle untouched.
    from ..kb.wiki.orchestrator import WikiAwareRunner

    kb_runner = WikiAwareRunner(
        runner,
        spec,
        reader_config=_wiki_cfg("wiki_reader", default_wiki_reader_config),
        merge_config=_wiki_cfg("wiki_merge", default_wiki_merge_config),
        reader_max_turns=wiki_reader_max_turns,
    )
    kb_turn_engine = ChatTurnEngine(kb_runner)
    register_kb_chat_routes(
        api,
        spec,
        kb_turn_engine,
        kb_retriever,
        get_user_id,
        users,
        kb_agent_configs=kb_agent_configs,
        history_max_messages=history_max_messages,
        history_max_context_tokens=history_max_context_tokens,
        max_searches_per_turn=kb_max_searches_per_turn,
    )

    # Cached fallback configs per sub-agent purpose, used when the
    # catalog the caller supplied didn't wire that purpose (legacy
    # positional-list tests). Bundled always populates kb_chat /
    # infer_modules so these defaults are always available.
    _purpose_fallbacks: dict[str, AgentConfig] = {
        "kb_chat": default_kb_agent_config,
        "infer_modules": default_infer_modules_config,
    }

    def _resolve_infer_modules_collections(name: str) -> list[str] | None:
        """#66: the KB collection ids infer_modules' per-step classifier
        searches. "" ⇒ None (search ALL collections, backward-compatible). A
        configured NAME resolves to its collection's ids; a name that matches
        no collection is a loud misconfig — raise rather than silently fall
        back to taxonomy-only (a typo would otherwise disable KB lookups for
        every step). Resolved once per turn, not per step."""
        if not name:
            return None
        from specstar import QB

        coll_rm = spec.get_resource_manager(Collection)
        ids = [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in coll_rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
            if isinstance(r.data, Collection) and r.data.name == name
        ]
        if not ids:
            raise ValueError(
                f"infer_modules is configured to search collection {name!r} "
                f"(agents.infer_modules[].collection) but no collection with that "
                f"name exists — create it, fix the name, or remove the setting to "
                f"search all collections."
            )
        return ids

    async def _run_subagent(
        purpose: str,
        payload: str,
        emit: OutputSink | None = None,
        origin_id: str | None = None,
        enhancements: Enhancements | None = None,
        reasoning_effort: str | None = None,
        wiki_query: bool = False,
        collection_ids: list[str] | None = None,
    ) -> tuple[str, list[Citation]]:
        """Generic sub-agent bridge — runs the sub-agent for `purpose`
        over every collection and returns its synthesized answer + the
        resolved citations. ONE bridge replaces the per-purpose
        `_ask_kb` / `_infer_modules` closures: tool impls own the
        arg→payload formatting (e.g. `infer_modules_impl` JSON-encodes
        its typed args); this bridge only knows how to ask the named
        sub-agent and bubble its work up.

        `emit` (when set) is the RCA run's output sink — the sub-agent's
        searches/reasoning relay to it as tool-log lines. `origin_id`
        is the calling investigation so its KB citations are logged
        against it. Returns the answer + citations; the tool impl
        stashes the citations into `ctx.subagent_citations[purpose]`."""
        from specstar import QB

        cfg = catalog.default_for(purpose) or _purpose_fallbacks.get(purpose)
        if cfg is None:
            raise ValueError(
                f"no AgentConfig registered for sub-agent purpose {purpose!r} "
                f"(catalog has: {sorted(catalog.purposes())}; bundled fallbacks: "
                f"{sorted(_purpose_fallbacks)})"
            )

        # #66: infer_modules passes a pre-resolved collection scope (a single
        # configured collection, resolved ONCE per turn) so its ~1500 per-step
        # calls don't each re-list every collection. None ⇒ search them all
        # (ask_knowledge_base / unconfigured infer_modules).
        if collection_ids is not None:
            ids = collection_ids
        else:
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

        captured: list[Citation] = []

        def log_cites(cites: list[Citation]) -> None:
            record_citations(
                spec, cites, origin_kind="rca", origin_id=origin_id or "", cited_by=get_user_id()
            )
            captured.extend(cites)

        # When the query opted into the wiki, drive the lookup with the
        # wiki-aware runner (chunk / wiki / both routing); otherwise the plain
        # base runner (chunk-RAG only).
        answer = await answer_question(
            kb_runner if wiki_query else runner,
            kb_retriever,
            ids,
            payload,
            agent_config=cfg,
            spec=spec,
            enhancements=enhancements,
            reasoning_effort=reasoning_effort,
            wiki=wiki_query,
            on_event=relay,
            on_citations=log_cites,
            # #195: the RCA → KB bridge is the same KB agent — cap its searches
            # too (None ⇒ unlimited when the operator lifts the cap).
            max_searches=kb_max_searches_per_turn,
        )
        return answer, captured

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
                link=f"/a/{_item_slug(investigation_id)}/items/{investigation_id}",
                actor=actor,
            )

    def _load_item_title(item_id: str) -> str | None:
        """Title of any App's WorkItem, resolved generically by id (the mention
        + export paths need it for their copy). ``None`` when the id maps to no
        registered App's item."""
        from ..apps.resolve import find_work_item

        found = find_work_item(spec, item_id)
        return found[1].title if found is not None else None

    def _item_profile(item_id: str) -> str:
        """The App profile an item was created from — drives the §A skill index
        (the runner exposes `read_skill` when the profile ships skills).
        "default" when the id maps to no registered App's item."""
        from ..apps.resolve import find_work_item

        found = find_work_item(spec, item_id)
        return found[1].profile if found is not None else "default"

    def _item_slug(item_id: str) -> str | None:
        """The App slug owning an item — pairs with `_item_profile` so the
        runner can read the profile's `.skill/` dir. None for an unknown id."""
        from ..apps.resolve import find_work_item

        found = find_work_item(spec, item_id)
        return found[0] if found is not None else None

    def _require_item(slug: str, item_id: str) -> str:
        """#95: the workspace routes nest under ``/a/{slug}/items/{item_id}``.
        Validate that ``item_id`` really belongs to App ``slug`` (404 otherwise)
        so a wrong slug can't operate on another App's item, and return the id
        for the handler to use."""
        from ..apps.resolve import find_work_item

        found = find_work_item(spec, item_id)
        if found is None or found[0] != slug:
            raise HTTPException(
                status_code=404, detail=f"item {item_id!r} not found in app {slug!r}"
            )
        return item_id

    def _agent_mention(investigation_id: str, user_ids: list[str], note: str) -> None:
        """The agent's `mention_user` tool reaches this — same summon, authored
        by the agent."""
        title = _load_item_title(investigation_id)
        if title is None:  # pragma: no cover - the agent only mentions on a live item
            return
        _record_mention(investigation_id, title, user_ids, note, actor=None, author="RCA Agent")

    def _resolve_agent_config(item_id: str) -> AgentConfig | None:
        """#89: a per-App WorkItem (RcaInvestigation, …) resolves its turn's
        config via the 3-layer AppCatalog (app ◇ profile ◇ preset)."""
        from ..apps.resolve import resolve_item_agent_config

        return resolve_item_agent_config(spec, app_catalog, item_id)

    def _conversation_for(investigation_id: str) -> tuple[str, Conversation]:
        # Item-level (no chat_id) endpoints operate on the item's DEFAULT chat — the
        # earliest-born free chat, created on first use (manual §3). With multi-chat
        # an item holds many conversations; this resolves the implicit default and
        # never returns a workflow chat. Pre-multi-chat items have one (unstamped)
        # conversation, which stays the default — byte-for-byte preserved.
        from .chats import resolve_default_conversation

        return resolve_default_conversation(conv_rm, investigation_id)

    def _engine_key(investigation_id: str, chat_id: str) -> str:
        """The turn-engine / SSE key for a chat (manual §3). The DEFAULT chat keeps
        the legacy ``item_id`` key so item-level endpoints, the workflow drive path,
        and file-change broadcasts all share its stream; every other chat keys on its
        own id. Read-only — never materialises the default."""
        from .chats import find_default_conversation

        default = find_default_conversation(conv_rm, investigation_id)
        if default is not None and default[0] == chat_id:
            return investigation_id
        return chat_id

    def _require_chat(slug: str, item_id: str, chat_id: str) -> tuple[str, Conversation]:
        """Validate slug→item AND that ``chat_id`` is a chat OF that item; return
        ``(chat_id, Conversation)`` or 404. Guards the chat-scoped endpoints (manual §3)."""
        investigation_id = _require_item(slug, item_id)
        try:
            conv = conv_rm.get(chat_id).data
        except ResourceIDNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown chat: {chat_id!r}") from None
        if not isinstance(conv, Conversation) or conv.item_id != investigation_id:
            raise HTTPException(status_code=404, detail=f"unknown chat: {chat_id!r}")
        return chat_id, conv

    async def _hub_collection_ids(item_id: str) -> list[str]:
        """The item's collection set (topic-hub §5): the ids in its ``collections.json``
        workspace file, or ``[]`` when it has none. Scopes ``lookup_glossary`` /
        ``ask_knowledge_base`` for the turn. Tolerant of a hand-edited / malformed file."""
        from ..kb.collections import collection_ids_from_json

        try:
            raw = await filestore.read(item_id, "/collections.json")
        except FileNotFound:
            return []
        try:
            return collection_ids_from_json(json.loads(raw))
        except (ValueError, TypeError):
            return []

    def _app_context_files(item_id: str) -> list[str]:
        """The App's declared per-turn context files (manual §6) — the workspace files
        whose live content is injected each turn. Empty for most Apps."""
        from ..apps.manifest import load_app_manifest

        slug = _item_slug(item_id)
        if slug is None:  # pragma: no cover - callers pass a validated item id
            return []
        return load_app_manifest(slug).agent.context_files

    # ── replay diagnostics (#51 P4) ──────────────────────────────────
    # Read-only loaders: replay must never create/mutate anything, so
    # these do their own lookups instead of reusing `_conversation_for`
    # (which creates a conversation for a fresh investigation).

    def _load_turn(
        source: str, thread_id: str
    ) -> tuple[list[Any], AgentConfig, list[PackageInfo] | None, str | None] | None:
        from specstar import QB

        if source == "rca":
            for r in conv_rm.list_resources((QB["item_id"] == thread_id).build()):
                data = r.data
                assert isinstance(data, Conversation)
                # #94: no fallback. If the item can't resolve a config (gone /
                # unregistered App), there's nothing to replay — report "no turn".
                config = _resolve_agent_config(thread_id)
                if config is None:
                    return None
                return (
                    list(data.messages),
                    config,
                    packages,
                    _item_profile(thread_id),
                )
            return None
        # kb — the per-message model picker isn't persisted on the
        # message, so replay probes the deploy's default KB agent.
        kb_rm = spec.get_resource_manager(KbChat)
        try:
            chat = kb_rm.get(thread_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(chat, KbChat)
        return list(chat.messages), default_kb_agent_config, None, None

    def _load_doc(document_id: str) -> tuple[str, str, bytes] | None:
        doc_rm = spec.get_resource_manager(SourceDoc)
        try:
            rev = doc_rm.get(document_id)
        except ResourceIDNotFoundError:
            return None
        doc = rev.data
        assert isinstance(doc, SourceDoc)
        raw = doc_rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        ct = doc.content.content_type
        mime = ct if isinstance(ct, str) else "application/octet-stream"
        return doc.path, mime, raw

    register_replay_routes(api, service=replay_service, load_turn=_load_turn, load_doc=_load_doc)

    # ── Workflows (#100) ─────────────────────────────────────────────
    # A run drives its own WORKFLOW CHAT (§3): agent nodes stream into that chat and
    # the orchestrator overlays phase/step events on the same per-chat stream.
    from ..apps.profiles import load_profile, load_profile_workflow

    def _wf_upload_dir(slug: str, profile: str) -> str:
        """#198: the active profile's staging folder — the orchestrator threads it onto
        the handle (``wf.upload_dir``) and derives the run's ``input.json`` from it, so a
        workflow globs the same folder the chat attach lands in."""
        return load_profile(slug, profile).upload_dir

    async def _wf_drive_turn(
        item_id: str, chat_key: str, captured_user: str, prompt: str, tools: list[str] | None
    ) -> str:
        """Run one agent node as a turn on the run's WORKFLOW CHAT (§3, §5.1):
        ``chat_key`` is that chat's conversation id, so turns enqueue + persist there
        (keeping the run's stream separate from the item's other chats). Builds the
        ctx, narrows the tool ceiling to the step's subset, enqueues + awaits, persists
        the produced messages under the captured user, and returns the assistant text."""
        try:
            rid = chat_key
            got = conv_rm.get(rid).data
            assert isinstance(got, Conversation)
            conv = got
        except (ResourceIDNotFoundError, AssertionError):
            rid, conv = _conversation_for(item_id)  # legacy fallback (item's default chat)
        cfg = _resolve_agent_config(item_id)
        if cfg is not None and tools is not None:
            # tools= ⊆ the profile's tool ceiling (manual §5.1) — drop anything the
            # profile doesn't already allow, so a step can't widen the boundary.
            ceiling = cfg.allowed_tools or []
            cfg = msgspec.structs.replace(cfg, allowed_tools=[t for t in tools if t in ceiling])
        session = await registry.session(item_id)
        ctx = AgentToolContext(
            investigation_id=item_id,
            sandbox=sandbox,
            filestore=filestore,
            files=files,
            sync=sync,
            sandbox_spec=SandboxSpec(),
            handle=session.handle,
            ensure_sandbox_via=lambda: registry.ensure_handle(session),
            agent_config=cfg,
            run_subagent=_run_subagent,
            mention=_agent_mention,
            describer=vlm_describer,
            deck_vlm=deck_vlm,
            read_file_max_lines=read_file_max_lines,
            read_file_max_chars=read_file_max_chars,
            exec_output_max_chars=exec_output_max_chars,
            history=history_items(
                conv.messages,
                max_messages=history_max_messages,
                max_tokens=history_max_context_tokens,
                users=users,
            ),
            packages=packages or [],
            prebuilt_dir=prebuilt_dir,
            app_slug=_item_slug(item_id),
            template_profile=_item_profile(item_id),
        )
        answer: list[str] = []

        def persist(produced: list[TurnMessage]) -> None:
            if produced:
                conv2 = conv_rm.get(rid).data  # re-fetch the workflow chat
                assert isinstance(conv2, Conversation)
                # Background step → attribute the persisted turn to the captured
                # user (§15, the job-pod acting-user pattern).
                with conv_rm.using(user=captured_user):
                    for tm in produced:
                        conv2.messages.append(_to_rca_message(tm))
                    conv_rm.update(rid, conv2)
            answer.extend(tm.content for tm in produced if tm.role == "assistant")

        await turn_engine.enqueue(chat_key, prompt, ctx, on_complete=persist)
        return "\n".join(answer)

    async def _wf_run_sandbox(
        item_id: str,
        run: str,
        credential: str,
        on_output: OutputSink | None = None,
    ) -> tuple[int, str]:
        """Run a deterministic node's command in the item's sandbox (§5.2), with the
        run-scoped credential injected into its env so a node script can auth
        capability HTTP calls (manual §15). ``on_output`` streams stdout chunks live
        (#178) so a long command shows movement instead of looking dead."""
        session = await registry.session(item_id)
        handle = await registry.ensure_handle(session)
        import shlex

        env = f"export WF_TOKEN={shlex.quote(credential)}; " if credential else ""
        result = await sandbox.exec(handle, ["sh", "-lc", env + run], on_output=on_output)
        with contextlib.suppress(Exception):
            await registry.flush(item_id)
        return result.exit_code, result.stdout.decode("utf-8", errors="replace")

    async def _wf_ingest(
        item_id: str,
        captured_user: str,
        collection: str,
        path: str,
        journal_dir: str = "/.workflow/_default",
    ) -> str:
        """The ingest capability (§8) bound to this run's workspace + captured user.
        ``journal_dir`` is the run's journal folder (#136) so the receipt lands under
        the run's workflow folder, not scattered at the workspace root."""
        return await ingest_to_collection(
            spec,
            ingestor,
            files,  # WorkspaceFiles is FileStore-shaped (read/write by workspace id)
            workspace_id=item_id,
            collection=collection,
            path=path,
            user=captured_user,
            # #234: store-then-enqueue — the upload auto-indexes off the request path via
            # the IndexCoordinator, exactly like the KB upload endpoint.
            enqueue=index_coordinator.enqueue,
            journal_dir=journal_dir,
        )

    async def _wf_upsert_card(
        captured_user: str, collection: str, keys: list[str], title: str, body: str
    ) -> str:
        """The upsert-context-card capability (§8, #111) bound to this run's captured
        user — create-or-update by key, so re-classifying a term updates its card
        instead of duplicating it."""
        return upsert_context_card(
            spec, collection=collection, keys=keys, title=title, body=body, user=captured_user
        )

    async def _wf_find_card(collection: str, keys: list[str], title: str) -> dict[str, Any] | None:
        """The read-only find-overwrite-target capability (#205) — the existing card a
        commit-time upsert would overwrite, as a plain dict the workflow lib renders into
        the diff "before" snapshot (it stays decoupled from ``ContextCard``)."""
        from ..resources.kb import ContextCard
        from ..workflow.capabilities import find_overwrite_target

        card, ambiguity = find_overwrite_target(spec, collection=collection, keys=keys, title=title)
        if not isinstance(card, ContextCard):
            return None
        return {
            "keys": list(card.keys),
            "title": card.title,
            "body": card.body,
            "ambiguity": ambiguity,
        }

    async def _wf_collection_has(collection: str, path: str) -> bool:
        """Backs ``check.collection_has`` (§8): did ``path`` land in ``collection``
        (a name or id)? #234: ingest is async, so ``landed`` means the SourceDoc EXISTS
        — the upload succeeded — not that the background index has flipped it to ``ready``."""
        from ..workflow.capabilities import collection_has_doc

        return collection_has_doc(spec, collection=collection, path=path)

    def _wf_wire_handle(
        wf: WorkflowHandle, run_id: str, item_id: str, captured_user: str, chat_key: str
    ) -> None:
        # Agent turns drive the run's workflow CHAT (chat_key); sandbox / ingest stay
        # on item_id (the workspace shared across the item's chats, §3.1).
        wf.drive_turn = lambda prompt, tools: _wf_drive_turn(
            item_id, chat_key, captured_user, prompt, tools
        )
        wf.run_sandbox = lambda run, on_output=None: _wf_run_sandbox(
            item_id, run, wf.credential, on_output
        )
        wf._ingest = lambda collection, path: _wf_ingest(
            item_id, captured_user, collection, path, wf.journal_dir
        )
        wf._collection_has = _wf_collection_has
        wf._upsert_card = lambda collection, keys, title, body: _wf_upsert_card(
            captured_user, collection, keys, title, body
        )
        wf._find_card = _wf_find_card

    def _wf_any_running(item_id: str) -> bool:
        """Is any run on this item still RUNNING? Used to decide whether the shared
        sandbox is still needed (§3.1) — the run being released is no longer RUNNING
        (it is terminal / awaiting_human), so this counts only the OTHERS."""
        from specstar import QB

        rm = spec.get_resource_manager(WorkflowRun)
        for r in rm.list_resources((QB["item_id"] == item_id).build()):
            if isinstance(r.data, WorkflowRun) and r.data.status is RunStatus.RUNNING:
                return True
        return False

    async def _wf_release(item_id: str, terminal: bool, chat_key: str) -> None:
        """Free resources when a run ends/pauses (§16). The sandbox + workspace are
        shared across the item's chats (§3.1), so tear the sandbox down only when no
        OTHER run is still running — a parallel run keeps it alive (§3). On terminal,
        drop the finished run's own chat turn session (never the item's other chats)."""
        if not _wf_any_running(item_id):
            await registry.close_session(item_id)
        if terminal:
            turn_engine.forget(chat_key)

    def _wf_notify_failure(run: WorkflowRun) -> None:
        """In-app failure notification to the item's owner (manual §17)."""
        from ..apps.resolve import find_work_item

        found = find_work_item(spec, run.item_id)
        if found is None:  # pragma: no cover - a run always has a live item
            return
        slug, item = found
        phase = run.current_phase or "?"
        notify(
            spec,
            recipient=item.owner,
            kind="status",
            title=f"Workflow run failed at “{phase}”",
            link=f"/a/{slug}/items/{run.item_id}",
            actor=run.captured_user,
        )

    workflow_credentials = CredentialBroker()
    workflow_orchestrator = WorkflowOrchestrator(
        spec=spec,
        store=files,  # WorkspaceFiles is FileStore-shaped (read/write by workspace id)
        load_run=load_run_callable,
        load_manifest=load_profile_workflow,
        load_upload_dir=_wf_upload_dir,
        wire_handle=_wf_wire_handle,
        publish=turn_engine.publish,
        release=_wf_release,
        notify_failure=_wf_notify_failure,
        credentials=workflow_credentials,
        max_steps=workflow_max_steps,
        run_timeout_s=(
            workflow_run_timeout.total_seconds() if workflow_run_timeout is not None else None
        ),
        step_timeout_s=(
            workflow_step_timeout.total_seconds() if workflow_step_timeout is not None else None
        ),
        concurrency=workflow_concurrency,
        keep_last_runs=workflow_keep_last_runs,
    )
    app.state.workflow_orchestrator = workflow_orchestrator

    def _workflow_manifest_or_404(slug: str, item_id: str, workflow_id: str = ""):
        """Validate the item belongs to the slug AND its profile carries the requested
        workflow (manual §4); return (investigation_id, profile, manifest)."""
        investigation_id = _require_item(slug, item_id)
        profile = _item_profile(investigation_id)
        manifest = load_profile_workflow(slug, profile, workflow_id)
        if manifest is None:
            raise HTTPException(
                status_code=422,
                detail=f"profile {profile!r} of app {slug!r} has no workflow {workflow_id!r}",
            )
        return investigation_id, profile, manifest

    @api.get("/a/{slug}/profiles")
    async def list_app_profiles(slug: str) -> list[dict]:
        """#100 (manual §4 & §14): the App's profiles, each with its list of workflow
        MANIFESTS so the FE's new-chat picker can offer every workflow type. Also keeps
        the legacy singular ``workflow`` field for back-compat."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.profiles import list_profiles, load_profile, normalize_workflows

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        out: list[dict] = []
        for name in list_profiles(slug):
            p = load_profile(slug, name)
            workflows = normalize_workflows(p)
            out.append(
                {
                    "name": name,
                    "title": p.title or name,
                    "description": p.description,
                    "has_workflow": bool(workflows),
                    "workflow": msgspec.to_builtins(p.workflow) if p.workflow else None,
                    "workflows": [msgspec.to_builtins(wf) for wf in workflows],
                }
            )
        return out

    @api.post("/a/{slug}/items/{item_id}/run", status_code=status.HTTP_202_ACCEPTED)
    async def run_workflow_item(
        slug: str, item_id: str, request: Request, workflow_id: str = Query("")
    ) -> dict:
        """#100 / topic-hub P8 (manual §3, §4, §14): launch a workflow. Opens a fresh
        WORKFLOW CHAT (a `Conversation` with `run_id`) the run streams into, and returns
        its `chat_id`. ``workflow_id`` selects which of the profile's workflows (§4);
        runs are per-chat, so several may run in parallel on one item (§3). Inputs come
        from the workspace (``MANIFEST.input_json``).

        #197: an external system triggers headlessly by uploading the workflow's input
        FILES in the SAME call — we communicate with workflows through the workspace, not
        a JSON body. A ``multipart/form-data`` body carries ``file`` parts (each part's
        filename IS its workspace path, sub-dirs allowed) and may also carry
        ``workflow_id`` as a form field; the files are written (overwrite) BEFORE the run
        starts. A path that escapes the workspace root aborts the whole call (400) so
        nothing is half-written and no run begins. With no body the call is the plain
        trigger the FE makes — the upload path is skipped entirely."""
        workflow_id, staged = await _staged_run_uploads(request, workflow_id)
        investigation_id, profile, manifest = _workflow_manifest_or_404(slug, item_id, workflow_id)
        for norm, data in staged:
            await files.write(investigation_id, norm, data)
            activity.record(
                "file_written",
                f"Wrote {norm}",
                {"investigation_id": investigation_id, "path": norm},
            )
            turn_engine.publish(
                investigation_id, FileChanged(path=norm, by=get_user_id(), kind="written")
            )
        # The chat overlay (manual §3): create it, start the run on it, then link the
        # run_id back. This runs synchronously before the run task drives any turn, so
        # the chat carries its run_id before it streams.
        title = manifest.title or workflow_id or "Workflow"
        chat_id = conv_rm.create(
            Conversation(item_id=investigation_id, title=title, created_ms=_now_ms())
        ).resource_id
        run_id = await workflow_orchestrator.start(
            slug=slug,
            item_id=investigation_id,
            profile=profile,
            captured_user=get_user_id(),
            workflow_id=workflow_id,
            chat_id=chat_id,
        )
        chat = conv_rm.get(chat_id).data
        assert isinstance(chat, Conversation)
        chat.run_id = run_id
        conv_rm.update(chat_id, chat)
        activity.record(
            "workflow_started",
            "Started a workflow run",
            {"item_id": investigation_id, "run_id": run_id, "chat_id": chat_id},
        )
        return {"run_id": run_id, "item_id": investigation_id, "chat_id": chat_id}

    @api.get("/a/{slug}/items/{item_id}/runs")
    async def list_workflow_runs(slug: str, item_id: str) -> list[dict]:
        """#100: the item's run history (newest first), for the run-list view."""
        investigation_id = _require_item(slug, item_id)
        from specstar import QB

        rm = spec.get_resource_manager(WorkflowRun)
        out: list[dict] = []
        for r in rm.list_resources((QB["item_id"] == investigation_id).build()):
            assert isinstance(r.data, WorkflowRun)
            out.append(
                {
                    "run_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    **msgspec.to_builtins(r.data),
                }
            )
        out.sort(key=lambda d: d.get("started") or 0, reverse=True)
        return out

    @api.get("/a/{slug}/items/{item_id}/runs/preview")
    async def preview_workflow_run(
        slug: str, item_id: str, workflow_id: str = Query("")
    ) -> _PreflightPreviewOut:
        """#283 (manual §18): the launch dialog's pre-flight — what THIS workflow will do
        + whether its preconditions are met, WITHOUT starting a run. Reads the staged
        ``input.json`` (manual §14) and calls the author's optional ``preflight(wf, inputs)``
        over a read-only handle (no turn/sandbox drivers — pre-flight only inspects the
        workspace). A workflow with no ``preflight`` previews its phases alone (runnable).
        Registered before ``/runs/{run_id}`` so ``preview`` isn't read as a run id."""
        investigation_id, profile, manifest = _workflow_manifest_or_404(slug, item_id, workflow_id)
        wf = WorkflowHandle(
            store=files,
            workspace_id=investigation_id,
            workflow_id=workflow_id,
            config=dict(manifest.config),
            upload_dir=_wf_upload_dir(slug, profile),
            user=get_user_id(),
        )
        inputs = await resolve_inputs(wf, manifest)
        preflight = load_preflight_callable(slug, profile, workflow_id)
        summary = ""
        checks: list[_PreflightCheckOut] = []
        allowed = True
        if preflight is not None:
            report = await preflight(wf, inputs)
            summary = report.summary
            checks = [
                _PreflightCheckOut(
                    label=c.label, ok=c.ok, severity=c.severity.value, reason=c.reason
                )
                for c in report.checks
            ]
            allowed = _preflight_can_run(report)
        return _PreflightPreviewOut(
            workflow_id=workflow_id,
            # The dialog falls back to the workflow id when title is empty (FE side), so
            # send the raw title — no server-side or-chain (keeps the branch coverage clean).
            title=manifest.title,
            description=manifest.description,
            phases=[_PhaseOut(id=p.id, title=p.title) for p in manifest.phases],
            summary=summary,
            checks=checks,
            can_run=allowed,
            has_preflight=preflight is not None,
        )

    @api.get("/a/{slug}/items/{item_id}/runs/{run_id}")
    async def get_workflow_run(slug: str, item_id: str, run_id: str) -> dict:
        """#100 (manual §14): poll a run — status + result + per-phase progress."""
        _require_item(slug, item_id)
        rm = spec.get_resource_manager(WorkflowRun)
        try:
            data = rm.get(run_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id!r}") from exc
        assert isinstance(data, WorkflowRun)
        return {"run_id": run_id, **msgspec.to_builtins(data)}

    @api.get("/a/{slug}/items/{item_id}/runs/{run_id}/stream")
    async def stream_workflow_run(slug: str, item_id: str, run_id: str) -> StreamingResponse:
        """#100 / P8 (manual §3, §14): the run's live SSE — its WORKFLOW CHAT's stream
        (agent events + phase/step events overlaid). Falls back to the item's broadcast
        stream when the run / its chat can't be resolved (defensive)."""
        investigation_id = _require_item(slug, item_id)
        key = investigation_id
        try:
            run = spec.get_resource_manager(WorkflowRun).get(run_id).data
            if isinstance(run, WorkflowRun) and run.chat_id:
                key = run.chat_id
        except ResourceIDNotFoundError:
            pass
        return StreamingResponse(turn_engine.subscribe_sse(key), media_type="text/event-stream")

    @api.post(
        "/a/{slug}/items/{item_id}/runs/{run_id}/cancel",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_workflow_run(slug: str, item_id: str, run_id: str) -> Response:
        """#100 (manual §10): Stop a run — it goes terminal (cancelled) and the item
        opens to interactive use. Idempotent (a no-op when nothing is running)."""
        investigation_id = _require_item(slug, item_id)
        await workflow_orchestrator.cancel(run_id, investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.post(
        "/a/{slug}/items/{item_id}/runs/{run_id}/decisions",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def decide_workflow_run(
        slug: str, item_id: str, run_id: str, body: _DecisionBody
    ) -> dict:
        """#100 (manual §10): answer a `human_gate` — records the decision artifact
        and resumes the run (completed steps skip; the gate reads the decision)."""
        investigation_id, profile, _manifest = _workflow_manifest_or_404(slug, item_id)
        try:
            await workflow_orchestrator.decide(
                slug=slug,
                item_id=investigation_id,
                profile=profile,
                run_id=run_id,
                choice=body.choice,
                input=body.input,
                decided_by=get_user_id(),
            )
        except NotAwaitingDecision as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run_id, "resumed": True}

    @api.post("/a/{slug}/items/{item_id}/capabilities/ingest")
    async def capability_ingest(
        slug: str,
        item_id: str,
        body: _IngestBody,
        x_workflow_token: str | None = Header(default=None),
    ) -> dict:
        """#100 (manual §8): the ingest capability as an HTTP endpoint — a
        deterministic node's sandbox script reaches it with the run-scoped
        credential (manual §15). Idempotent (upsert by natural-key doc id).

        Auth: a valid ``X-Workflow-Token`` acts as its captured user and must be
        scoped to THIS item; an expired/forged token is 401. With no token the call
        falls back to the session user (the in-app / FE path)."""
        investigation_id = _require_item(slug, item_id)
        actor = get_user_id()
        if x_workflow_token is not None:
            claims = workflow_credentials.resolve(x_workflow_token)
            if claims is None or claims.item_id != investigation_id:
                raise HTTPException(status_code=401, detail="invalid or expired workflow token")
            actor = claims.user
        try:
            # The receipt for this direct/HTTP capability call lands in the _default
            # journal folder (#136) — the handle-driven node path threads the run's own
            # per-workflow journal_dir; this vestigial receipt has no run handle here.
            doc_id = await _wf_ingest(investigation_id, actor, body.collection, body.path)
        except CollectionNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown collection: {body.collection!r}"
            ) from exc
        return {"doc_id": doc_id}

    @api.post("/a/{slug}/items/{item_id}/capabilities/context-card")
    async def capability_context_card(
        slug: str,
        item_id: str,
        body: _CardBody,
        x_workflow_token: str | None = Header(default=None),
    ) -> dict:
        """topic-hub P9 / #111 (manual §8): the upsert-context-card capability as an HTTP
        endpoint — a deterministic node's sandbox script reaches it with the run-scoped
        credential (manual §15). Idempotent: create-or-update by key, so a re-run updates
        the card instead of duplicating it. Same auth as ingest: a valid
        ``X-Workflow-Token`` acts as its captured user, scoped to THIS item; no token →
        the session user."""
        investigation_id = _require_item(slug, item_id)
        actor = get_user_id()
        if x_workflow_token is not None:
            claims = workflow_credentials.resolve(x_workflow_token)
            if claims is None or claims.item_id != investigation_id:
                raise HTTPException(status_code=401, detail="invalid or expired workflow token")
            actor = claims.user
        try:
            card_id = await _wf_upsert_card(
                actor, body.collection, body.keys, body.title, body.body
            )
        except CollectionNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown collection: {body.collection!r}"
            ) from exc
        return {"card_id": card_id}

    @api.get("/a/{slug}/items/{item_id}/export")
    async def export_investigation(slug: str, item_id: str) -> Response:
        """Download the investigation's full conversation as JSON — every message
        with its reasoning, tool calls (name/args/output), citations, metrics and
        timestamps, plus the case metadata. Read-only (won't create a
        conversation) and curl-friendly, so it doubles as a debug dump."""
        investigation_id = _require_item(slug, item_id)
        from specstar import QB

        from ..apps.resolve import find_work_item

        meta: dict[str, object] = {"id": investigation_id}
        found = find_work_item(spec, investigation_id)
        if found is not None:
            meta = {"id": investigation_id, **msgspec.to_builtins(found[1])}

        messages: list = []
        for r in conv_rm.list_resources((QB["item_id"] == investigation_id).build()):
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

    @api.post(
        "/a/{slug}/items/{item_id}/messages",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def send_message(slug: str, item_id: str, body: _MessageBody) -> Response:
        investigation_id = _require_item(slug, item_id)
        # Item-level (no chat_id) → the implicit default chat, keyed on item_id so the
        # workflow drive path + file-change broadcasts share its stream (manual §3).
        rid, conv = _conversation_for(investigation_id)
        await _send_into(investigation_id, rid, conv, investigation_id, body)
        return Response(status_code=status.HTTP_202_ACCEPTED)

    async def _send_into(
        investigation_id: str,
        rid: str,
        conv: Conversation,
        engine_key: str,
        body: _MessageBody,
    ) -> None:
        """Append the user message to conversation ``rid``, build the RCA turn ctx
        from ITS history, and enqueue the turn on ``engine_key`` (item_id for the
        default chat, the chat_id otherwise — manual §3). Shared by the item-level
        and chat-scoped message endpoints."""
        # #43: stamp the sender so a shared workspace's chat shows who said what,
        # and broadcast the message to live viewers (below, before the turn runs).
        author = get_user_id()
        created = _now_ms()
        conv.messages.append(
            Message(role="user", content=body.content, author=author, created_at=created)
        )
        conv_rm.update(rid, conv)

        session = await registry.session(investigation_id)
        # Topic Hub §5/§7: the Hub's collection set (collections.json) scopes the
        # turn's deterministic glossary + KB tools. [] for Apps without the file.
        hub_collection_ids = await _hub_collection_ids(investigation_id)
        # Composer knowledge-search depth: applies to this turn's KB
        # lookups. The bridge wrapper forwards it to the kb_chat
        # sub-agent only — infer_modules' focused classification probe
        # keeps the operator defaults.
        caller_enh = to_caller_enhancements(body.enhancements)
        # The composer's "Search the wiki" toggle — a routing flag (not a depth
        # knob), so it's read off the body separately and only applies to the
        # kb_chat sub-agent (infer_modules stays chunk-only).
        caller_wiki = bool(body.enhancements and body.enhancements.wiki)
        # #66: resolve infer_modules' configured collection NAME → ids ONCE for
        # this whole turn (not per step). "" ⇒ None ⇒ the bridge searches all
        # collections (backward-compatible). A configured-but-missing name → []
        # ⇒ kb_search finds nothing and the classifier falls back to taxonomy.
        infer_coll_ids = _resolve_infer_modules_collections(infer_modules_collection)

        async def _run_subagent_with_depth(
            purpose: str,
            payload: str,
            emit: OutputSink | None = None,
            origin_id: str | None = None,
        ) -> tuple[str, list[Citation]]:
            # kb_chat uses the COMPOSER's live depth + effort (#65); infer_modules
            # uses its OWN configured depth + effort + a single configured
            # collection (#66, a focused classifier).
            if purpose == "kb_chat":
                enh, reff, wiki, colls = caller_enh, body.reasoning_effort, caller_wiki, None
            elif purpose == "infer_modules":
                enh, reff, wiki = infer_modules_enhancements, infer_modules_reasoning_effort, False
                colls = infer_coll_ids
            else:
                enh, reff, wiki, colls = None, None, False, None
            return await _run_subagent(
                purpose,
                payload,
                emit,
                origin_id,
                enhancements=enh,
                reasoning_effort=reff,
                wiki_query=wiki,
                collection_ids=colls,
            )

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
            # ONE bridge for every sub-agent the RCA tools may invoke
            # (ask_knowledge_base, infer_modules, future ones). The tool
            # impls each pass their own purpose name + formatted payload.
            run_subagent=_run_subagent_with_depth,
            describer=vlm_describer,
            deck_vlm=deck_vlm,
            # The turn's depth override also rides the ctx so any direct
            # kb tool on the RCA agent applies the same cascade.
            kb_enhancements=caller_enh,
            # Lets the agent's mention_user tool summon a human to this case.
            mention=_agent_mention,
            # read_file truncation caps (deploy config).
            read_file_max_lines=read_file_max_lines,
            read_file_max_chars=read_file_max_chars,
            exec_output_max_chars=exec_output_max_chars,
            # Cross-turn memory: prior dialogue (excludes the user msg just added),
            # windowed by message count THEN a token budget (#45) so huge tool
            # outputs can't overflow the model's context.
            history=history_items(
                conv.messages[:-1],
                max_messages=history_max_messages,
                max_tokens=history_max_context_tokens,
                users=users,
            ),
            # Provisionable tool packages (installed into the sandbox on
            # create; the runner exposes the allowed-via-colon commands).
            # Deploy config (see workspace_app.tooling.packages).
            packages=packages or [],
            prebuilt_dir=prebuilt_dir,
            # Per-message reasoning effort from the UI selector.
            reasoning_effort=body.reasoning_effort,
            # #66: bound the infer_modules tool's per-step classification fan-out.
            infer_modules_parallelism=infer_modules_parallelism,
            # Template profile drives the §A skill index: the runner exposes
            # `read_skill` when the profile ships skills.
            app_slug=_item_slug(investigation_id),
            template_profile=_item_profile(investigation_id),
            # Topic Hub §5/§7: spec + the Hub's collection set let the retriever-free
            # `lookup_glossary` / `resolve_collection` tools query context cards.
            spec=spec,
            collection_ids=hub_collection_ids,
            # #111: card create/update agent tools stamp this user on the write.
            acting_user=author,
            # #242: the resolved speaker for the per-turn "who am I replying to"
            # system note in this shared, multi-collaborator workspace.
            speaker=users.get(author),
            # #275: the directory the `lookup_user` tool resolves a teammate's
            # handle → id/section/email through (handle is all the agent sees).
            users=users,
        )

        def persist(produced: list[TurnMessage]) -> None:
            # Persist the agent's reply + tool outputs so re-entering the
            # workspace shows them, not just the user's own messages.
            if produced:
                conv2 = conv_rm.get(rid).data  # re-fetch THIS chat (not the default)
                assert isinstance(conv2, Conversation)
                # Citations live on `ctx.subagent_citations` — a dict
                # keyed by TOOL NAME (the surface that produced them).
                # Per name, lists are in CALL ORDER, so we keep one
                # cursor per name and pair the Nth bucket entry with
                # the Nth tool message bearing that name. Assistant
                # messages that quote `[N]` bubble against the shared
                # seen-so-far pool (most-recent call wins for marker
                # collisions), so a `[3]` after both an ask_kb call AND
                # an infer_modules call resolves to whichever of them
                # surfaced marker 3 most recently. Tool messages without
                # any stashed citations keep `citations=[]`.
                tool_idx: dict[str, int] = {}
                seen_subagent: list[list[Citation]] = []
                for tm in produced:
                    msg = _to_rca_message(tm)
                    name = tm.tool_name
                    pool = ctx.subagent_citations.get(name) if name is not None else None
                    if pool is not None and name is not None:
                        idx = tool_idx.get(name, 0)
                        if idx < len(pool):
                            msg.citations = list(pool[idx])
                            seen_subagent.append(pool[idx])
                        tool_idx[name] = idx + 1
                    elif tm.role == "assistant" and seen_subagent:
                        msg.citations = _bubble_kb_citations(tm.content, seen_subagent)
                    conv2.messages.append(msg)
                conv_rm.update(rid, conv2)
            activity.record(
                "agent_turn_complete",
                "Agent finished a turn",
                {"investigation_id": investigation_id},
            )

        # Topic Hub §6: prepend the App's context_files (e.g. MEMORY.md +
        # collections.json) as a labelled, authoritative block — re-derived fresh from
        # the live FileStore each turn and handed ONLY to the agent. The persisted user
        # message + the broadcast UserMessage stay clean (block never enters history),
        # so it is idempotent + replay-safe. "" for Apps that declare no context_files.
        from ..apps.context_files import build_context_block

        block = await build_context_block(
            filestore, investigation_id, _app_context_files(investigation_id)
        )
        turn_content = f"{block}\n\n{body.content}" if block else body.content

        # #43: broadcast the human's message to every live viewer, then queue the
        # turn and await ITS completion. The queue serializes concurrent users on
        # the shared sandbox/files (a new message no longer cancels a running
        # turn — Stop does). Live turn events reach all viewers via GET .../stream
        # (item-level / default chat) or the chat-scoped stream (other chats).
        turn_engine.publish(
            engine_key,
            UserMessage(author=author, content=body.content, created_at=created),
        )
        await turn_engine.enqueue(engine_key, turn_content, ctx, on_complete=persist)

    @api.get("/a/{slug}/items/{item_id}/stream")
    async def stream_investigation(slug: str, item_id: str) -> StreamingResponse:
        """#43: the shared per-investigation event stream. Every viewer subscribes
        here and sees all turns live (whoever sent them) + human messages +
        file-changed notices. Live-only — past messages load from the
        conversation resource."""
        investigation_id = _require_item(slug, item_id)
        # subscribe_sse() registers the subscriber NOW (eagerly), so a turn that
        # starts between connect and first body-pull isn't missed.
        return StreamingResponse(
            turn_engine.subscribe_sse(investigation_id), media_type="text/event-stream"
        )

    @api.delete(
        "/a/{slug}/items/{item_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_message(slug: str, item_id: str) -> Response:
        investigation_id = _require_item(slug, item_id)
        # #43 Stop: anyone may interrupt the in-flight turn; the queue keeps
        # draining (queued messages from others are not dropped).
        await turn_engine.cancel_current(investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.delete("/a/{slug}/items/{item_id}/messages")
    async def undo_turns(
        slug: str,
        item_id: str,
        turns: int = Query(..., ge=1, description="How many whole turns to undo (≥1)."),
    ) -> _UndoOut:
        """Undo the last `turns` whole turns (issue #38). A turn is the
        user's prompt plus everything the agent produced for it; undoing
        removes them as a unit (no orphan tool/assistant left behind) so
        the next turn's history no longer sees them. The workspace FILES
        are NOT reverted — undo edits the conversation only."""
        investigation_id = _require_item(slug, item_id)
        rid, conv = _conversation_for(investigation_id)
        cut = _undo_cut_index(conv.messages, turns)
        removed = len(conv.messages) - cut
        conv.messages = conv.messages[:cut]
        conv_rm.update(rid, conv)
        activity.record(
            "turns_undone",
            f"Undid {turns} turn(s)",
            {"investigation_id": investigation_id, "removed": removed},
        )
        return _UndoOut(message_count=len(conv.messages), removed=removed)

    # ── Multi-chat (topic-hub P7, manual §3) ─────────────────────────────
    # An item holds many chats. These chat-scoped endpoints address one chat by id;
    # the item-level endpoints above keep hitting the implicit default chat.

    def _chat_info(
        chat_id: str,
        conv: Conversation,
        default_id: str | None,
        *,
        status: str | None = None,
        last_activity_ms: int | None = None,
    ) -> _ChatInfo:
        return _ChatInfo(
            chat_id=chat_id,
            title=conv.title,
            run_id=conv.run_id,
            created_ms=conv.created_ms,
            message_count=len(conv.messages),
            is_default=chat_id == default_id,
            name_hint=_chat_name_hint(conv),
            status=status,
            last_activity_ms=last_activity_ms,
        )

    def _chat_info_from_resource(
        r, default_id: str | None, run_status: dict[str, str]
    ) -> _ChatInfo:
        """Build a `_ChatInfo` from a fetched conversation resource — fills the run
        status + last-activity stamp the bare `_chat_info` can't derive (#132)."""
        conv = r.data
        assert isinstance(conv, Conversation)
        return _chat_info(
            r.info.resource_id,
            conv,
            default_id,
            status=run_status.get(conv.run_id) if conv.run_id else None,
            last_activity_ms=_dt_ms(r.info.updated_time),
        )

    def _item_run_status(investigation_id: str) -> dict[str, str]:
        """``run_id → status`` for the item's workflow runs — one bounded per-item
        query so the chat list shows each workflow chat's badge without polling each
        run (#132)."""
        from specstar import QB

        out: dict[str, str] = {}
        for r in spec.get_resource_manager(WorkflowRun).list_resources(
            (QB["item_id"] == investigation_id).build()
        ):
            if isinstance(r.data, WorkflowRun):
                out[r.info.resource_id] = str(r.data.status)  # ty: ignore[unresolved-attribute]
        return out

    @api.get("/a/{slug}/items/{item_id}/chats")
    async def list_chats(slug: str, item_id: str) -> list[_ChatInfo]:
        """List the item's chats (free + workflow), **most-recent-activity first**
        (#132 — no "main chat" privilege). Each workflow chat carries its driving
        run's status; every chat carries a name hint + last-activity stamp. Read-only:
        the implicit default chat materialises on first use, not here."""
        from .chats import _item_chats_query, find_default_conversation

        investigation_id = _require_item(slug, item_id)
        default = find_default_conversation(conv_rm, investigation_id)
        default_id = default[0] if default else None
        run_status = _item_run_status(investigation_id)
        infos = [
            _chat_info_from_resource(r, default_id, run_status)
            for r in conv_rm.list_resources(_item_chats_query(investigation_id))
        ]
        # Most-recent activity first; chat_id breaks ties for a stable order.
        infos.sort(key=lambda c: (-(c.last_activity_ms or 0), c.chat_id))
        return infos

    @api.post("/a/{slug}/items/{item_id}/chats", status_code=status.HTTP_201_CREATED)
    async def create_chat(slug: str, item_id: str, body: _CreateChatBody) -> _ChatInfo:
        """Open a new FREE chat in the item (manual §3); returns its chat_id. A
        workflow chat is opened by the run endpoint (P8), not here."""
        from .chats import find_default_conversation

        investigation_id = _require_item(slug, item_id)
        rev = conv_rm.create(
            Conversation(item_id=investigation_id, title=body.title, created_ms=_now_ms())
        )
        default = find_default_conversation(conv_rm, investigation_id)
        return _chat_info_from_resource(
            conv_rm.get(rev.resource_id), default[0] if default else None, {}
        )

    @api.patch("/a/{slug}/items/{item_id}/chats/{chat_id}")
    async def rename_chat(
        slug: str, item_id: str, chat_id: str, body: _RenameChatBody
    ) -> _ChatInfo:
        """Rename a chat (#132) — set its display title from the manage modal."""
        from .chats import find_default_conversation

        investigation_id = _require_item(slug, item_id)
        rid, conv = _require_chat(slug, item_id, chat_id)
        conv.title = body.title
        conv_rm.update(rid, conv)
        default = find_default_conversation(conv_rm, investigation_id)
        return _chat_info_from_resource(
            conv_rm.get(rid), default[0] if default else None, _item_run_status(investigation_id)
        )

    @api.delete("/a/{slug}/items/{item_id}/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_chat(slug: str, item_id: str, chat_id: str) -> Response:
        """Delete a chat (#132). A workflow chat's driving run is **cancelled first**
        (delete also cancels the run); then any in-flight turn / SSE is torn down and
        the conversation removed. Idempotent cancel — a no-op for a terminal run."""
        investigation_id = _require_item(slug, item_id)
        rid, conv = _require_chat(slug, item_id, chat_id)
        if conv.run_id:
            await workflow_orchestrator.cancel(conv.run_id, investigation_id)
        turn_engine.forget(_engine_key(investigation_id, rid))
        conv_rm.delete(rid)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.post(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/messages",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def send_chat_message(
        slug: str, item_id: str, chat_id: str, body: _MessageBody
    ) -> Response:
        investigation_id = _require_item(slug, item_id)
        rid, conv = _require_chat(slug, item_id, chat_id)
        await _send_into(investigation_id, rid, conv, _engine_key(investigation_id, rid), body)
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @api.get("/a/{slug}/items/{item_id}/chats/{chat_id}/stream")
    async def stream_chat(slug: str, item_id: str, chat_id: str) -> StreamingResponse:
        """The chat's own live event stream (manual §3) — per-chat, unlike the
        item-level stream which carries the default chat + item-wide events."""
        investigation_id = _require_item(slug, item_id)
        _require_chat(slug, item_id, chat_id)
        return StreamingResponse(
            turn_engine.subscribe_sse(_engine_key(investigation_id, chat_id)),
            media_type="text/event-stream",
        )

    @api.delete(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_chat_message(slug: str, item_id: str, chat_id: str) -> Response:
        investigation_id = _require_item(slug, item_id)
        _require_chat(slug, item_id, chat_id)
        await turn_engine.cancel_current(_engine_key(investigation_id, chat_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.delete("/a/{slug}/items/{item_id}/chats/{chat_id}/messages")
    async def undo_chat_turns(
        slug: str,
        item_id: str,
        chat_id: str,
        turns: int = Query(..., ge=1, description="How many whole turns to undo (≥1)."),
    ) -> _UndoOut:
        """Undo the last `turns` whole turns of ONE chat (manual §3 + issue #38),
        the chat-scoped twin of `undo_turns`. A turn is the user's prompt plus
        everything the agent produced for it; undoing removes them as a unit. The
        workspace FILES are NOT reverted — undo edits the conversation only."""
        _require_item(slug, item_id)
        rid, conv = _require_chat(slug, item_id, chat_id)
        cut = _undo_cut_index(conv.messages, turns)
        removed = len(conv.messages) - cut
        conv.messages = conv.messages[:cut]
        conv_rm.update(rid, conv)
        return _UndoOut(message_count=len(conv.messages), removed=removed)

    @api.post(
        "/a/{slug}/items/{item_id}/mentions",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def mention_users(slug: str, item_id: str, body: _MentionBody) -> Response:
        """@-mention people in the chat — a pure "come look" summon (does NOT
        run the agent): records a mention entry + notifies each user."""
        investigation_id = _require_item(slug, item_id)
        title = _load_item_title(investigation_id)
        if title is None:
            raise HTTPException(status_code=404, detail=f"unknown item: {investigation_id!r}")
        me = get_user_id()
        _record_mention(investigation_id, title, body.user_ids, body.note, actor=me, author=me)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.post("/a/{slug}/items/{item_id}/promote-to-kb")
    async def promote_chat_to_kb(slug: str, item_id: str) -> dict[str, list[str]]:
        """Manual trigger for chat → knowledge insight extraction. Runs
        synchronously (FE shows a spinner) and returns the SourceDoc ids
        written. `[]` when the chat had no extractable insights, the LLM
        failed, or no chat pipeline is wired (offline / no KB LLM)."""
        investigation_id = _require_item(slug, item_id)
        if kb_chat_pipeline is None:
            return {"insight_ids": []}
        title = _load_item_title(investigation_id)
        if title is None:
            raise HTTPException(status_code=404, detail=f"unknown item: {investigation_id!r}")
        _rid, conv = _conversation_for(investigation_id)
        ids = await _promote_chat_to_kb(
            ingestor=ingestor,
            insights_collection_id=insights_collection_id,
            actor=get_user_id(),
            investigation_id=investigation_id,
            investigation_title=title,
            messages=conv.messages,
        )
        return {"insight_ids": ids}

    @api.get("/a/{slug}/items/{item_id}/export-chat")
    async def export_chat(slug: str, item_id: str) -> Response:
        """Download the conversation in the `.chat.json` round-trip
        format — the KB upload path runs the same insight extraction
        the promote button does on these files (debug / out-of-band
        re-ingestion). The filename guarantees the suffix contract."""
        investigation_id = _require_item(slug, item_id)
        from ..kb.chat_export import CHAT_EXPORT_SUFFIX, build_chat_export

        title = _load_item_title(investigation_id)
        if title is None:
            raise HTTPException(status_code=404, detail=f"unknown item: {investigation_id!r}")
        _rid, conv = _conversation_for(investigation_id)
        payload = build_chat_export(
            title=title,
            messages=[
                {"role": m.role, "content": m.content, "tool_name": m.tool_name or ""}
                for m in conv.messages
            ],
        )
        filename = f"{investigation_id}{CHAT_EXPORT_SUFFIX}"
        return Response(
            content=payload,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ---- Files API (plan-backend §3.8) ----

    @api.get("/a/{slug}/items/{item_id}/files")
    async def list_files(slug: str, item_id: str, prefix: str = "") -> list[_FileEntry]:
        investigation_id = _require_item(slug, item_id)
        paths = await files.ls(investigation_id, prefix)
        out: list[_FileEntry] = []
        for p in sorted(paths):
            data = await files.read(investigation_id, p)
            out.append(_FileEntry(path=p, size=len(data), read_only=_is_readonly_path(p)))
        return out

    @api.get("/a/{slug}/items/{item_id}/files/usage")
    async def workspace_files_usage(slug: str, item_id: str) -> _WorkspaceUsage:
        """#245: the workspace's durable byte total vs its quota — backs the
        upload usage bar. Registered before the ``/files/{path:path}`` read route
        so the literal ``usage`` segment isn't swallowed as a file path."""
        investigation_id = _require_item(slug, item_id)
        return _WorkspaceUsage(
            used=await files.workspace_usage(investigation_id),
            quota=workspace_quota,
        )

    @api.get("/a/{slug}/items/{item_id}/dirs")
    async def list_dirs(slug: str, item_id: str) -> list[str]:
        """Directory paths (incl. empty ones) for the file tree."""
        investigation_id = _require_item(slug, item_id)
        return sorted(await files.listdir(investigation_id))

    @api.post("/a/{slug}/items/{item_id}/files/refresh")
    async def refresh_files(slug: str, item_id: str) -> dict:
        """Force-mirror the live sandbox to the snapshot now (don't wait for the
        ≤window throttle sweep) — the explicit 'refresh' action. No-op cold."""
        investigation_id = _require_item(slug, item_id)
        await registry.flush(investigation_id)
        return {"ok": True}

    # Issue #247: raw folder/root download (no manifest). Registered before the
    # `{path:path}` routes so `files/download/...` isn't swallowed as a file path.
    async def _collect_download_members(
        investigation_id: str, prefix: str
    ) -> list[tuple[str, bytes]]:
        """Every workspace file under `prefix`, re-rooted at it, as
        `(arcname, bytes)`. Reserved `.readonly/` agent snapshots are skipped —
        they're internal diff state, not user content."""
        members: list[tuple[str, bytes]] = []
        for p in sorted(await files.ls(investigation_id, "")):
            if _is_readonly_path(p):
                continue
            arcname = subtree_arcname(p, prefix)
            if arcname is None:
                continue
            members.append((arcname, await files.read(investigation_id, p)))
        return members

    def _workspace_zip_name(investigation_id: str, prefix: str) -> str:
        folder = prefix.strip("/").rsplit("/", 1)[-1]
        # subfolder → its name; root → the item title; untitled → "workspace".
        name = folder or _load_item_title(investigation_id) or ""
        return safe_zip_filename(name, fallback="workspace")

    @api.post("/a/{slug}/items/{item_id}/files/download/prepare")
    async def prepare_files_download(slug: str, item_id: str, prefix: str = "") -> DownloadPrepared:
        """Build a plain ZIP of the raw bytes of every file under `prefix`
        (`prefix=""` = the whole workspace), entries re-rooted at the folder.
        Reading routes warm→sandbox / cold→snapshot via the facade; only the
        compression runs off the event loop."""
        investigation_id = _require_item(slug, item_id)
        members = await _collect_download_members(investigation_id, prefix)
        download_id, size = await prepare_zip(lambda out: write_zip_members(out, members))
        return DownloadPrepared(
            download_id=download_id,
            filename=_workspace_zip_name(investigation_id, prefix),
            size=size,
        )

    @api.get("/a/{slug}/items/{item_id}/files/download/{download_id}")
    async def stream_files_download(
        slug: str, item_id: str, download_id: str, prefix: str = ""
    ) -> FileResponse:
        """Stream a prepared workspace ZIP once, then delete it. 404 when the id
        is malformed / already streamed / reaped."""
        investigation_id = _require_item(slug, item_id)
        path = prepared_path(download_id)
        if path is None:
            raise HTTPException(status_code=404, detail="download not found")
        return stream_prepared_zip(path, _workspace_zip_name(investigation_id, prefix))

    @api.put(
        "/a/{slug}/items/{item_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def write_file(slug: str, item_id: str, path: str, request: Request) -> Response:
        investigation_id = _require_item(slug, item_id)
        norm = "/" + path.lstrip("/")
        if _is_readonly_path(norm):
            # #205: the `.readonly/` snapshot the human diffs against is not hand-editable.
            raise HTTPException(status_code=403, detail="this file is read-only")
        # #219: stream the body to a staging file (never the whole upload in RAM),
        # enforcing the single-file cap as bytes arrive, then stream it into the
        # store. `files.write_from_path` routes warm→sandbox / cold→blob.
        # #245: also gate the per-workspace total quota mid-stream.
        await _stream_upload_to_store(
            investigation_id, norm, request, files, max_file_size, workspace_quota
        )
        activity.record(
            "file_written",
            f"Wrote {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        # #43: tell other viewers of this shared workspace the file changed so
        # they refetch (last-write-wins; this is the "someone else edited" cue).
        turn_engine.publish(
            investigation_id, FileChanged(path=norm, by=get_user_id(), kind="written")
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # POST /files/mkdir and /move and /copy are registered before the
    # {path:path} routes so their literal segments can't be swallowed as a
    # path (distinct methods anyway, but keeping them first documents intent).
    @api.post(
        "/a/{slug}/items/{item_id}/files/mkdir",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def make_dir(slug: str, item_id: str, body: _MkdirBody) -> Response:
        investigation_id = _require_item(slug, item_id)
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
        turn_engine.publish(
            investigation_id, FileChanged(path=norm, by=get_user_id(), kind="dir_created")
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

    @api.post(
        "/a/{slug}/items/{item_id}/files/move",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def move_file(slug: str, item_id: str, body: _MoveBody) -> Response:
        investigation_id = _require_item(slug, item_id)
        src = "/" + body.from_.strip("/")
        dst = "/" + body.to.strip("/")
        await _transfer(investigation_id, src, dst, copy=False)
        activity.record(
            "file_moved",
            f"Moved {src} → {dst}",
            {"investigation_id": investigation_id, "path": dst},
        )
        turn_engine.publish(investigation_id, FileChanged(path=dst, by=get_user_id(), kind="moved"))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.post(
        "/a/{slug}/items/{item_id}/files/copy",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def copy_file(slug: str, item_id: str, body: _MoveBody) -> Response:
        investigation_id = _require_item(slug, item_id)
        src = "/" + body.from_.strip("/")
        dst = "/" + body.to.strip("/")
        await _transfer(investigation_id, src, dst, copy=True)
        activity.record(
            "file_copied",
            f"Copied {src} → {dst}",
            {"investigation_id": investigation_id, "path": dst},
        )
        turn_engine.publish(
            investigation_id, FileChanged(path=dst, by=get_user_id(), kind="copied")
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

    @api.post("/a/{slug}/items/{item_id}/search")
    async def search(slug: str, item_id: str, body: _SearchBody) -> list[dict]:
        investigation_id = _require_item(slug, item_id)
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

    @api.post("/a/{slug}/items/{item_id}/replace")
    async def replace(slug: str, item_id: str, body: _ReplaceBody) -> dict:
        investigation_id = _require_item(slug, item_id)
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

    @api.delete(
        "/a/{slug}/items/{item_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_file(slug: str, item_id: str, path: str) -> Response:
        investigation_id = _require_item(slug, item_id)
        norm = "/" + path.lstrip("/")
        if await files.is_dir(investigation_id, norm):
            await files.rmdir(investigation_id, norm)
            activity.record(
                "dir_deleted",
                f"Deleted folder {norm}",
                {"investigation_id": investigation_id, "path": norm},
            )
            turn_engine.publish(
                investigation_id, FileChanged(path=norm, by=get_user_id(), kind="deleted")
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
        turn_engine.publish(
            investigation_id, FileChanged(path=norm, by=get_user_id(), kind="deleted")
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.get("/a/{slug}/items/{item_id}/files/{path:path}")
    async def read_file(slug: str, item_id: str, path: str) -> Response:
        investigation_id = _require_item(slug, item_id)
        import mimetypes

        try:
            data = await files.read(investigation_id, "/" + path.lstrip("/"))
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Issue #40: extension → MIME first so workspace markdown reports
        # rendering `![foo](./foo.png)` get `Content-Type: image/png`
        # (the browser inlines) instead of `application/octet-stream`
        # (the browser offers a download). Unknown extension → fall back
        # to the previous UTF-8 sniff so text-with-unknown-extension
        # still renders in the file viewer.
        guessed, _ = mimetypes.guess_type(path)
        if guessed:
            media_type = guessed
        else:
            try:
                data.decode("utf-8")
                media_type = "text/plain; charset=utf-8"
            except UnicodeDecodeError:
                media_type = "application/octet-stream"
        return Response(content=data, media_type=media_type)

    # ---- Notebook cell execution (plan-backend §7.3) ----

    @api.post("/a/{slug}/items/{item_id}/notebooks/{notebook_path:path}/cells/{idx}/execute")
    async def execute_cell(
        slug: str,
        item_id: str,
        notebook_path: str,
        idx: int,
        body: _CellExecuteBody,
    ) -> StreamingResponse:
        investigation_id = _require_item(slug, item_id)
        handle = await kernels.get_or_start(investigation_id, notebook_path)

        async def gen() -> AsyncIterator[str]:
            ev: CellEvent
            async for ev in kernels.execute_cell(handle, body.code):
                yield to_sse(ev)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @api.delete(
        "/a/{slug}/items/{item_id}/notebooks/{notebook_path:path}/cells/{idx}/execute",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def interrupt_cell(slug: str, item_id: str, notebook_path: str, idx: int) -> Response:
        investigation_id = _require_item(slug, item_id)
        handle = kernels.peek(investigation_id, notebook_path)
        if handle is not None:
            await kernels.interrupt(handle)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @api.post(
        "/a/{slug}/items/{item_id}/notebooks/{notebook_path:path}/kernel/restart",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def restart_kernel(slug: str, item_id: str, notebook_path: str) -> Response:
        investigation_id = _require_item(slug, item_id)
        handle = kernels.peek(investigation_id, notebook_path)
        if handle is not None:
            await kernels.restart(handle)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Direct sandbox shell — backs the FE Terminal pane ----

    @api.post("/a/{slug}/items/{item_id}/exec")
    async def exec_in_sandbox(slug: str, item_id: str, body: _ExecBody) -> dict[str, object]:
        investigation_id = _require_item(slug, item_id)
        if not body.cmd:
            raise HTTPException(status_code=422, detail="cmd must be non-empty")
        try:
            session = await registry.session(investigation_id)
            handle = await registry.ensure_handle(session)
            result = await sandbox.exec(handle, body.cmd)
        except Exception as exc:  # noqa: BLE001
            # The Terminal pane has nowhere to render an HTTP error and the
            # agent's exec tool expects a structured ExecResult body — any
            # unexpected failure becomes a 200 with a non-zero exit code and
            # the error in stderr (so the consumer sees a normal command
            # failure). In-sandbox "command not found" / "permission denied"
            # are already translated to POSIX exits 127/126 inside the sandbox
            # impls, so we only land here for genuinely unexpected failures.
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"sandbox error: {type(exc).__name__}: {exc}\n",
            }
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

    # #177: now that EVERY route (specstar CRUD + all hand-written) is on the
    # /api router, include it onto the app exactly once. Mounting the SPA at "/"
    # afterwards means any non-/api path falls through to the SPA history
    # fallback, so a refreshed client route can't be shadowed by an API route.
    app.include_router(api)

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
