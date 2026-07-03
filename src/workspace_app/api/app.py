from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from agents.tracing import set_trace_processors
from fastapi import APIRouter, FastAPI
from specstar import SpecStar

from ..agent.config_catalog import AgentConfigCatalog
from ..config.schema import EnhancementSettings
from ..files import WorkspaceFiles
from ..filestore.protocol import FileStore
from ..health import CheckRegistry, CheckResult
from ..health.replay import ReplayService
from ..health.service import HealthService
from ..kb.chunker import Chunker
from ..kb.embedder import Embedder, HashEmbedder
from ..kb.llm import ILlm, LitellmLlm
from ..kb.retriever import Enhancements, Retriever
from ..kb.vlm import IVlm, VlmDescriber
from ..kernels import KernelService
from ..monitor import IMonitor, InMemoryMonitor, MonitorProcessor
from ..observability.boot import boot_step
from ..resources import (
    AgentConfig,
    CheckRun,
)
from ..resources.kb import EMBED_DIM, Collection
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..sync import SandboxSync
from ..tooling.registry import PackageInfo
from ..turn_control import SpecstarTurnControl
from ..users import MockUserDirectory, UserDirectory
from ..workflow.credential import CredentialBroker
from ..workflow.discovery import load_run_callable
from ..workflow.orchestrator import (
    WorkflowOrchestrator,
)
from .activity import ActivityLog
from .capability_routes import register_capability_routes
from .card_gen_routes import register_card_gen_routes
from .chat_routes import register_chat_routes
from .chat_send import ChatSendService
from .context_card_routes import register_context_card_actions, register_context_card_routes
from .doc_question_routes import register_doc_question_routes
from .entity_routes import register_entity_routes
from .file_routes import register_file_routes
from .health_routes import (
    register_health_routes,
    register_replay_routes,
    register_sanity_routes,
)
from .item_routes import register_item_routes
from .kb_chat_routes import (
    register_kb_chat_routes,
)
from .kb_routes import register_kb_routes
from .lifecycle import build_lifespan
from .locator import ItemLocator
from .mention import MentionService
from .meta_routes import register_meta_routes
from .notifications import register_notification_routes
from .registry import InvestigationRegistry
from .replay_loaders import ReplayLoaders
from .runner import AgentRunner
from .sandbox_activity import IActivityStore, SpecstarActivityStore
from .sandbox_address import IAddressStore, SpecstarAddressStore
from .spa import SpaStaticFiles
from .subagent_bridge import SubagentBridge
from .tools_routes import register_tools_routes
from .turn_context import TurnContextBuilder
from .turns import ChatTurnEngine
from .workflow_exec import WorkflowExecutor
from .workflow_routes import register_workflow_routes


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
    # #356: the LLM the Tune-parsing "Try answer" path streams through — the
    # kb_chat model, answering from a FIXED doc∩top-k passage set (no self-search).
    # None ⇒ build a plain LitellmLlm from the default kb_chat AgentConfig so the
    # probe answers with the same model a user's KB chat would; tests inject a fake.
    answer_llm: ILlm | None = None,
    # #175: the LLM that drafts context cards from documents (自動 context card).
    # None ⇒ a no-op drafter (the feature stays mounted but proposes nothing).
    # __main__ passes factories.get_card_drafter_llm(settings).
    card_drafter_llm: ILlm | None = None,
    # #105: the LLM-as-judge that scores a doc's quality at index time. None ⇒
    # scoring off (docs stay un-scored = neutral; search ranking unaffected).
    # __main__ passes factories.get_kb_quality_judge_llm(settings).
    quality_judge_llm: ILlm | None = None,
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
    # #262: user ids with UNRESTRICTED collection access — threaded into the
    # route-level `authorize(...)` guards (the dedicated permission endpoint +
    # content-route guards). MUST match the set passed to `make_spec(superusers=…)`
    # (which feeds the storage-layer access_scope + write checker) — both come from
    # `settings.server.superusers`.
    superusers: frozenset[str] = frozenset(),
    users: UserDirectory | None = None,
    monitor: IMonitor | None = None,
    spa_dist: Path | None = None,
    root_path: str = "",
    # #312: whether THIS process drains the job queues in-process. Default True
    # keeps the all-in-one behaviour (local dev / tests / single-pod deploys).
    # A pod-split deploy sets it False on the API Deployment so the API is a pure
    # producer (it still enqueues), and dedicated worker pods consume each
    # JobType under their own HPA. Sweepers (idle/mirror/index/blob-gc/code-sync)
    # are NOT gated — they always run on the API (the always-on control plane).
    run_consumers: bool = True,
    # #349: how often a running turn polls the shared (cross-pod) cancel epoch.
    # The cancel latency under degraded sticky routing equals this interval; the
    # in-pod fast-path is unaffected. Threaded from
    # settings.server.turn_cancel_poll_seconds.
    turn_cancel_poll_seconds: float = 0.5,
    idle_timeout: timedelta = timedelta(hours=8),
    idle_check_interval: timedelta = timedelta(seconds=60),
    mirror_interval: timedelta = timedelta(seconds=5),
    # #345: soft cap (bytes) on ONE item's shared scratch dir; the idle reaper's
    # du-sweep recycles any item over it so a runaway workspace can't fill the
    # scratch volume the whole fleet shares. 0 ⇒ disabled (the lenient default).
    # Threaded from settings.sandbox.max_workspace_bytes.
    max_workspace_bytes: int = 0,
    # P3.0: background code-repo sync sweeper interval. None ⇒ sweeper
    # disabled (manual /sync only). __main__ derives this from
    # Settings.sync_check_interval_sec.
    code_sync_check_interval: timedelta | None = None,
    # #355: server-local "HH:MM" wall-clock time the daily code-collection
    # auto-sync fires (None ⇒ off). __main__ derives this from
    # Settings.kb.git.daily_sync.
    code_daily_sync: str | None = None,
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
    # #105: the document-quality prior's strength + optional hard floor.
    # __main__ threads `settings.kb.retrieval.quality_weight / quality_floor`.
    kb_quality_weight: float = 0.10,
    kb_quality_floor: int | None = None,
    # #195: per-turn cap on `kb_search` calls for the KB chat turn + the
    # ask_knowledge_base bridge. `None` ⇒ unlimited (also what other surfaces
    # like Topic Hub use). __main__ threads `settings.kb.max_searches_per_turn`
    # (default 3); the default here stays None so tests that don't pass it keep
    # the unlimited pre-#195 behaviour.
    kb_max_searches_per_turn: int | None = None,
    # #334: upper bound a per-message kb_search-count pick may request (the
    # composer's value is clamped to [0, this]). __main__ threads
    # `settings.kb.max_searches_ceiling` (default 10).
    kb_max_searches_ceiling: int = 10,
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
    # #356: the Tune-parsing "Try answer" LLM — same model as the KB chat, so the
    # probe's answer mirrors production. A plain LitellmLlm (no agent tool loop):
    # it's handed a FIXED passage set, so it must NOT self-search.
    kb_answer_llm = answer_llm or LitellmLlm(
        default_kb_agent_config.model,
        base_url=default_kb_agent_config.llm_base_url or None,
        api_key=default_kb_agent_config.llm_api_key or None,
    )
    # Same shape for infer_modules: fall back to bundled when the
    # supplied catalog didn't wire one (legacy positional-list tests).
    default_infer_modules_config = catalog.infer_modules() or _bundled.infer_modules()
    assert default_infer_modules_config  # bundled always populates infer_modules

    # Live telemetry monitor (issue #11), resolved here — before SandboxSync —
    # so the durable-sync telemetry (#407: one summary event per mirror/restore)
    # lands in the same sink as the agent/LLM traces. The trace-processor is
    # registered a few lines down once the app-level wiring is complete.
    monitor = monitor if monitor is not None else InMemoryMonitor()
    sync = SandboxSync(filestore=filestore, sandbox=sandbox, monitor=monitor)
    # #345: only the local process sandbox keeps an item's working dir on a
    # shared volume across pods, so only it needs the GLOBAL activity heartbeat
    # that lets the idle reaper recycle a dir solely when no pod is using it.
    # Other backends (mock/http) own their own per-pod lifecycle → no heartbeat.
    from ..sandbox.http_client import HttpSandbox
    from ..sandbox.local_process import LocalProcessSandbox

    activity_store: IActivityStore | None = (
        SpecstarActivityStore(spec) if isinstance(sandbox, LocalProcessSandbox) else None
    )
    # #366: the HTTP sandbox-host mints a per-pod uuid handle on every `create`
    # (it does NOT reattach by item id), so two pods diverge into two sandboxes
    # for one item. The shared per-item address store makes them converge on ONE
    # live sandbox (CAS publish + dead-handle rebuild). Local/mock already
    # converge via the item-keyed shared dir, so they need no address store.
    address_store: IAddressStore | None = (
        SpecstarAddressStore(spec) if isinstance(sandbox, HttpSandbox) else None
    )
    registry = InvestigationRegistry(
        sandbox=sandbox,
        default_spec=SandboxSpec(),
        sync=sync,
        activity=activity_store,
        address=address_store,
    )
    # The single chokepoint for workspace file ops (agent tools + file routes):
    # routes to the live sandbox (single source of truth) when one is up for the
    # investigation, else to the FileStore snapshot. registry.peek_handle reads
    # liveness without waking — only exec wakes a cold sandbox.
    files = WorkspaceFiles(filestore, sandbox, registry.peek_handle)
    kernels = KernelService()
    activity = ActivityLog()
    # Feed the monitor (resolved above) from the OpenAI Agents SDK's own tracing
    # — every run's LLM generations (with token usage), tool calls and agent
    # steps flow through MonitorProcessor in real time (issue #11). Registering
    # replaces the SDK's default (OpenAI-backend) exporter, which we don't use.
    set_trace_processors([MonitorProcessor(monitor)])

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

    lifespan = build_lifespan(
        registry=registry,
        spec=spec,
        kernels=kernels,
        health_service=health_service,
        filestore=filestore,
        monitor=monitor,
        run_consumers=run_consumers,
        idle_timeout=idle_timeout,
        idle_check_interval=idle_check_interval,
        mirror_interval=mirror_interval,
        max_workspace_bytes=max_workspace_bytes,
        code_sync_check_interval=code_sync_check_interval,
        code_daily_sync=code_daily_sync,
        gc_interval=gc_interval,
        gc_t1=gc_t1,
        gc_t2=gc_t2,
    )

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

    register_meta_routes(
        api, users=users, get_user_id=get_user_id, activity=activity, monitor=monitor
    )

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
    from ..coordinators import build_ingestor

    embedder = kb_embedder or HashEmbedder(dim=EMBED_DIM)
    # #312: the ingestor is built by the shared `build_ingestor` so the worker
    # entrypoint constructs it identically. Pipeline mode (P1) takes precedence;
    # the legacy chunker stays for tests + offline runs (handled inside).
    ingestor = build_ingestor(
        spec,
        embedder=embedder,
        pipeline=kb_pipeline,
        chunker=kb_chunker,
        chat_pipeline=kb_chat_pipeline,
        code_embedder=kb_code_embedder,
        parser_registry=kb_parser_registry,
    )
    # #54: the code-sync sweeper (in api/lifecycle.py) reads the ingestor off
    # app.state at startup — the ingestor is built after the FastAPI app, so the
    # lifespan can't capture it directly (symmetric with the coordinators below).
    app.state.ingestor = ingestor
    # P2: ensure the "Investigations Knowledge" collection exists at boot so
    # the chat-promote path always has a target. Idempotent (re-uses a
    # collection with the same name).
    insights_collection_id = _ensure_insights_collection(spec, insights_collection_name)
    # #312: the background job coordinators are built by the shared
    # `build_coordinators` composition root — the SAME one the standalone worker
    # entrypoint uses — so the API can run as a pure producer (its consumers
    # gated off via `run_consumers`) while dedicated worker pods each consume one
    # JobType. The API layer still owns the request-stack wiring below: route
    # registration, the reindex-on-edit trigger, and `app.state` exposure.
    from ..coordinators import build_coordinators, resolve_wiki_config
    from ..kb.wiki.orchestrator import default_wiki_merge_config
    from ..kb.wiki.reader import default_wiki_reader_config

    coordinators = build_coordinators(
        spec,
        ingestor=ingestor,
        runner=runner,
        catalog=catalog,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
        quality_judge_llm=quality_judge_llm,
        card_drafter_llm=card_drafter_llm,
        sanity_llm_factory=sanity_llm_factory,  # ty: ignore[invalid-argument-type]
        sanity_judge_llm=sanity_judge_llm,
        wiki_maintainer_max_turns=wiki_maintainer_max_turns,
        wiki_model=wiki_model,
        wiki_llm_base_url=wiki_llm_base_url,
        wiki_llm_api_key=wiki_llm_api_key,
    )
    wiki_coordinator = coordinators.wiki
    index_coordinator = coordinators.index
    app.state.wiki_coordinator = wiki_coordinator
    # #87: a content edit (the FE's blob-upload + CAS PATCH /source-doc/{id})
    # auto-enqueues a reindex via a SourceDoc patch event_handler — wired here
    # (API-side: edits only happen through the API), after the coordinator exists.
    index_coordinator.install_reindex_on_edit()
    app.state.index_coordinator = index_coordinator
    card_gen_coordinator = coordinators.card_gen
    app.state.card_gen_coordinator = card_gen_coordinator
    register_card_gen_routes(api, card_gen_coordinator)
    # #377: the global "待釐清" inbox — answer/discard the clarification questions
    # the digest raised. A term answer becomes a context card (the card-drafter LLM
    # tidies it, verbatim fallback when none is wired); a description answer lands
    # on the collection's clarification wiki page.
    from ..kb.answer_formatter import LlmAnswerCardFormatter, VerbatimAnswerFormatter
    from ..kb.wiki.store import WikiFileStore

    register_doc_question_routes(
        api,
        spec,
        formatter=(
            LlmAnswerCardFormatter(card_drafter_llm)
            if card_drafter_llm is not None
            else VerbatimAnswerFormatter()
        ),
        wiki_store=WikiFileStore(spec),
    )
    # Model-sanity battery routes mount only when the live-LLM factory is wired.
    sanity_coordinator = coordinators.sanity
    if sanity_coordinator is not None:
        register_sanity_routes(api, sanity_models or [], sanity_coordinator)
    app.state.sanity_coordinator = sanity_coordinator
    # The chat agent shares the injected runner; its retriever uses the same
    # embedder as ingestion so query and document vectors are comparable.
    # When a KB llm is wired, the retriever gains multi-query + HyDE + rerank.
    # Built BEFORE register_kb_routes so the findability probe (#328) can rank a
    # doc through the real hybrid pipeline.
    kb_retriever = Retriever(
        spec,
        embedder=embedder,
        llm=kb_llm,
        code_embedder=kb_code_embedder,
        enhancement_defaults=kb_retrieval_enhancements,
        quality_weight=kb_quality_weight,
        quality_floor=kb_quality_floor,
    )
    register_kb_routes(
        api,
        spec,
        ingestor,
        wiki_coordinator,
        index_coordinator=index_coordinator,
        retriever=kb_retriever,
        get_user_id=get_user_id,
        superusers=superusers,
        answer_llm=kb_answer_llm,
        answer_system_prompt=default_kb_agent_config.system_prompt,
    )
    # #106: the exposed deterministic context-card lookup (read route, post-apply).
    register_context_card_routes(api, spec)
    # #230: the /help endpoint — the Help collection id + its documents for the
    # platform help page (its KB chat scopes to that id; the doc list links to
    # the KB document viewer).
    from .help_routes import register_help_routes

    register_help_routes(api, spec)
    # #349: the cross-pod cancel epoch, backed by specstar so every replica over
    # the shared store reads/bumps the SAME per-key counter. Shared by BOTH turn
    # engines — their keys (item / conversation / KB-chat ids) are globally
    # unique specstar ids, so one TurnEpoch table can't collide across surfaces.
    # The in-pod fast-path (cancel-prior / Stop on this engine) still fires; this
    # is what reaches a turn stranded on a peer pod when sticky routing degrades.
    turn_control = SpecstarTurnControl(spec)
    # One turn engine drives the RCA workspace; one cancellable in-flight turn
    # per conversation, SSE streaming, cancel hook.
    turn_engine = ChatTurnEngine(
        runner, turn_control=turn_control, poll_interval=turn_cancel_poll_seconds
    )
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
        reader_config=resolve_wiki_config(
            catalog,
            "wiki_reader",
            default_wiki_reader_config,
            wiki_model=wiki_model,
            wiki_llm_base_url=wiki_llm_base_url,
            wiki_llm_api_key=wiki_llm_api_key,
        ),
        merge_config=resolve_wiki_config(
            catalog,
            "wiki_merge",
            default_wiki_merge_config,
            wiki_model=wiki_model,
            wiki_llm_base_url=wiki_llm_base_url,
            wiki_llm_api_key=wiki_llm_api_key,
        ),
        reader_max_turns=wiki_reader_max_turns,
    )
    kb_turn_engine = ChatTurnEngine(
        kb_runner, turn_control=turn_control, poll_interval=turn_cancel_poll_seconds
    )
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
        max_searches_ceiling=kb_max_searches_ceiling,
        # #397: KB chat's request_wiki_update tool submits corrections through this.
        wiki_coordinator=wiki_coordinator,
    )

    # Cached fallback configs per sub-agent purpose, used when the
    # catalog the caller supplied didn't wire that purpose (legacy
    # positional-list tests). Bundled always populates kb_chat /
    # infer_modules so these defaults are always available.
    _purpose_fallbacks: dict[str, AgentConfig] = {
        "kb_chat": default_kb_agent_config,
        "infer_modules": default_infer_modules_config,
    }

    # #54: the generic sub-agent bridge (ask_knowledge_base / infer_modules / future)
    # is one module the turn-driving glue + the workflow executor share.
    subagent_bridge = SubagentBridge(
        spec=spec,
        runner=runner,
        kb_runner=kb_runner,
        retriever=kb_retriever,
        catalog=catalog,
        purpose_fallbacks=_purpose_fallbacks,
        get_user_id=get_user_id,
        max_searches=kb_max_searches_per_turn,
    )
    _run_subagent = subagent_bridge.run

    # #54: the item locator owns the slug/profile/title scan + default-chat /
    # engine-key / chat-validation rules every workspace route crosses. The route
    # modules + the turn/mention/replay services call ``locator.<method>`` directly.
    locator = ItemLocator(spec, app_catalog)

    mention_svc = MentionService(spec=spec, locator=locator)

    # #54: the single builder for an RCA turn's AgentToolContext. Both the
    # interactive send path (`_send_into`) and the workflow node driver
    # (`_wf_drive_turn`) build their ctx through this, so a new ctx field is added
    # once instead of in two hand-rolled constructions that can drift.
    turn_ctx = TurnContextBuilder(
        sandbox=sandbox,
        filestore=filestore,
        files=files,
        sync=sync,
        registry=registry,
        locator=locator,
        agent_mention=mention_svc.agent_mention,
        describer=vlm_describer,
        deck_vlm=deck_vlm,
        users=users,
        spec=spec,
        packages=packages,
        prebuilt_dir=prebuilt_dir,
        read_file_max_lines=read_file_max_lines,
        read_file_max_chars=read_file_max_chars,
        exec_output_max_chars=exec_output_max_chars,
        infer_modules_parallelism=infer_modules_parallelism,
        history_max_messages=history_max_messages,
        history_max_context_tokens=history_max_context_tokens,
        # #397: lets the request_wiki_update tool submit a user's wiki correction.
        wiki_coordinator=wiki_coordinator,
    )

    # ── replay diagnostics (#51 P4) ──────────────────────────────────
    # Read-only loaders: replay must never create/mutate anything, so
    # these do their own lookups instead of reusing `_conversation_for`
    # (which creates a conversation for a fresh investigation).

    replay = ReplayLoaders(
        spec=spec,
        locator=locator,
        packages=packages,
        default_kb_agent_config=default_kb_agent_config,
    )

    register_replay_routes(
        api, service=replay_service, load_turn=replay.load_turn, load_doc=replay.load_doc
    )

    # ── Workflows (#100) ─────────────────────────────────────────────
    # A run drives its own WORKFLOW CHAT (§3): agent nodes stream into that chat and
    # the orchestrator overlays phase/step events on the same per-chat stream.
    from ..apps.profiles import load_profile_workflow
    from ..workflow.dsl import build_run
    from ..workflow.workspace_store import load_workspace_workflow

    async def _load_workspace(item_id: str, workflow_id: str):
        """#323 P4 (manual §22, Q5): resolve a WORKSPACE-authored ``.workflows/<id>.json``
        in this item to its ``(run, manifest)`` — the interpreter + its manifest from the
        one parsed DSL — or ``None`` (absent / malformed), so the orchestrator falls back
        to a package workflow."""
        res = await load_workspace_workflow(files, item_id, workflow_id)
        return (build_run(res[0]), res[1]) if res is not None else None

    # #54: the workflow execution callbacks (agent turn / sandbox / ingest / card
    # upsert+find / landed-check) plus the orchestrator's upload-dir / wire-handle /
    # release / notify-failure hooks all bind ``create_app``'s services through one
    # adapter. The orchestrator wiring + capability routes call its methods.
    workflow_executor = WorkflowExecutor(
        spec=spec,
        files=files,
        registry=registry,
        sandbox=sandbox,
        ingestor=ingestor,
        index_coordinator=index_coordinator,
        turn_engine=turn_engine,
        turn_ctx=turn_ctx,
        locator=locator,
        run_subagent=_run_subagent,
    )

    workflow_credentials = CredentialBroker()
    workflow_orchestrator = WorkflowOrchestrator(
        spec=spec,
        store=files,  # WorkspaceFiles is FileStore-shaped (read/write by workspace id)
        load_run=load_run_callable,
        load_manifest=load_profile_workflow,
        load_workspace=_load_workspace,
        load_upload_dir=workflow_executor.upload_dir,
        wire_handle=workflow_executor.wire_handle,
        publish=turn_engine.publish,
        release=workflow_executor.release,
        notify_failure=workflow_executor.notify_failure,
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

    register_workflow_routes(
        api,
        spec=spec,
        files=files,
        locator=locator,
        get_user_id=get_user_id,
        activity=activity,
        turn_engine=turn_engine,
        workflow_orchestrator=workflow_orchestrator,
        workflow_executor=workflow_executor,
    )

    chat_send_svc = ChatSendService(
        spec=spec,
        locator=locator,
        turn_ctx=turn_ctx,
        subagent_bridge=subagent_bridge,
        filestore=filestore,
        files=files,
        users=users,
        activity=activity,
        turn_engine=turn_engine,
        get_user_id=get_user_id,
        infer_modules_collection=infer_modules_collection,
        infer_modules_enhancements=infer_modules_enhancements,
        infer_modules_reasoning_effort=infer_modules_reasoning_effort,
        # #334: the composer's per-message kb_search-count pick (one budget shared
        # across the turn's ask_knowledge_base calls); default + ceiling from config.
        kb_max_searches_per_turn=kb_max_searches_per_turn,
        kb_max_searches_ceiling=kb_max_searches_ceiling,
    )

    register_chat_routes(
        api,
        spec=spec,
        locator=locator,
        turn_engine=turn_engine,
        activity=activity,
        get_user_id=get_user_id,
        workflow_orchestrator=workflow_orchestrator,
        ingestor=ingestor,
        insights_collection_id=insights_collection_id,
        kb_chat_pipeline=kb_chat_pipeline,
        send_into=chat_send_svc.send,
        record_mention=mention_svc.record,
    )

    # ---- Files API (plan-backend §3.8) ----

    register_item_routes(
        api,
        spec=spec,
        filestore=filestore,
        get_user_id=get_user_id,
        activity=activity,
        registry=registry,
        turn_engine=turn_engine,
        locator=locator,
        ingestor=ingestor,
        insights_collection_id=insights_collection_id,
        kb_chat_pipeline=kb_chat_pipeline,
    )

    register_tools_routes(
        api,
        spec=spec,
        app_catalog=app_catalog,
        packages=packages,
        locator=locator,
    )

    register_capability_routes(
        api,
        spec=spec,
        locator=locator,
        get_user_id=get_user_id,
        workflow_credentials=workflow_credentials,
        workflow_executor=workflow_executor,
    )

    register_file_routes(
        api,
        files=files,
        registry=registry,
        kernels=kernels,
        sandbox=sandbox,
        locator=locator,
        get_user_id=get_user_id,
        turn_engine=turn_engine,
        activity=activity,
        workspace_quota=workspace_quota,
        max_file_size=max_file_size,
    )

    # #419: file-first entity CRUD. Opt-in — an item with no `.entity/` schema
    # dir yields an empty catalog, so these routes are safe no-ops there.
    register_entity_routes(
        api,
        files=files,
        locator=locator,
        get_user_id=get_user_id,
        activity=activity,
        spec=spec,
        users=users,
    )

    # #177: now that EVERY route (specstar CRUD + all hand-written) is on the
    # /api router, include it onto the app exactly once. Mounting the SPA at "/"
    # afterwards means any non-/api path falls through to the SPA history
    # fallback, so a refreshed client route can't be shadowed by an API route.
    app.include_router(api)

    # Defer specstar's OpenAPI customisation until first access. Building the
    # schema walks every registered route (~1600) and is ~3.5s — the single
    # biggest cost inside create_app. The running server needs it only when
    # /openapi.json or /docs is hit, and the test suite builds an app per test,
    # so paying it eagerly here dominated CI wall time. FastAPI serves the schema
    # through app.openapi(); wrap that hook so specstar's customize runs once, on
    # first request, and caches into app.openapi_schema. `spec.apply(...,
    # auto_include=False)` above deliberately skipped the eager build, and by now
    # EVERY route (specstar CRUD + all hand-written) is on the app, so the lazily
    # built schema is complete — the custom workspace routes stay discoverable in
    # /openapi.json (FE / Swagger), just without the per-boot / per-test cost.
    def _openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            spec.openapi(app)  # customises + caches into app.openapi_schema
        return cast("dict[str, Any]", app.openapi_schema)

    # FastAPI's documented override hook; ty models app.openapi as the unbound
    # method, so the no-self replacement trips invalid-assignment.
    app.openapi = _openapi  # ty: ignore[invalid-assignment]

    # Mount the built SPA last so API routes registered above take precedence
    # over the catch-all static handler. If no build exists, skip silently —
    # the API alone is still usable (e.g. via curl or the specstar admin UI).
    if spa_dist is None:
        spa_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if spa_dist.is_dir() and (spa_dist / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=spa_dist, html=True), name="spa")

    return app
