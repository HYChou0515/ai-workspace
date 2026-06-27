"""Composition root — nested `Settings` + `get_*(settings) -> Protocol` factories.

The single place that decides *which* implementation backs each Protocol seam
(sandbox / filestore / runner / embedder / chunker / KB llm) + the specstar data
layer. Everything downstream (`create_app` and the app internals) depends only
on the Protocols, never on a concrete implementation or on `Settings`.

`__main__` reads `load_settings()` and wires the factories into `create_app`.
Tests inject mocks/scripted impls directly and do NOT go through these
factories — the factories serve the production composition only.

The `Settings` itself is the nested dataclass defined in `config.schema`
(re-exported here for backward compat of `from workspace_app.factories
import Settings`). The legacy flat `Settings.from_env(...)` is gone — use
`config.loader.load(config_path=..., env=...)` (re-exported as
`load_settings`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from specstar import BackendBinding, BackendConfig, ConnectionProfile, SpecStar

from workspace_app.resources import make_spec

from .agent.config_catalog import AgentConfigCatalog
from .api.litellm_runner import LitellmAgentRunner
from .api.runner import AgentRunner
from .apps.catalog import AppCatalog, validate_all_apps
from .config.catalog_build import build_catalog
from .config.loader import load as load_settings
from .config.schema import Preset, RetrievalLlmRef, Settings
from .failover.llm import FallbackLlm, FallbackVlm
from .failover.observe import make_switch_logger
from .failover.registry import get_cooldown_registry
from .filestore.memory import MemoryFileStore
from .filestore.protocol import FileStore
from .filestore.specstar_impl import SpecstarFileStore
from .kb.chunker import Chunker, FixedTokenChunker
from .kb.embedder import Embedder, HashEmbedder, LitellmEmbedder
from .kb.llm import ILlm, LitellmLlm
from .kb.retriever import Enhancements
from .kb.vlm.protocol import IVlm
from .resources.kb import CODE_EMBED_DIM, EMBED_DIM
from .sandbox.local_process import LocalProcessSandbox
from .sandbox.mock import MockSandbox
from .sandbox.protocol import Sandbox

__all__ = [
    "Settings",
    "load_settings",
    "get_spec",
    "get_sandbox",
    "get_filestore",
    "get_runner",
    "get_agent_config_catalog",
    "get_app_catalog",
    "get_embedder",
    "get_code_embedder",
    "get_chunker",
    "get_doc_pipeline",
    "get_chat_pipeline",
    "get_kb_llm",
    "get_kb_quality_judge_llm",
    "get_kb_vlm",
    "get_designed_pptx_vlm",
    "get_kb_describer",
    "get_wiki_endpoint",
    "get_infer_modules_run_config",
    "InferModulesRunConfig",
    "get_check_registry",
    "build_message_queue_factory",
]


def build_message_queue_factory(settings: Settings):  # -> IMessageQueueFactory
    """The job-queue backend shared by the durable background queues — wiki
    maintenance (#58/#59) AND KB indexing (#82) — selected by
    ``settings.message_queue.kind``:

    - ``simple`` — jobs are specstar resources on the shared backend, so
      every pod consumes the same queue (multipod with zero extra infra).
    - ``rabbitmq`` — broker-backed, for higher throughput; all the
      production knobs (prefix / retry policy / heartbeat) are threaded
      through from ``settings.message_queue.rabbitmq``.

    Construction does NOT open a connection (the RabbitMQ factory only
    records the config), so this is safe to call at startup."""
    mq = settings.message_queue
    if mq.kind == "simple":
        from specstar.message_queue import SimpleMessageQueueFactory

        return SimpleMessageQueueFactory()
    if mq.kind == "rabbitmq":
        from specstar.message_queue import RabbitMQMessageQueueFactory

        rmq = mq.rabbitmq
        return RabbitMQMessageQueueFactory(
            amqp_url=rmq.url,
            queue_prefix=rmq.queue_prefix,
            max_retries=rmq.max_retries,
            retry_delay_seconds=rmq.retry_delay_seconds,
            amqp_heartbeat_seconds=rmq.heartbeat_seconds,
        )
    raise ValueError(f"unknown message_queue.kind: {mq.kind!r} (use simple | rabbitmq)")


def get_spec(
    settings: Settings,
    get_user_id: Callable[[], str] | None = None,
) -> SpecStar:
    """Production spec wiring: backend connections come from settings;
    `get_user_id` (if set) overrides `settings.server.default_user` so
    the spec stamps `created_by` with the same callable the API access
    layer checks against. Delegates registration to `make_spec` — there
    is no "construct + register" 2-step at this layer.

    When `filestore.disk_root` and `filestore.pg_dsn` are both empty
    (the dataclass default), no `backend=` is passed and specstar uses
    its in-memory default — keeps `Settings()` usable for tests +
    fast-dev iteration without forcing a disk path."""
    return make_spec(
        default_user=get_user_id or settings.server.default_user,
        backend=_backend_for(settings),
        superusers=frozenset(settings.server.superusers),
    )


def _backend_for(settings: Settings) -> BackendConfig | None:
    """Compose a specstar BackendConfig from settings, or ``None`` when
    neither connection field is set. Empty options would make specstar
    reject the connection (`disk backend requires options.rootdir`), so
    we skip the whole config block in that case."""
    fs = settings.filestore
    if not fs.disk_root and not fs.pg_dsn:
        return None
    connections: dict[str, ConnectionProfile] = {}
    if fs.disk_root:
        connections["local"] = ConnectionProfile(type="disk", options={"rootdir": fs.disk_root})
    if fs.pg_dsn:
        connections["pg"] = ConnectionProfile(
            type="postgres",
            options={"connection_string": _with_connect_timeout(fs.pg_dsn, fs.pg_connect_timeout)},
        )
    bind = "local" if "local" in connections else "pg"
    return BackendConfig(
        connections=connections,
        meta=BackendBinding(use=bind),
        resource=BackendBinding(use=bind),
        blob=BackendBinding(use=bind),
    )


def _with_connect_timeout(dsn: str, seconds: int) -> str:
    """Inject a libpq ``connect_timeout`` into a Postgres DSN so an unreachable
    server fails fast instead of hanging the boot silently (#208).

    Specstar's SQLAlchemy engine is built with no timeout, so the first real
    connection (``create_all`` at ``spec.apply``) blocks for minutes when the DB
    is down. We set the timeout at the only seam we own — the DSN string — which
    flows to every store (meta / resource / blob) built from this connection.

    No-ops when ``seconds <= 0`` (opt-out), when the DSN is empty, or when it
    already carries a ``connect_timeout`` (an explicit value wins). Handles both
    the URL form (``postgresql://…?k=v``) and the libpq key=value form
    (``host=… port=…``)."""
    if seconds <= 0 or not dsn:
        return dsn
    if "://" in dsn:
        parts = urlsplit(dsn)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if any(k.lower() == "connect_timeout" for k, _ in pairs):
            return dsn
        pairs.append(("connect_timeout", str(seconds)))
        return urlunsplit(parts._replace(query=urlencode(pairs)))
    # libpq key=value form: "host=… port=… dbname=…"
    keys = {tok.split("=", 1)[0].lower() for tok in dsn.split() if "=" in tok}
    if "connect_timeout" in keys:
        return dsn
    return f"{dsn} connect_timeout={seconds}".strip()


def get_sandbox(settings: Settings, tools_dir: Path | None = None) -> Sandbox:
    sb = settings.sandbox
    match sb.kind:
        case "mock":
            return MockSandbox()
        case "local":
            isolate = sb.isolate
            if settings.tools.mode == "uv-run":
                # uv-run runs tools from live source via `uv run`, which needs
                # the host env — the chroot jail has neither uv nor network.
                # Force isolation off; reject an explicit opt-in as a
                # contradiction (#63).
                if sb.isolate is True:
                    raise ValueError(
                        "tools.mode=uv-run requires a non-isolated sandbox (uv run "
                        "needs the host env), but sandbox.isolate is true — set "
                        "sandbox.isolate: false (or remove it)."
                    )
                isolate = False
            return LocalProcessSandbox(
                root_dir=Path(sb.root) if sb.root else None,
                exec_timeout=sb.exec_timeout,
                log_timeout=sb.log_timeout,
                isolate=isolate,
                tools_dir=tools_dir,
            )
        case "docker":
            from .sandbox.docker import DockerSandbox

            return DockerSandbox()
        case "http":
            if sb.http is None or not sb.http.base_url:
                raise ValueError(
                    "sandbox.kind=http requires sandbox.http.base_url (the "
                    "sandbox host's Service URL)"
                )
            from .sandbox.http_client import HttpSandbox

            return HttpSandbox(base_url=sb.http.base_url, read_timeout=sb.http.read_timeout)
        case other:
            raise ValueError(f"unknown sandbox.kind: {other!r}")


def get_filestore(settings: Settings, spec: SpecStar) -> FileStore:
    match settings.filestore.kind:
        case "memory":
            return MemoryFileStore()
        case "specstar":
            return SpecstarFileStore(spec)
        case other:
            raise ValueError(f"unknown filestore.kind: {other!r}")


def get_runner(settings: Settings) -> AgentRunner:
    """Production agent runner. Reads top-level `runner` / `llm`
    settings for the runner-wide defaults; per-preset endpoint overrides
    (set via `agents.presets.<name>.llm.*`) ride on each AgentConfig and
    win per turn (see `LitellmAgentRunner._build_agent`)."""
    # #196 busy-aware failover for the agent / sub-agent chat path: build the
    # per-config chains from every preset that declares `fallbacks`, keyed by the
    # primary endpoint (model, base_url) so a turn's config finds its chain
    # without touching the persisted AgentConfig.
    chains: dict[tuple[str, str | None], list[LlmEndpoint]] = {}
    for name in settings.agents.presets:
        chain = resolve_llm_chain(settings, RetrievalLlmRef(preset=name))
        if len(chain) >= 2:
            chains[(chain[0].model, chain[0].base_url)] = chain
    return LitellmAgentRunner(
        max_retries=settings.runner.max_retries,
        max_turns=settings.runner.max_turns,
        base_url=settings.llm.base_url or None,
        api_key=settings.llm.api_key or None,
        fallback_chains=chains or None,
        cooldown_registry=get_cooldown_registry() if chains else None,
    )


def get_agent_config_catalog(
    settings: Settings, config_dir: Path | None = None
) -> AgentConfigCatalog:
    """Build the deploy's AgentConfigCatalog from `settings.agents`.

    Resolves the KB-facing sub-agent lists (`kb_chat` / `infer_modules`) +
    the preset registry against `agents.presets` (Y semantics + Q5 merge
    rules). Threading: `__main__.py` calls this once at startup and passes the
    result into `create_app(agent_config_catalog=...)`."""
    return build_catalog(settings, config_dir=config_dir)


def get_app_catalog(settings: Settings) -> AppCatalog:
    """Build the deploy's `AppCatalog` from `agents.presets` (#89, decision 25).

    Validates every discovered App's function/tools coherence at startup
    (decision 11) so an incoherent `app.json` fails the boot loud. The picker /
    per-turn resolve are not yet wired to this in P3d (that cutover is P4); this
    makes the App layer constructible + validated."""
    validate_all_apps()
    return AppCatalog(presets=settings.agents.presets)


def get_embedder(settings: Settings) -> Embedder:
    e = settings.kb.embedder
    if e.model:
        return LitellmEmbedder(
            e.model,
            dim=EMBED_DIM,
            query_prefix=e.query_prefix,
            doc_prefix=e.doc_prefix,
            timeout=e.timeout,
            batch_size=e.batch_size,
            base_url=e.base_url or None,
            api_key=e.api_key or None,
            # #196 same-model replica failover + #249 transient-error retry over
            # the [primary, *replicas] chain (call_with_failover owns the retry).
            fallback_base_urls=list(e.fallbacks),
        )
    return HashEmbedder(dim=EMBED_DIM)


def get_code_embedder(settings: Settings) -> Embedder | None:
    """P3.0: the code-specialised embedder for `DocChunk.embedding_alt`.

    None when no code embedder is configured — code Collections still work
    (the retriever falls back to single-vector behaviour); only the
    semantic-code geometry is gone. Production wires this into
    ``create_app(kb_code_embedder=...)``."""
    ce = settings.kb.code_embedder
    if not ce.model:
        return None
    eb = settings.kb.embedder  # shares HTTP resilience knobs with default embedder
    return LitellmEmbedder(
        ce.model,
        dim=CODE_EMBED_DIM,
        query_prefix=ce.query_prefix,
        doc_prefix=ce.doc_prefix,
        timeout=eb.timeout,
        batch_size=eb.batch_size,
        base_url=ce.base_url or None,
        api_key=ce.api_key or None,
    )


def get_chunker(settings: Settings) -> Chunker:
    c = settings.kb.chunker
    return FixedTokenChunker(max_tokens=c.max_tokens, overlap_tokens=c.overlap)


def get_doc_pipeline(settings: Settings, embedder: Embedder) -> object:  # IngestionPipeline
    """P1 production path: LlamaIndex `IngestionPipeline` for doc ingest.
    Separate from `get_chunker` so the legacy chunker stays reachable for
    tests + offline runs; production wires this into `create_app(kb_pipeline=...)`."""
    from .kb.li_pipeline import build_doc_pipeline

    return build_doc_pipeline(embedder=embedder)


def get_chat_pipeline(
    settings: Settings, embedder: Embedder, llm: ILlm | None
) -> object | None:  # IngestionPipeline | None
    """P2 production path: chat-ingest pipeline. Returns None when no LLM is
    wired (offline / no KB chat model) — chat → knowledge can't run without an
    LLM. The Ingestor + close hook check this and degrade gracefully."""
    if llm is None:
        return None
    from .kb.li_pipeline import build_chat_pipeline

    return build_chat_pipeline(llm=llm, embedder=embedder)


def get_parser_registry(settings: Settings):  # -> ParserRegistry
    """Build the ``ParserRegistry`` the Ingestor uses to route uploads
    to parsers (issue #39).

    Registration order is custom-first, bundled-last. Operator
    declares custom parsers as dotted import paths under
    ``kb.parsers: ["my.pkg.MyCsvParser", ...]``; each path resolves
    to an ``IParser`` class which is constructed with zero arguments
    today (dependency injection lands when a bundled parser needs
    it). Custom-first means an in-house parser intentionally shadows
    a bundled one for the same extension.

    Bundled parsers (in fixed order — most-specific first): the
    LlamaIndex-Reader-backed ``PdfParser`` / ``HtmlParser`` /
    ``DocxParser``. The pre-#39 ``reader_for`` chain handled these
    same three formats; this registry replaces it.

    Errors raise at deploy startup (NOT at first upload) so
    operators see them immediately:

      - Unknown dotted path → ``ValueError`` with the path quoted.
      - Resolved class isn't an ``IParser`` subclass → ``TypeError``.
    """
    import importlib

    from .kb.parsers import IParser, ParserRegistry
    from .kb.parsers.chat_export_parser import ChatExportParser
    from .kb.parsers.json_file import JsonParser
    from .kb.parsers.llamaindex_readers import DocxParser, HtmlParser
    from .kb.parsers.pdf import PdfParser
    from .kb.parsers.slides import PptxParser
    from .kb.parsers.svg_image import SvgParser
    from .kb.parsers.tabular import CsvParser, ExcelParser
    from .kb.parsers.vlm_image import VlmImageParser

    registry = ParserRegistry()
    # Custom first — operator's declared order is preserved (multiple
    # in-house parsers stack without ordering surprises).
    for dotted in settings.kb.parsers:
        module_name, _, class_name = dotted.rpartition(".")
        if not module_name or not class_name:
            raise ValueError(
                f"kb.parsers entry {dotted!r} is not a dotted import path "
                f"(expected `pkg.module.ClassName`)"
            )
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise ValueError(f"kb.parsers entry {dotted!r} could not be imported: {exc}") from exc
        cls = getattr(module, class_name, None)
        if cls is None:
            raise ValueError(
                f"kb.parsers entry {dotted!r}: module {module_name!r} has no "
                f"attribute {class_name!r}"
            )
        if not (isinstance(cls, type) and issubclass(cls, IParser)):
            raise TypeError(
                f"kb.parsers entry {dotted!r}: resolved object {cls!r} is not an IParser subclass"
            )
        registry.register(cls())
    # Vision wiring: one shared VlmDescriber (or None when kb.vlm_llm
    # is disabled — VlmImageParser then never matches; PdfParser /
    # PptxParser degrade to text-layer-only pages).
    describer = get_kb_describer(settings)
    # Bundled parsers — fixed order, most-specific extensions first.
    # `kb.parsers_disabled` (class names) skips bundled entries: with
    # all-matching dispatch (Q8b) a custom parser runs ALONGSIDE a
    # bundled one rather than shadowing it, so replacing e.g. the PDF
    # path (Docling adaptation point) = register the custom parser in
    # `kb.parsers` + list "PdfParser" here.
    bundled: list[IParser] = [
        PdfParser(describer),
        HtmlParser(),
        DocxParser(),
        # ChatExportParser before JsonParser purely for readability —
        # all_matching runs every match; JsonParser itself declines
        # `.chat.json` so a conversation never shreds into key-path lines.
        ChatExportParser(get_kb_llm(settings)),
        JsonParser(),
        CsvParser(),
        ExcelParser(),
        VlmImageParser(describer),
        SvgParser(describer),  # #81: vector SVG → rasterize → same VLM describe path
        PptxParser(describer),
    ]
    disabled = set(settings.kb.parsers_disabled)
    unknown = disabled - {type(p).__name__ for p in bundled}
    if unknown:
        raise ValueError(
            f"kb.parsers_disabled names unknown bundled parser(s) {sorted(unknown)}; "
            f"bundled: {sorted(type(p).__name__ for p in bundled)}"
        )
    for parser in bundled:
        if type(parser).__name__ not in disabled:
            registry.register(parser)
    return registry


def _litellm_for(endpoint: LlmEndpoint) -> ILlm:
    """Build the inner production ILlm for one failover endpoint — the per-call
    `timeout` (so an abandoned producer dies) is the endpoint's idle ceiling, and
    `num_retries=0` because the failover loop owns retry-by-switching (#196)."""
    return LitellmLlm(
        endpoint.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        reasoning_effort=endpoint.reasoning_effort,
        timeout=endpoint.idle_s,
        num_retries=0,
    )


def _litellm_vlm_for(endpoint: LlmEndpoint) -> IVlm:
    """Vision-side mirror of `_litellm_for` (#131 / #196): the per-call `timeout` is
    the endpoint's idle ceiling and `num_retries=0` because the failover loop owns
    retry-by-switching. Module-level (not nested in `get_kb_vlm`) so it's unit-testable
    in isolation, exactly like `_litellm_for`."""
    from .kb.vlm import LitellmVlm

    return LitellmVlm(
        endpoint.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        timeout=endpoint.idle_s,
        num_retries=0,
    )


def _llm_from_chain(chain: list[LlmEndpoint]) -> ILlm | None:
    """An ILlm for a resolved chain: `[]` → None (role off); a single endpoint →
    a plain `LitellmLlm` (no failover machinery — there's nowhere to switch, so
    behaviour is byte-for-byte today's); ≥2 → a busy-aware `FallbackLlm`."""
    if not chain:
        return None
    if len(chain) == 1:
        e = chain[0]
        return LitellmLlm(
            e.model, base_url=e.base_url, api_key=e.api_key, reasoning_effort=e.reasoning_effort
        )
    return FallbackLlm(
        chain,
        get_cooldown_registry(),
        make_llm=_litellm_for,
        on_switch=make_switch_logger("kb-llm"),
    )


def get_kb_llm(settings: Settings) -> ILlm | None:
    """KB retrieval LLM (multi-query / HyDE / rerank).

    `kb.retrieval_llm` is a usage-entry reference to a named preset
    (like `workspace_chat[]` / `kb_chat[]` / `infer_modules[]`). The
    resolution cascade for each LLM-call field:

      ref.<field>  ◇  preset.<field>  ◇  settings.llm.<field>  ◇  None

    `None` retrieval_llm = enhancements disabled (factory returns
    None). When the preset declares `fallbacks`, the chain becomes a
    busy-aware `FallbackLlm` (#196). Operators put retrieval on a
    different provider from the agent chat by editing the referenced
    preset (or pointing the ref at a new one) — no parallel flat config
    block to keep in sync."""
    return _llm_from_chain(resolve_llm_chain(settings, settings.kb.retrieval_llm))


def get_card_drafter_llm(settings: Settings) -> ILlm | None:
    """The LLM that drafts context cards from documents (#175 自動 context card).

    `kb.card_drafter` is a usage-entry reference to a named preset (default
    `card-drafter`), resolved through the same cascade + failover chain as every
    other role. `None` ⇒ card drafting disabled (the generation feature stays
    mounted but the drafter proposes nothing)."""
    return _llm_from_chain(resolve_llm_chain(settings, settings.kb.card_drafter))


def get_kb_quality_judge_llm(settings: Settings) -> ILlm | None:
    """The LLM-as-judge that scores a document's quality as a knowledge source at
    index time (#105). `kb.quality_judge` is a usage-entry reference resolved
    through the same cascade + failover chain as every other role. `None` (the
    default) ⇒ quality scoring off (docs stay un-scored = neutral; search ranking
    unaffected)."""
    return _llm_from_chain(resolve_llm_chain(settings, settings.kb.quality_judge))


def get_sanity_judge_llm(settings: Settings) -> ILlm | None:
    """The LLM-as-judge for the Diagnostics sanity matrix (#231): scores each cell
    pass/fail (ai_grade/ai_note) and writes the per-model fitness verdict.

    `health.judge_llm` is a usage-entry reference resolved through the same cascade
    + failover chain as every other role; a preset with `fallbacks` becomes a
    busy-aware `FallbackLlm` (#196). `None` ⇒ AI scoring off (the ai columns stay
    empty, no verdict). Should be a capable model distinct from those under test."""
    return _llm_from_chain(resolve_llm_chain(settings, settings.health.judge_llm))


def get_kb_vlm_formatter(settings: Settings) -> ILlm | None:
    """Stage-2 formatter for vision parsers (issue #115): the text LLM that
    re-emits the VLM's output as clean Markdown so the chunker splits it on
    structure instead of truncating it.

    Resolution: `kb.vlm_format_llm` if set, else reuse `kb.retrieval_llm` (a
    small reformat job — sharing the retrieval model is fine), else `None`
    (stage 2 skipped; the raw VLM text is used as-is). Inherits the referenced
    preset's failover chain like every other role."""
    ref = settings.kb.vlm_format_llm or settings.kb.retrieval_llm
    return _llm_from_chain(resolve_llm_chain(settings, ref))


def _resolve_llm_ref(
    settings: Settings, ref: RetrievalLlmRef | None
) -> tuple[str | None, str | None, str | None]:
    """(model, base_url, api_key) for a `RetrievalLlmRef` preset
    reference — the one resolution cascade shared by every LLM-only
    usage site (`kb.retrieval_llm`, `kb.vlm_llm`, `kb.wiki.llm`):

      ref.<field>  ◇  preset.<field>  ◇  settings.llm.<field>  ◇  None

    `ref is None` (the off switch) → `(None, None, None)`."""
    if ref is None:
        return None, None, None
    preset = settings.agents.presets.get(ref.preset)
    assert preset is not None, f"preset {ref.preset!r} unknown — loader should have caught this"
    model = ref.model or preset.model
    base_url = ref.llm.base_url or preset.llm.base_url or settings.llm.base_url or None
    api_key = ref.llm.api_key or preset.llm.api_key or settings.llm.api_key or None
    return model, base_url, api_key


@dataclass(frozen=True)
class LlmEndpoint:
    """One resolved entry in a role's busy-aware failover chain (#196): a
    concrete model + endpoint creds + the per-entry TTFT / idle / cooldown
    budget (the preset's override, else the global `failover:` default)."""

    model: str
    base_url: str | None
    api_key: str | None
    reasoning_effort: str | None
    ttft_s: float
    idle_s: float
    cooldown_s: float

    @property
    def cooldown_key(self) -> tuple[str, str]:
        """Identity for the shared cooldown registry — the same model on the
        same endpoint is one physical deployment whichever role reached it."""
        return (self.model, self.base_url or "")


def _endpoint(
    settings: Settings,
    preset: Preset,
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    reasoning_effort: str | None,
) -> LlmEndpoint:
    fo = settings.failover
    return LlmEndpoint(
        model=model,
        base_url=base_url,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        ttft_s=preset.ttft_timeout_s if preset.ttft_timeout_s is not None else fo.ttft_timeout_s,
        idle_s=preset.idle_timeout_s if preset.idle_timeout_s is not None else fo.idle_timeout_s,
        cooldown_s=preset.cooldown_s if preset.cooldown_s is not None else fo.cooldown_s,
    )


def resolve_llm_chain(settings: Settings, ref: RetrievalLlmRef | None) -> list[LlmEndpoint]:
    """The ordered failover chain for an LLM-only role: the primary (the ref's
    preset cascade) followed by each name in `preset.fallbacks` resolved through
    the same cascade. The chain is NOT expanded recursively — a fallback's own
    `fallbacks` are ignored. The ref's `reasoning_effort` (a role-level
    preference) applies to every entry. `ref is None` (off) → `[]`."""
    if ref is None:
        return []
    preset = settings.agents.presets.get(ref.preset)
    assert preset is not None, f"preset {ref.preset!r} unknown — loader should have caught this"
    reasoning = ref.reasoning_effort or None
    model, base_url, api_key = _resolve_llm_ref(settings, ref)
    assert model is not None  # ref present ⇒ model resolves
    chain = [
        _endpoint(
            settings,
            preset,
            model=model,
            base_url=base_url,
            api_key=api_key,
            reasoning_effort=reasoning,
        )
    ]
    for name in preset.fallbacks:
        fb = settings.agents.presets.get(name)
        assert fb is not None, f"fallback preset {name!r} unknown — loader should have caught this"
        fb_base = fb.llm.base_url or settings.llm.base_url or None
        fb_key = fb.llm.api_key or settings.llm.api_key or None
        chain.append(
            _endpoint(
                settings,
                fb,
                model=fb.model,
                base_url=fb_base,
                api_key=fb_key,
                reasoning_effort=reasoning,
            )
        )
    return chain


def _sanity_endpoints(settings: Settings) -> dict[str, tuple[str | None, str | None]]:
    """model → (base_url, api_key) for every TEXT model the deploy references,
    for the model-sanity matrix: the KB chat fleet (``kb_chat[]``) plus
    ``kb.retrieval_llm`` (the kb_search expand/HyDE LLM — the reason this
    feature exists) and ``kb.wiki.llm``. ``kb.vlm_llm`` is excluded (vision,
    not text reasoning). Resolved through the same preset cascade as the rest
    of the factory; last write wins on a duplicate model."""
    out: dict[str, tuple[str | None, str | None]] = {}
    presets = settings.agents.presets
    for purpose in ("kb_chat",):
        for entry in settings.agents.sub_agents.get(purpose, []):
            preset = presets.get(str(entry.get("preset", "")))
            if preset is None:
                continue
            entry_llm = entry.get("llm") or {}
            model = str(entry.get("model", "")) or preset.model
            base_url = entry_llm.get("base_url", "") or preset.llm.base_url or settings.llm.base_url
            api_key = entry_llm.get("api_key", "") or preset.llm.api_key or settings.llm.api_key
            if model:
                out[model] = (base_url or None, api_key or None)
    for ref in (settings.kb.retrieval_llm, settings.kb.wiki.llm):
        model, base_url, api_key = _resolve_llm_ref(settings, ref)
        if model:
            out[model] = (base_url, api_key)
    return out


def get_sanity_models(settings: Settings) -> list[str]:
    """Sorted, deduped list of the models the sanity matrix probes (see
    ``_sanity_endpoints``) — the FE's model dropdown."""
    return sorted(_sanity_endpoints(settings))


def get_sanity_llm_factory(settings: Settings):  # -> Callable[[str, str], ILlm]
    """A ``(model, reasoning_level) -> ILlm`` factory for the sanity matrix. It
    returns the SAME ``LitellmLlm`` kb_search runs on — per model endpoint +
    ``reasoning_effort=level`` — so the battery exercises the real LLM seam, not
    a parallel runner. An unknown model falls back to litellm's provider env."""
    endpoints = _sanity_endpoints(settings)

    def make(model: str, level: str) -> ILlm:
        base_url, api_key = endpoints.get(model, (None, None))
        return LitellmLlm(model, base_url=base_url, api_key=api_key, reasoning_effort=level)

    return make


def get_kb_vlm(settings: Settings):  # -> IVlm | None
    """KB vision LLM for the vision-backed parsers (issue #39: image /
    PDF visual pages / slides). Same preset-reference resolution
    cascade as `get_kb_llm`:

      ref.<field>  ◇  preset.<field>  ◇  settings.llm.<field>  ◇  None

    `kb.vlm_llm: null` = VLM parsing disabled (factory returns None;
    image-only uploads store with zero chunks until an operator wires
    a VLM and reindexes). When the vlm preset declares `fallbacks`, the chain
    becomes a busy-aware `FallbackVlm` (#131 / #196)."""
    return _vlm_from_chain(resolve_llm_chain(settings, settings.kb.vlm_llm))


def _vlm_from_chain(chain: list[LlmEndpoint]):  # -> IVlm | None
    """An `IVlm` for a resolved chain — the vision-side mirror of
    `_llm_from_chain`: `[]` → None (role off); one endpoint → a plain
    `LitellmVlm`; ≥2 → a busy-aware `FallbackVlm`."""
    from .kb.vlm import LitellmVlm

    if not chain:
        return None
    if len(chain) == 1:
        e = chain[0]
        return LitellmVlm(e.model, base_url=e.base_url, api_key=e.api_key)
    return FallbackVlm(chain, make_vlm=_litellm_vlm_for, on_switch=make_switch_logger("vlm"))


def get_designed_pptx_vlm(settings: Settings):  # -> IVlm | None
    """The multimodal model that drives the `make_deck` build loop (#284) — it
    both *sees* rendered slides and *writes* the pptxgenjs fix, so it must be a
    multimodal (vision) model. Resolved like every other role: `kb.deck_vlm`
    when set, otherwise it reuses `kb.vlm_llm` (the read_image / ingest VLM) so a
    deploy that already wired a vision model gets `make_deck` for free. `None`
    (both unset) ⇒ the tool reports no model configured (fail-loud)."""
    ref = settings.kb.deck_vlm or settings.kb.vlm_llm
    return _vlm_from_chain(resolve_llm_chain(settings, ref))


def get_kb_describer(settings: Settings):  # -> VlmDescriber | None
    """The shared `VlmDescriber` over `kb.vlm_llm` (+ the `kb.vlm_format_llm`
    formatter), or None when no VLM is configured. Used both by the
    VLM-backed ingestion parsers and the interactive `read_image` agent tool
    (#112), so a deployment wires its vision model once."""
    from .kb.vlm import VlmDescriber

    vlm = get_kb_vlm(settings)
    if vlm is None:
        return None
    return VlmDescriber(vlm, formatter=get_kb_vlm_formatter(settings))


def get_wiki_endpoint(settings: Settings) -> tuple[str | None, str | None, str | None]:
    """(model, base_url, api_key) for the wiki agents (maintainer /
    reader / merge), resolved from `kb.wiki.llm` through the shared
    preset cascade. `(None, None, None)` when `kb.wiki.llm` is null —
    the off switch (#56): the wiki agents fall back to their in-code
    default model, and the health probes / disable logic treat a missing
    model as "wiki not configured"."""
    return _resolve_llm_ref(settings, settings.kb.wiki.llm)


def _agent_endpoint(settings: Settings, purpose: str) -> tuple[str | None, str | None, str | None]:
    """(model, base_url, api_key) for a sub-agent purpose's DEFAULT
    (first) usage entry — the same resolution cascade as get_kb_llm:
    entry.* ◇ preset.* ◇ top-level llm.*. (None, None, None) when the
    purpose has no entries (→ the check reports skip)."""
    entries = settings.agents.sub_agents.get(purpose, [])
    if not entries:
        return None, None, None
    entry = entries[0]
    preset = settings.agents.presets.get(str(entry.get("preset", "")))
    if preset is None:
        return None, None, None
    entry_llm = entry.get("llm") or {}
    model = str(entry.get("model", "")) or preset.model
    base_url = entry_llm.get("base_url", "") or preset.llm.base_url or settings.llm.base_url
    api_key = entry_llm.get("api_key", "") or preset.llm.api_key or settings.llm.api_key
    return model, base_url or None, api_key or None


@dataclass(frozen=True)
class InferModulesRunConfig:
    """Resolved per-step config for the `infer_modules` tool (#66): the KB
    query depth + reasoning effort each classification sub-agent runs with,
    and how many run concurrently. Read from the FIRST `agents.infer_modules`
    usage entry's optional `enhancements` / `reasoning_effort` / `parallelism`
    keys (defaults: operator KB defaults, model default, 8)."""

    enhancements: Enhancements | None
    reasoning_effort: str | None
    parallelism: int
    # The KB collection NAME the per-step classifier searches (#66). "" ⇒
    # search ALL collections (backward-compatible). The API resolves the name
    # to ids once per turn.
    collection: str


def get_infer_modules_run_config(settings: Settings) -> InferModulesRunConfig:
    """Resolve the infer_modules per-step run config from
    `agents.infer_modules[0]`. These are NOT the composer's live settings —
    infer_modules is a focused classification probe with its own config."""
    entries = settings.agents.sub_agents.get("infer_modules", [])
    entry = entries[0] if entries else {}
    enh = entry.get("enhancements")
    if isinstance(enh, dict):
        enhancements = Enhancements(
            expand=enh.get("expand"), hyde=enh.get("hyde"), rerank=enh.get("rerank")
        )
    else:
        # #66: default OFF (expand/hyde/rerank all 0/false). Classifying ONE
        # step name is a focused lookup that doesn't need multi-query / HyDE /
        # rerank, and the tool runs one classification PER unique step (~1500
        # seen), so any per-step enhancement explodes into thousands of extra
        # retrieval-LLM round-trips. Operators opt back in (clamped by the
        # retriever's max) via agents.infer_modules[].enhancements.
        enhancements = Enhancements(expand=0, hyde=0, rerank=False)
    reasoning = entry.get("reasoning_effort")
    par = entry.get("parallelism", 16)
    collection = entry.get("collection")
    return InferModulesRunConfig(
        enhancements=enhancements,
        reasoning_effort=reasoning if isinstance(reasoning, str) else None,
        parallelism=par if isinstance(par, int) and par > 0 else 16,
        collection=collection if isinstance(collection, str) else "",
    )


def get_check_registry(settings: Settings):  # -> health.CheckRegistry
    """The #51 sanity-check registry: the bundled capability
    probes (minus ``health.checks_disabled``) plus custom
    ``health.checks`` dotted-path classes. Same conventions as
    ``get_parser_registry``: unknown disabled ids and broken dotted
    paths raise at startup, not at first run."""
    from .health import CheckRegistry, ISanityCheck
    from .health.checks import (
        EmbedderDimCheck,
        InsightExtractionCheck,
        RetrievalExpandCheck,
        ToolCallCheck,
        VlmDescribeCheck,
    )

    kb_llm = get_kb_llm(settings)
    bundled: list[ISanityCheck] = [
        EmbedderDimCheck(
            get_embedder(settings),
            expected_dim=EMBED_DIM,
            check_id="embedder-default",
            description="Embed one sentence with the default model and verify the vector width",
        ),
        EmbedderDimCheck(
            get_code_embedder(settings),
            expected_dim=CODE_EMBED_DIM,
            check_id="embedder-code",
            description="Embed one sentence with the code model and verify the vector width",
        ),
        InsightExtractionCheck(kb_llm),
        RetrievalExpandCheck(kb_llm),
        VlmDescribeCheck(get_kb_vlm(settings)),
        ToolCallCheck(
            check_id="agent-kb-chat",
            description="KB chat agent model can call tools",
            **_tool_check_kwargs(settings, "kb_chat"),
        ),
        ToolCallCheck(
            check_id="agent-infer-modules",
            description="infer_modules sub-agent model can call tools",
            **_tool_check_kwargs(settings, "infer_modules"),
        ),
        ToolCallCheck(
            check_id="agent-wiki-reader",
            description="Wiki reader model navigates the wiki (calls search_wiki) instead of "
            "answering from memory",
            tool_name="search_wiki",
            tool_description="Search the collection's knowledge wiki pages for a term.",
            param_name="query",
            param_description="term to search the wiki for",
            prompt="Find what the wiki says about 'reflow'. Do not answer from memory — "
            "call search_wiki.",
            **_wiki_check_kwargs(settings),
        ),
        ToolCallCheck(
            check_id="agent-wiki-maintainer",
            description="Wiki maintainer model edits wiki pages (calls write_file) instead of "
            "narrating",
            tool_name="write_file",
            tool_description="Write content to a wiki page at the given path.",
            param_name="path",
            param_description="wiki page path to write",
            prompt="Record that 'reflow zone 3 setpoint is 245C' on the page "
            "/entities/reflow.md. Do not narrate — call write_file.",
            **_wiki_check_kwargs(settings),
        ),
    ]
    # #89: one tool-call probe per registered App's default agent — the model a
    # live workspace turn resolves through the AppCatalog. Replaces the removed
    # workspace_chat 'agent-workspace' check.
    from .apps.catalog import discover_app_slugs

    for slug in discover_app_slugs():
        bundled.append(
            ToolCallCheck(
                check_id=f"agent-{slug}",
                description=f"{slug} App agent model can call tools",
                **_app_agent_check_kwargs(settings, slug),
            )
        )
    disabled = set(settings.health.checks_disabled)
    unknown = disabled - {c.check_id for c in bundled}
    if unknown:
        raise ValueError(
            f"health.checks_disabled names unknown check(s) {sorted(unknown)}; "
            f"bundled: {sorted(c.check_id for c in bundled)}"
        )
    registry = CheckRegistry()
    for check in bundled:
        if check.check_id not in disabled:
            registry.register(check)
    for dotted in settings.health.checks:
        registry.register(_construct_dotted(dotted, ISanityCheck, config_key="health.checks"))
    return registry


def _tool_check_kwargs(settings: Settings, purpose: str) -> dict:
    model, base_url, api_key = _agent_endpoint(settings, purpose)
    return {"model": model, "base_url": base_url, "api_key": api_key}


def _app_agent_check_kwargs(settings: Settings, slug: str) -> dict:
    """Endpoint for an App's default-agent probe (#89). Resolves the App's
    `default_profile` through the AppCatalog — the same path a live turn takes
    — and capability-tests THAT model. `None` model ⇒ the check reports skip."""
    from .apps.manifest import load_app_manifest

    catalog = get_app_catalog(settings)
    cfg = catalog.resolve(
        app_slug=slug,
        profile=load_app_manifest(slug).default_profile,
        attached_preset=None,
    )
    if cfg is None:
        return {"model": None, "base_url": None, "api_key": None}
    return {
        "model": cfg.model,
        "base_url": cfg.llm_base_url or None,
        "api_key": cfg.llm_api_key or None,
    }


def _wiki_check_kwargs(settings: Settings) -> dict:
    """Endpoint for the wiki-agent probes (#50 P8 / #57). The wiki
    maintainer + reader run on the model named by ``kb.wiki.llm``, so the
    probe must capability-test THAT model — not the workspace agent's.
    (#57: the old `runner.wiki_*` lived in a namespace the resolver never
    read, so the probe silently tested the workspace model and could not
    detect a wiki model that narrates instead of calling tools.)
    ``kb.wiki.llm: null`` ⇒ model None ⇒ the check reports skip."""
    model, base_url, api_key = get_wiki_endpoint(settings)
    return {"model": model, "base_url": base_url, "api_key": api_key}


def get_replay_service(settings: Settings, kb_llm: ILlm | None):  # -> health.ReplayService
    """#51 P4 replay diagnostics. Reuses the already-built KB LLM (so
    chat-export replays hit the same endpoint extraction does) and
    builds its own describer over `kb.vlm_llm` (stateless — sharing the
    parser registry's instance buys nothing). Turn replays inherit the
    runner-wide endpoint defaults (`settings.llm`), with per-preset
    overrides riding on each AgentConfig exactly like live turns."""
    from .health.replay import ReplayService
    from .kb.vlm import VlmDescriber

    vlm = get_kb_vlm(settings)
    return ReplayService(
        kb_llm=kb_llm,
        describer=(
            VlmDescriber(vlm, formatter=get_kb_vlm_formatter(settings)) if vlm is not None else None
        ),
        default_base_url=settings.llm.base_url or None,
        default_api_key=settings.llm.api_key or None,
    )


def _construct_dotted(dotted: str, base_cls: type, *, config_key: str):
    """Resolve a `pkg.module.ClassName` config entry to a zero-arg
    instance of `base_cls`, raising at startup with the offending entry
    quoted (same contract as the kb.parsers resolver)."""
    import importlib

    module_name, _, class_name = dotted.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(
            f"{config_key} entry {dotted!r} is not a dotted import path "
            f"(expected `pkg.module.ClassName`)"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"{config_key} entry {dotted!r} could not be imported: {exc}") from exc
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(
            f"{config_key} entry {dotted!r}: module {module_name!r} has no attribute {class_name!r}"
        )
    if not (isinstance(cls, type) and issubclass(cls, base_cls)):
        raise TypeError(
            f"{config_key} entry {dotted!r}: resolved object {cls!r} is "
            f"not an {base_cls.__name__} subclass"
        )
    return cls()
