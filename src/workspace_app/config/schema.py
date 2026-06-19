"""Nested Settings tree + bundled defaults — the typed end of the loader.

The loader's job ends with a `Settings(...)` instance. Downstream code
reads typed attributes (`settings.kb.embedder.model`) instead of dict-key
navigation, so refactors / typos are caught by ty / IDE.

Shape mirrors the YAML schema (Q3 hybrid C nesting):

- top-level sections: `server` / `sandbox` / `filestore` / `runner` /
  `llm` / `read_file` / `history`  — each a small frozen dataclass.
- deep-nested area: `kb.{embedder, chunker, retrieval_llm, code_embedder,
  git}` — KB has 5 sibling subsystems worth structuring; the other
  sections are single knob bags.
- agents: `presets` (dict of `Preset`) + `workspace_chat` (FE picker list
  of override-dicts) + `kb_chat` (single override-dict).

Why `Preset` is typed but `workspace_chat[]` / `kb_chat` are dicts:
the usage entries carry an arbitrary subset of preset-shaped overrides
(Q5 merge rules) — typing every possible override as Optional would
duplicate every Preset field. The catalog builder (later slice) walks
the dict, merges with the named preset, and constructs the typed
`AgentConfig` callers see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─── server ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ServerSettings:
    default_user: str = "default-user"
    host: str = "127.0.0.1"
    port: int = 8000
    # External sub-path when behind a path-stripping proxy (e.g. "/my-svc/rca").
    # Only affects generated URLs (OpenAPI/docs); the SPA's own base path is a
    # build-time setting (VITE_BASE_PATH). Default "" = served at root.
    root_path: str = ""


# ─── sandbox ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SandboxSettings:
    kind: str = "local"  # local | docker | mock
    root: str | None = None  # null → tmpdir per sandbox
    # Two peer command timeouts (#70); 0 disables that one. `exec_timeout` is
    # the TOTAL wall-clock cap; `log_timeout` is the IDLE cap (no stdout/stderr
    # output for this long ⇒ assumed hung). A long job sets `exec_timeout: 0`
    # and relies on `log_timeout` to catch a hang.
    exec_timeout: float = 60.0
    log_timeout: float = 60.0
    isolate: bool | None = None  # None = auto-detect userns


# ─── filestore ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FilestoreSettings:
    kind: str = "memory"  # memory | specstar
    pg_dsn: str = ""
    disk_root: str = ""


# ─── runner ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunnerSettings:
    max_retries: int = 2
    max_turns: int = 10
    # Wiki agent knobs (model/endpoint + step budgets) used to live here as
    # flat `wiki_*` fields (#56) — they now follow the same preset-reference
    # pattern as the other LLMs under `kb.wiki` (`WikiSettings`).


# ─── llm (chat endpoint shared by RCA + KB chat) ────────────────────────
@dataclass(frozen=True)
class LlmSettings:
    base_url: str = ""
    api_key: str = ""


# ─── read_file caps ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class ReadFileSettings:
    max_lines: int = 2000
    max_chars: int = 200_000


# ─── exec / tool output cap (issue #44) ─────────────────────────────────
@dataclass(frozen=True)
class ExecSettings:
    # A tool command whose stdout+stderr exceeds this is truncated head+tail
    # with a notice, so one `grep` over a big file can't flood the context.
    output_max_chars: int = 30_000


# ─── cross-turn memory ──────────────────────────────────────────────────
@dataclass(frozen=True)
class HistorySettings:
    max_messages: int = 40
    # Token budget for the replayed history (issue #45). After the
    # message-count window, oldest items are dropped until the estimated
    # token total (≈chars/4) fits — so a handful of huge tool outputs
    # can't overflow the model's context even within `max_messages`.
    # `0` disables the token budget (count window only). Default sized
    # for the bundled local qwen3 (~32K ctx) leaving room for the system
    # prompt + the current turn + the reply.
    max_context_tokens: int = 24_000


# ─── kb subsystem ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class EmbedderSettings:
    model: str = "ollama/bge-m3"
    query_prefix: str = ""
    doc_prefix: str = ""
    timeout: float = 60.0
    num_retries: int = 2
    batch_size: int = 64
    base_url: str = ""
    api_key: str = ""


@dataclass(frozen=True)
class ChunkerSettings:
    max_tokens: int = 256
    overlap: int = 32


@dataclass(frozen=True)
class RetrievalLlmRef:
    """Usage-entry reference to a named preset for the KB retrieval
    LLM. Mirrors the workspace_chat[] / kb_chat[] / infer_modules[]
    pattern: `preset` names a recipe in `agents.presets`; `model` /
    `llm` are optional inline overrides that win over the named
    preset's values. The retriever consumes only model + endpoint
    creds, so prompt_file / allowed_tools / etc. are NOT exposed
    here — operators who want to override those edit the preset
    itself.

    `KbSettings.retrieval_llm: RetrievalLlmRef | None`. `None`
    disables retrieval enhancements (multi-query / HyDE / rerank)
    entirely — `factories.get_kb_llm` returns `None`.
    """

    preset: str
    model: str = ""  # empty → inherit from named preset
    llm: PresetLlmSettings = field(default_factory=lambda: PresetLlmSettings())
    # Reasoning effort for THIS LLM's calls. "" = unset (omit the param → model
    # default; qwen3 thinks). Consumed by the KB retrieval LLM (kb.retrieval_llm
    # → multi-query / HyDE / rerank): "none" maps to Ollama think=False so
    # kb_search doesn't <think> on every query expansion; low|medium|high keep
    # thinking on. (vlm_llm / wiki.llm carry the field but don't read it yet.)
    reasoning_effort: str = ""


@dataclass(frozen=True)
class EnhancementInt:
    """Int-valued enhancement dial (e.g. multi-query expand, HyDE doc count).

    `default` = the value used when no caller / LLM specifies one.
    `max` = hard ceiling that LLM-set tool args cannot exceed. Caller
    Python params are also clamped — operator's `max` is the final
    word. `0` disables that enhancement entirely.
    """

    default: int = 0
    max: int = 0


@dataclass(frozen=True)
class EnhancementBool:
    """Bool-valued enhancement switch (e.g. rerank). Same default/max
    pattern as `EnhancementInt` — `max=False` forces the enhancement
    off regardless of caller / LLM input."""

    default: bool = False
    max: bool = False


@dataclass(frozen=True)
class EnhancementSettings:
    """Per-knob enhancement defaults + ceilings the KB retriever reads.
    Bundled values are intentionally light (`expand=1` alt, no HyDE,
    rerank on) to keep latency reasonable; operators raise them when
    recall trumps latency."""

    expand: EnhancementInt = field(
        default_factory=lambda: EnhancementInt(default=1, max=3),
    )
    hyde: EnhancementInt = field(
        default_factory=lambda: EnhancementInt(default=0, max=1),
    )
    rerank: EnhancementBool = field(
        default_factory=lambda: EnhancementBool(default=True, max=True),
    )


@dataclass(frozen=True)
class RetrievalSettings:
    """Behavioural knobs for the KB retriever — kept separate from
    `RetrievalLlmRef` (which holds "which LLM"). Operators tune
    `enhancements` here to dial cost vs. recall."""

    enhancements: EnhancementSettings = field(default_factory=EnhancementSettings)


@dataclass(frozen=True)
class WikiSettings:
    """#56: wiki-agent settings, co-located and pattern-consistent.

    `llm` is a `RetrievalLlmRef` preset reference (same shape as
    `kb.retrieval_llm` / `kb.vlm_llm`) — the wiki maintainer / reader /
    merge agents own their prompts + tools in code; this names only
    which model + endpoint drives them. `llm: null` disables the wiki
    subsystem entirely (no maintenance on ingest, KB chat's wiki route
    becomes a no-op). The step budgets are far higher than a chat
    reply's: a maintenance pass reads + searches before writing several
    pages; the reader navigates + grounds."""

    llm: RetrievalLlmRef | None = field(
        default_factory=lambda: RetrievalLlmRef(preset="wiki-default"),
    )
    maintainer_max_turns: int = 40
    reader_max_turns: int = 24


@dataclass(frozen=True)
class CodeEmbedderSettings:
    model: str = ""  # "" disables code embedder
    query_prefix: str = ""
    doc_prefix: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass(frozen=True)
class GitSettings:
    default_token: str = ""
    sync_check_interval_sec: int = 300


@dataclass(frozen=True)
class KbSettings:
    embedder: EmbedderSettings = field(default_factory=EmbedderSettings)
    chunker: ChunkerSettings = field(default_factory=ChunkerSettings)
    # `None` = retrieval LLM disabled (multi-query / HyDE / rerank
    # silently off). Default = reference to the bundled `kb-retrieval`
    # preset, so a fresh deploy gets enhancements out of the box.
    retrieval_llm: RetrievalLlmRef | None = field(
        default_factory=lambda: RetrievalLlmRef(preset="kb-retrieval"),
    )
    # Retriever behaviour knobs (expand / hyde / rerank defaults + LLM
    # ceilings). Independent from `retrieval_llm` — that names which LLM
    # to call; this controls how many calls and how aggressively.
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    code_embedder: CodeEmbedderSettings = field(default_factory=CodeEmbedderSettings)
    git: GitSettings = field(default_factory=GitSettings)
    # Issue #39: the VLM the vision-backed parsers (image / PDF visual
    # pages / slides) call to turn pixels into searchable text. Same
    # usage-entry shape as `retrieval_llm`; default = the bundled
    # `kb-vlm` preset (local qwen2.5-vl via Ollama). `None` disables
    # the VLM parsers — image-only uploads then store with zero chunks
    # until an operator wires a VLM and reindexes.
    vlm_llm: RetrievalLlmRef | None = field(
        default_factory=lambda: RetrievalLlmRef(preset="kb-vlm"),
    )
    # Issue #115: the text LLM that re-formats the VLM's output into clean
    # Markdown (small VLMs read images well but often emit free text the
    # chunker then truncates). `None` (the default) = reuse `retrieval_llm`;
    # both off = stage 2 skipped (the raw VLM text is used as-is). Same
    # usage-entry shape as `retrieval_llm` / `vlm_llm`.
    vlm_format_llm: RetrievalLlmRef | None = None
    # Issue #56: wiki-agent LLM (preset ref) + step budgets. `wiki.llm:
    # null` disables the wiki subsystem.
    wiki: WikiSettings = field(default_factory=WikiSettings)
    # Issue #39: custom (in-house) parser classes the operator wants
    # the KB ingest to pick up. Each entry is a dotted import path to
    # an `IParser` subclass — e.g. `my.pkg.MyCsvParser`. The factory
    # registers them at the HEAD of the ParserRegistry (before the
    # bundled PDF/HTML/DOCX wrappers), so an in-house parser
    # intentionally shadows a bundled one for the same extension.
    # Construction is zero-arg today; dependency injection (ILlm /
    # settings) lands when a bundled parser needs it (e.g. VLM).
    parsers: list[str] = field(default_factory=list)
    # Issue #39: bundled parser CLASS NAMES the registry must skip —
    # with all-matching dispatch (Q8b) a custom parser doesn't shadow
    # a bundled one, it runs ALONGSIDE it, so replacement needs an
    # explicit off switch. The Docling adaptation point: register
    # `my.pkg.DoclingParser` in `parsers` and list "PdfParser" here.
    parsers_disabled: list[str] = field(default_factory=list)


# ─── agents — presets + usage references ────────────────────────────────
@dataclass(frozen=True)
class PresetLlmSettings:
    """Per-preset LLM endpoint override. When both fields empty, the
    preset falls back to the top-level `llm:` section (and ultimately
    to litellm's provider env / Ollama defaults)."""

    base_url: str = ""
    api_key: str = ""
    # #113 Layer 1 (anti-repetition sampling). All None = inherit the model
    # default. Honoured by vLLM; silently dropped by Ollama's Go runner — the
    # stream-side guard is the backend-independent backstop.
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None


@dataclass(frozen=True)
class Suggestion:
    """One quick-prompt chip on the agent panel.

    ``label`` is what the chip button renders. ``prompt`` is sent verbatim
    as the user message when the chip is pressed. Split so a chip can read
    as "SPC" while submitting the full "Show me the SPC analysis ..." (#91).

    Mirrors :class:`workspace_app.resources.agent_config.Suggestion` — this
    one is the loader-facing dataclass form, the resource one is the
    msgspec.Struct form the API surface exposes to the FE. Catalog build
    copies field-for-field.
    """

    label: str
    prompt: str


def _to_suggestion(v: object) -> Suggestion:
    """Normalise loader input into a ``Suggestion``.

    * ``"short"`` → ``Suggestion(label="short", prompt="short")`` (matches
      the old ``list[str]`` "display == send" semantics, so existing
      operator YAML keeps working).
    * ``{"label": "X", "prompt": "Y"}`` → ``Suggestion(label="X", prompt="Y")``.
    * ``Suggestion(...)`` → returned as-is.
    """
    if isinstance(v, Suggestion):
        return v
    if isinstance(v, str):
        return Suggestion(label=v, prompt=v)
    if isinstance(v, dict):
        return Suggestion(
            label=str(v["label"]),  # ty:ignore[invalid-argument-type]
            prompt=str(v["prompt"]),  # ty:ignore[invalid-argument-type]
        )
    raise TypeError(f"suggestion entry must be str | dict | Suggestion; got {type(v).__name__}")


@dataclass(frozen=True)
class Preset:
    """One named LLM-backend bundle (Y semantics — preset is a full
    AgentConfig template). Referenced by name from `workspace_chat[]`
    and `kb_chat`; usage entries override any subset of these fields.

    `prompt_file` is the unresolved value string (`pkg:...`, absolute,
    or relative); the catalog resolves it to the prompt body at build
    time via `resolve_prompt_file`.
    """

    model: str
    # Optional — agent-style callers (workspace_chat / kb_chat /
    # infer_modules) need a prompt; LLM-only callers (kb.retrieval_llm)
    # don't. Catalog build enforces non-empty for agent callers.
    prompt_file: str = ""
    # One-line picker blurb (the composer model picker renders it under
    # the entry name — handoff redesign). "" = no note shown.
    description: str = ""
    suggestions: list[Suggestion] = field(default_factory=list)
    # Tri-state, mirrors AgentConfig.allowed_tools (Q4-followup):
    # None = "not specified" → runner uses default workspace tools;
    # [] = explicit empty (no tools); [...] = exact.
    # Bundled RCA presets leave this absent (= None) so picking one
    # gives the standard agent; kb-default explicitly sets [kb_search].
    allowed_tools: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "workspace-app/sandbox:py312-ds"
    idle_timeout_seconds: int = 28800
    llm: PresetLlmSettings = field(default_factory=PresetLlmSettings)


# Bundled default presets — these populate `Settings().agents.presets`
# when no operator config.yaml is provided. They're plain dicts (not
# typed Preset instances) because the loader's merge step operates on
# dict trees; the schema layer reads them back into Preset instances
# in `_bundled_presets()` below.
_BUNDLED_PRESETS: dict[str, dict[str, Any]] = {
    "qwen3-local": {
        "model": "ollama_chat/qwen3:14b",
        "description": "Local model — private, no credentials needed. Solid default.",
        "suggestions": [
            "Show the SPC analysis",
            "Run a Pareto of defect modes",
            "Draft the report",
        ],
    },
    "claude-opus": {
        "model": "claude-opus-4-7",
        "description": "Deepest reasoning for tricky root-cause chains. Hosted; needs credentials.",
        "suggestions": [
            "Show the SPC analysis",
            "Run a Pareto of defect modes",
            "Draft the report",
        ],
    },
    "openai-mini": {
        "model": "openai/gpt-4o-mini",
        "description": "Fast hosted second opinion. Needs credentials.",
        "suggestions": [
            "Show the SPC analysis",
            "Run a Pareto of defect modes",
            "Draft the report",
        ],
    },
    "kb-default": {
        "model": "ollama_chat/qwen3:14b",
        "prompt_file": "pkg:workspace_app.kb.prompts/system.md",
        "description": "Local model — private, no credentials needed. Solid default.",
        "suggestions": [
            "What does the knowledge base say about this?",
            "Summarize what we know on this topic",
            "Find related past findings",
        ],
        # `lookup_glossary` (#106) is the deterministic context-card path beside
        # kb_search: an unknown TERM resolves instantly from the glossary, only
        # a QUESTION needing document facts falls through to the slow RAG search.
        "allowed_tools": ["kb_search", "lookup_glossary"],
    },
    # Bundled hosted KB-chat options — same KB system prompt + tool set
    # as `kb-default`, just a different model so the FE picker shows
    # real choices on first run (#32 follow-up). Operators wire
    # credentials via `agents.presets.kb-claude.llm.api_key`
    # (or the top-level `llm.api_key`); without creds the entry is
    # still visible (picker discoverability) but the run fails fast
    # with a Missing-Credentials error — same UX as the bundled hosted
    # `claude-opus` / `openai-mini` workspace_chat entries.
    "kb-claude": {
        "model": "claude-opus-4-7",
        "prompt_file": "pkg:workspace_app.kb.prompts/system.md",
        "description": "Strongest synthesis across many sources. Hosted; needs credentials.",
        "suggestions": [
            "What does the knowledge base say about this?",
            "Summarize what we know on this topic",
            "Find related past findings",
        ],
        "allowed_tools": ["kb_search", "lookup_glossary"],
    },
    "kb-openai": {
        "model": "openai/gpt-4o-mini",
        "prompt_file": "pkg:workspace_app.kb.prompts/system.md",
        "description": "Quick hosted answers. Needs credentials.",
        "suggestions": [
            "What does the knowledge base say about this?",
            "Summarize what we know on this topic",
            "Find related past findings",
        ],
        "allowed_tools": ["kb_search", "lookup_glossary"],
    },
    # `infer-modules-default` — the sub-agent the RCA agent's
    # `infer_modules` tool delegates to. KB-retrieval-flavoured (same
    # `kb_search`-only tool set as kb-default) but a different system
    # prompt: it classifies step_name strings into process modules
    # (STI / Gate / Contact / M1-M6 / Pad / Pass / Other), using KB for
    # fab-specific naming when the default taxonomy doesn't fit.
    "infer-modules-default": {
        "model": "ollama_chat/qwen3:14b",
        "prompt_file": "pkg:workspace_app.kb.prompts/infer_modules.md",
        "allowed_tools": ["kb_search"],
    },
    # `kb-retrieval` — the LLM-only preset referenced by
    # `kb.retrieval_llm` for multi-query / HyDE / rerank. Carries no
    # prompt or tools because the retriever consumes only the LLM
    # endpoint. Operators who want retrieval on a different provider
    # (e.g. hosted OpenAI while agents stay local) override just this
    # preset's `model` / `llm` in config.yaml.
    "kb-retrieval": {
        "model": "ollama_chat/qwen3:14b",
    },
    # `kb-vlm` — the LLM-only preset referenced by `kb.vlm_llm` for
    # the vision-backed parsers (issue #39: standalone images, PDF
    # visual pages, slides). qwen2.5-vl is the de-facto local VLM
    # pick (2026); operators on hosted vision models override just
    # this preset's `model` / `llm`.
    "kb-vlm": {
        "model": "ollama_chat/qwen2.5vl:7b",
    },
    # `wiki-default` — the LLM-only preset referenced by `kb.wiki.llm`
    # (#56). The wiki maintainer / reader / merge agents own their
    # prompts + tools in code; this supplies only which model + endpoint
    # drives them. NOTE: the bundled local qwen3:14b reliably narrates
    # instead of calling write_file (the #57 health probe flags this) —
    # operators point this preset at a stronger tool-calling model.
    "wiki-default": {
        "model": "ollama_chat/qwen3:14b",
    },
}


# Bundled kb_chat — ships so a fresh deploy has a working KB chat picker
# without any operator config. (The per-App workspace agent picker lives in
# each App's app.json, referencing the presets above by name — #89.)
_BUNDLED_KB_CHAT: list[dict[str, Any]] = [
    # Local-only entry first (the default — no creds needed).
    {"preset": "kb-default", "name": "KB · Qwen3 (local)"},
    # Hosted options so a fresh deploy already shows real choices.
    {"preset": "kb-claude", "name": "KB · Claude Opus"},
    {"preset": "kb-openai", "name": "KB · GPT-4o-mini"},
]

# Bundled infer_modules — single entry pointing at the default preset
# above. Mirrors kb_chat's shape (list of usage entries) so an operator
# can swap the model out in config.yaml via
# `agents.infer_modules: [{ "preset": "...", "model": "..." }]`.
_BUNDLED_INFER_MODULES: list[dict[str, Any]] = [{"preset": "infer-modules-default"}]


def _preset_from_dict(d: dict[str, Any]) -> Preset:
    """Build a typed `Preset` from a (merged) dict — drops unknown keys
    silently for now; the loader's strict-validation stage rejects
    those before we get here."""
    raw_at = d.get("allowed_tools")
    return Preset(
        model=d["model"],
        prompt_file=d.get("prompt_file", ""),
        description=d.get("description", ""),
        suggestions=[_to_suggestion(v) for v in d.get("suggestions", [])],
        # Preserve the tri-state: absent key → None (preset uses runner
        # defaults); explicit value → keep verbatim.
        allowed_tools=list(raw_at) if raw_at is not None else None,
        env=dict(d.get("env", {})),
        sandbox_image=d.get("sandbox_image", "workspace-app/sandbox:py312-ds"),
        idle_timeout_seconds=d.get("idle_timeout_seconds", 28800),
        llm=PresetLlmSettings(
            base_url=d.get("llm", {}).get("base_url", ""),
            api_key=d.get("llm", {}).get("api_key", ""),
        ),
    )


def _bundled_presets() -> dict[str, Preset]:
    return {name: _preset_from_dict(d) for name, d in _BUNDLED_PRESETS.items()}


def _bundled_kb_chat() -> list[dict[str, Any]]:
    import copy

    return copy.deepcopy(_BUNDLED_KB_CHAT)


def _bundled_infer_modules() -> list[dict[str, Any]]:
    import copy

    return copy.deepcopy(_BUNDLED_INFER_MODULES)


def _bundled_sub_agents() -> dict[str, list[dict[str, Any]]]:
    """B-flat default: every bundled purpose list packed into one dict
    keyed by purpose name. New sub-agent purposes ship by adding a key
    here; no `AgentsSettings` field needs to change."""
    return {
        "kb_chat": _bundled_kb_chat(),
        "infer_modules": _bundled_infer_modules(),
    }


@dataclass(frozen=True)
class AgentsSettings:
    """The `agents:` section — `presets` (typed recipes dict) +
    `sub_agents` (dynamic dict keyed by purpose name, holding the
    usage lists operators write at YAML level as flat `agents.<purpose>`
    keys). The loader packs the flat keys into `sub_agents` at build
    time. Named properties below give back-compat for existing call
    sites that read `agents.kb_chat` / `infer_modules` directly; new call
    sites should reach via `sub_agents[purpose]` or the catalog builder."""

    presets: dict[str, Preset] = field(default_factory=_bundled_presets)
    sub_agents: dict[str, list[dict[str, Any]]] = field(default_factory=_bundled_sub_agents)

    @property
    def kb_chat(self) -> list[dict[str, Any]]:
        return self.sub_agents.get("kb_chat", [])

    @property
    def infer_modules(self) -> list[dict[str, Any]]:
        return self.sub_agents.get("infer_modules", [])


# ─── health (#51 sanity checks) ────────────────────────────────────────
@dataclass(frozen=True)
class HealthSettings:
    """LLM sanity-check knobs (#51; see docs/plan-sanity-checks.md).

    - ``checks``: custom in-house check classes — dotted import paths
      to ``ISanityCheck`` subclasses (zero-arg constructed), appended
      after the bundled seven. Same pattern as ``kb.parsers``.
    - ``checks_disabled``: bundled check_ids to skip registering
      (unknown ids raise at startup — a typo must not silently leave
      a check running/missing)."""

    checks: list[str] = field(default_factory=list)
    checks_disabled: list[str] = field(default_factory=list)


# ─── message queue (#58/#59/#82: durable background job queues) ────────
@dataclass(frozen=True)
class RabbitmqSettings:
    """Broker-backed queue tuning. Defaults mirror specstar's own
    `RabbitMQMessageQueueFactory`, so leaving a knob unset is a no-op.

    - `queue_prefix` namespaces queue names when a broker is shared.
    - `max_retries` / `retry_delay_seconds` govern redelivery of a failed job.
    - `heartbeat_seconds` is the AMQP heartbeat — a slow KB index job
      (#82: seconds on the embedder) must not look idle and get reaped;
      raise it if jobs run longer than the broker's heartbeat window.
    """

    url: str = ""
    queue_prefix: str = "specstar:"
    max_retries: int = 3
    retry_delay_seconds: int = 10
    heartbeat_seconds: int = 600


@dataclass(frozen=True)
class MessageQueueSettings:
    """Backend for the durable background job queues — wiki maintenance
    (#58/#59) AND KB indexing (#82), which share one factory. `simple` =
    jobs are specstar resources on the shared backend, so every pod
    consumes the same queue (multipod with zero extra infra). `rabbitmq`
    swaps in the broker-backed factory for higher throughput."""

    kind: str = "simple"  # simple | rabbitmq
    rabbitmq: RabbitmqSettings = field(default_factory=RabbitmqSettings)


# ─── observability (config dump + LLM call log) ────────────────────────
@dataclass(frozen=True)
class LlmLogSettings:
    """The faithful LLM call log (one record per outbound litellm call).

    `enabled` ships TRUE — the operator wants it on by default; the env var
    `WORKSPACE_LLM_LOG=0` silences it without editing config (prod off-switch).
    `dir` is the log root (relative paths resolve from the run's cwd).
    `keep_days` is reserved for retention: `0` keeps everything (manual
    `rm -rf logs/llm/<date>`)."""

    enabled: bool = True
    dir: str = "logs/llm"
    keep_days: int = 0


@dataclass(frozen=True)
class ObservabilitySettings:
    llm_log: LlmLogSettings = field(default_factory=LlmLogSettings)


@dataclass(frozen=True)
class ToolsSettings:
    """How RCA tool packages are provisioned into the sandbox (#63).

    `prebuilt` (default) — the heavy self-contained bundles built by
    `scripts/prebuild_tools.py` (own portable python + venv), dropped
    read-only into the sandbox at provision time.

    `uv-run` — a lightweight DEBUG mode: each package runs straight from
    its live source via `uv run`, so editing a tool's source takes effect
    on the next call with no rebuild and without copying a python/venv.
    It needs `uv` on the host and a NON-isolated sandbox (the jail has no
    uv/network), so the factory forces `sandbox.isolate` off in this mode
    and rejects an explicit `sandbox.isolate: true`."""

    mode: str = "prebuilt"  # "prebuilt" | "uv-run"


# ─── top-level Settings ────────────────────────────────────────────────
@dataclass(frozen=True)
class Settings:
    """All deployment knobs, structured. `Settings()` (no-arg) gives
    the bundled defaults — what an operator gets with an empty (or
    absent) config.yaml. The loader builds a `Settings(...)` from
    merged dicts; downstream code reads typed attributes."""

    server: ServerSettings = field(default_factory=ServerSettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    tools: ToolsSettings = field(default_factory=ToolsSettings)
    filestore: FilestoreSettings = field(default_factory=FilestoreSettings)
    runner: RunnerSettings = field(default_factory=RunnerSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    read_file: ReadFileSettings = field(default_factory=ReadFileSettings)
    exec: ExecSettings = field(default_factory=ExecSettings)
    history: HistorySettings = field(default_factory=HistorySettings)
    kb: KbSettings = field(default_factory=KbSettings)
    agents: AgentsSettings = field(default_factory=AgentsSettings)
    health: HealthSettings = field(default_factory=HealthSettings)
    message_queue: MessageQueueSettings = field(default_factory=MessageQueueSettings)
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
