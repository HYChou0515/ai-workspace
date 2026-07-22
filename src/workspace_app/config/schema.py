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
    # #262: user ids with UNRESTRICTED access — they bypass collection access
    # control (read every collection, manage any). Threaded into the spec's
    # access_scope + write checker (`make_spec(superusers=…)`) AND the route-level
    # `authorize(...)` guards. Empty (default) ⇒ no superusers, as in prod today.
    superusers: list[str] = field(default_factory=list)
    # #312: whether this API process also drains the job queues in-process.
    # True (default) = all-in-one (local dev / single-pod). A pod-split deploy
    # sets it False on the API Deployment so the API is a pure producer and
    # dedicated worker pods (`python -m workspace_app.worker <jobtype>`) consume
    # each JobType under their own HPA.
    run_consumers: bool = True
    # #349: how often an in-flight turn polls the shared cross-pod cancel epoch.
    # Under degraded sticky routing (a Stop / new message landing on a peer pod)
    # the cancel latency equals this interval; the same-pod fast-path is instant.
    # Smaller = snappier cancel but more store reads per active turn.
    turn_cancel_poll_seconds: float = 0.5
    # #43 reconnect replay: how many recent broadcast events each per-item session
    # keeps in an in-pod ring so a same-pod reconnect can replay the gap (`?since=`).
    # 0 disables replay (a reconnect degrades to store re-hydrate). ~200KB/session
    # at the default; memory only while a session is live.
    turn_replay_buffer_events: int = 2000
    # #429 P7: how often the schedule-trigger sweeper polls profiles' triggers.json for
    # due headless runs. 0 (default) ⇒ off — time-triggered workflows are opt-in per deploy.
    # A CAS lease per (trigger, window) means only one pod fires each window when several run.
    trigger_check_interval_sec: int = 0


# ─── sandbox ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class HttpSandboxSettings:
    """`sandbox.kind: http` — the client half. `base_url` is the host's
    ClusterIP Service (only `create` hits it; later calls go straight to the
    pod the handle encodes). `read_timeout` 0 ⇒ no HTTP read deadline, so a
    long command is bounded by the host's exec/idle timeout, not the wire."""

    base_url: str = ""
    read_timeout: float = 0.0
    # #492: the host owns durable — it restores a sandbox's working dir from the
    # NFS archive on create and rsyncs it back on persist (host-local, so the
    # bulk copy never crosses this app↔host wire and can't hang). Set this ⇒ the
    # app skips its own per-file restore/mirror and writes back via the host's
    # /persist instead. Requires SANDBOX_HOST_NFS_ROOT set on the host, and the
    # app's `filestore.kind: nfs_tree` pointing at the SAME NFS tree so cold
    # reads and uploads agree with what the host rsyncs. False ⇒ the app-side
    # SandboxSync mirror, unchanged.
    host_managed_durable: bool = False
    # #492: how the idempotent file/probe ops retry a BUSY host (a read timeout ⇒
    # reachable but slow). Each retry gets a LONGER read deadline (a busy host
    # needs room, not another short hammer) and a longer backoff, both capped so a
    # stuck host still fails loud in bounded time rather than hanging (the original
    # #492 symptom). A connection failure / 404 (gone/reaped) is never retried
    # here — it rebuilds. Defaults are sane; tune only if the host runs hot.
    io_attempts: int = 4
    io_timeout_base_s: float = 10.0
    io_timeout_cap_s: float = 40.0
    io_backoff_base_s: float = 1.0
    io_backoff_cap_s: float = 8.0


@dataclass(frozen=True)
class SandboxIsolationSettings:
    """#345: per-item OS-user + cgroup-v2 isolation for the LOCAL sandbox when it
    runs on a SHARED working volume (multi-replica API, one fixed dir per item).
    Unlike the userns ``isolate`` jail (which needs unprivileged user namespaces
    — unavailable in our pods), this is the sandbox-host model: each item's exec
    runs as its OWN Linux uid under its OWN cgroup v2 slice, so items can't read,
    signal, or starve one another even though their dirs are siblings on one vol.

    The uid is ``uid_base + xxhash(item_id) % uid_range`` — a pure function of the
    item id, so every pod derives the SAME uid (file ownership on the shared vol
    stays consistent) with NO cross-pod coordination; the wide range keeps
    collisions negligible. ``enabled`` None = auto (on iff the pod has CAP_SETUID
    and a writable delegated cgroup v2 subtree); explicit False forces it off
    (dev / tests / hosts without the caps). Needs ``CAP_SETUID``/``CAP_SETGID`` +
    a delegated cgroup root on the API pod (see kubernetes/base/deployment.yaml).
    """

    enabled: bool | None = None
    uid_base: int = 1_000_000
    uid_range: int = 2_000_000_000
    cgroup_root: str | None = None  # delegated cgroup v2 subtree; None = auto-detect
    memory_max: str = "512M"
    cpu_cores: float = 1.0
    pids_max: int = 512


@dataclass(frozen=True)
class SandboxDurableSettings:
    """#501: the SANDBOX's durable workspace store — kept DISTINCT from the API's
    general `filestore` (the specstar blob store KB/wiki + WorkspaceFile registration
    + blob GC share). #492's `nfs_tree` is a sandbox-scoped persistence mechanism, so
    it lives here, NOT on the global `filestore.kind` (which stays specstar).

    - ``kind: ""`` (default) — sandbox persistence FOLLOWS the API filestore
      (`filestore.kind`); zero behaviour change for existing deploys.
    - ``kind: nfs_tree`` — the durable workspace store is a plain on-disk tree under
      ``nfs_root`` (a ReadWriteMany NFS mount) so the sandbox host can rsync a
      sandbox's working dir straight to/from it. Pairs with
      ``sandbox.http.host_managed_durable: true`` on the SAME tree.
    - ``migrate_from: specstar`` — wraps the tree in the M2 dual-read migration layer
      (read NFS, fall back to the API specstar filestore + lazy backfill) for a
      zero-downtime cut-over; "" ⇒ bare tree (post-migration).
    """

    kind: str = ""  # "" (follow filestore.kind) | nfs_tree
    nfs_root: str = ""
    migrate_from: str = ""  # "" | specstar


@dataclass(frozen=True)
class SandboxSettings:
    kind: str = "local"  # local | docker | mock | http
    root: str | None = None  # null → tmpdir per sandbox
    # Two peer command timeouts (#70); 0 disables that one. `exec_timeout` is
    # the TOTAL wall-clock cap; `log_timeout` is the IDLE cap (no stdout/stderr
    # output for this long ⇒ assumed hung). A long job sets `exec_timeout: 0`
    # and relies on `log_timeout` to catch a hang.
    exec_timeout: float = 60.0
    log_timeout: float = 60.0
    isolate: bool | None = None  # None = auto-detect userns
    # #345: per-item OS-user + cgroup isolation for the shared-vol local sandbox.
    isolation: SandboxIsolationSettings = field(default_factory=SandboxIsolationSettings)
    # #501: the sandbox's durable workspace store (nfs_tree lives HERE, scoped to the
    # sandbox — NOT on the global filestore.kind). Default "" ⇒ follow filestore.kind.
    durable: SandboxDurableSettings = field(default_factory=SandboxDurableSettings)
    http: HttpSandboxSettings | None = None  # only when kind == "http"


@dataclass(frozen=True)
class SandboxHostSettings:
    """LEGACY (#251): the sandbox host is now a standalone service (`sandbox-host/`)
    configured by `SANDBOX_HOST_*` env vars, NOT this file. The app no longer
    reads these fields; they are retained only so configs that still carry a
    `sandbox_host:` section keep validating (the loader rejects unknown keys).
    See `docs/sandbox-host.md`. Safe to delete from your config."""

    bind: str = "0.0.0.0:8000"
    uid_min: int = 100000
    uid_max: int = 199999
    memory_max: str = "512M"
    cpu_cores: float = 1.0
    pids_max: int = 512
    cgroup_root: str | None = None
    root: str | None = None
    exec_timeout: float = 60.0
    log_timeout: float = 60.0
    tools_dir: str | None = None
    idle_ttl: float = 1800.0


# ─── filestore ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FilestoreSettings:
    # The API's general durable store (the specstar blob store KB/wiki + the
    # WorkspaceFile model + blob GC all share). #501: the SANDBOX's durable store
    # (incl. #492's nfs_tree) is configured SEPARATELY under `sandbox.durable`, so
    # selecting an NFS workspace tree never swaps out this specstar filestore.
    kind: str = "memory"  # memory | specstar
    pg_dsn: str = ""
    disk_root: str = ""
    # #208: libpq connect timeout (seconds) injected into pg_dsn so an
    # unreachable Postgres fails fast with a clear error instead of hanging the
    # boot silently for minutes at the first connection (specstar's engine sets
    # no timeout). 0 ⇒ disabled (libpq waits indefinitely — the old behaviour).
    # Override by putting connect_timeout=… straight in pg_dsn (that wins).
    pg_connect_timeout: int = 10
    # #219: single-file upload cap in bytes. Streaming keeps RAM flat regardless
    # of size, so this guards disk + sandbox-wake cost, not memory — hence a
    # generous default (~2 GB). 0 ⇒ no cap. Per-workspace total quota is #245.
    max_file_size: int = 2 * 1024 * 1024 * 1024
    # #245: per-workspace total-size quota in bytes — the sum of a workspace's
    # files may not exceed this, so one workspace can't fill the disk root the
    # whole deploy shares. Enforced at the user-facing upload/edit endpoints
    # (the sandbox mirror is intentionally not gated — never lose agent work).
    # Default ~20 GiB (generous; normal use never hits it). 0 ⇒ no quota.
    workspace_quota: int = 20 * 1024 * 1024 * 1024
    # #245: blob-GC sweeper — reclaims orphaned blobs (deleted files' content) so
    # the quota stays honest. `gc_interval_sec` is how often a sweep runs (a CAS
    # lease means only one pod runs the full reconcile per window); 0 ⇒ sweeper
    # off. `gc_t1` protects freshly-written blobs from quarantine; `gc_t2` is the
    # reversible dwell in quarantine before a blob is permanently deleted.
    gc_interval_sec: float = 3600.0
    gc_t1: str = "1h"
    gc_t2: str = "24h"


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
    # It also bounds the LISTING tools (list_files / list_sources) and the
    # content a rejected write/edit echoes back — same reason, same budget.
    output_max_chars: int = 30_000
    # The ABSOLUTE ceiling on any ONE tool result, applied to every tool by
    # `agent/output_cap.py` instead of relying on each tool to cap itself.
    # A backstop, so it sits at the widest legitimate single answer (a full
    # `read_file`); the per-tool caps above are deliberately tighter. Lower it
    # for a small-context model — nothing a tool returns can then exceed it.
    tool_output_max_chars: int = 200_000


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
    batch_size: int = 64
    base_url: str = ""
    api_key: str = ""
    # #196 busy-aware failover for embeddings — a list of REPLICA endpoint URLs
    # for the SAME model (the embedder can only fall over to another endpoint
    # running the identical model; a different embedding model would produce
    # vectors in an incompatible space and corrupt the index). When non-empty,
    # the primary `base_url` plus these replicas form the priority chain; on a
    # transient failure the embedder retries with backoff then switches to the
    # next (#249). `api_key` is shared across replicas (same service).
    fallbacks: list[str] = field(default_factory=list)


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
    # #105: the document-quality prior. `quality_weight` (w) is its strength —
    # SMALL by default (Vertex AI Search recommends "0.1 or less" for a boost)
    # so a real relevance gap always wins; 0 disables the prior. `quality_floor`
    # is an OPTIONAL absolute hard cutoff (docs scored below it are dropped from
    # results); null (default) = soft only, never exclude.
    quality_weight: float = 0.10
    quality_floor: int | None = None
    # The BM25 corpus ceiling. The trigram pre-narrowing collapses a DISTINCTIVE
    # query to almost nothing, but a query of common domain vocabulary matches
    # nearly every chunk and narrows nothing — so this bounds how many of those the
    # store ships back (the most trigram-similar ones), capping the sparse arm's
    # cost on exactly the queries that hurt. BM25 still does the ranking, and the
    # dense arm ignores this cap (it still searches every chunk via the vector
    # index), so a capped-out chunk can still be retrieved. null (default) =
    # uncapped. Tune against a #535 eval run before lowering it.
    sparse_corpus_cap: int | None = None


@dataclass(frozen=True)
class ClusterSettings:
    """#506: thresholds for the card-gen reconcile + the background cluster sweeper
    (dedup / suppress duplicate proposals + questions). All τ are cosine SIMILARITY
    in [0, 1]; HIGHER = stricter (fewer merges / suppressions). Exact norm_key overlap
    is deterministic and ignores these. Conservative defaults (bias toward asking /
    keeping over wrongly dropping); an operator lowers them to dedup more aggressively.
    `sweep_interval_seconds` paces the API-side backfill+merge sweeper."""

    # Join a new candidate to an existing cluster when a member is within this sim.
    cluster_tau: float = 0.9
    # A candidate at/above this sim to a card (or a wiki grep hit) is auto-suppressed.
    suppress_tau: float = 0.92
    # At/above this (but below suppress) → suggest updating the near card instead.
    update_tau: float = 0.8
    # The background sweeper folds two clusters whose centroids are within this sim.
    merge_tau: float = 0.95
    # How often the API sweeper backfills un-projected candidates + folds race-splits.
    sweep_interval_seconds: float = 900.0


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
    # Issue #479: server-local "HH:MM" wall-clock time the daily wiki *reflection*
    # (consolidation) fires for every PROSE wiki collection — same shape as
    # kb.git.daily_sync. Reuses the wiki `llm` above. "" / None ⇒ the daily reflect
    # is OFF (manual POST /wiki/reflect only). A code collection is never reflected.
    reflect_daily: str | None = "04:00"


@dataclass(frozen=True)
class CodeEmbedderSettings:
    model: str = ""  # "" disables code embedder
    query_prefix: str = ""
    doc_prefix: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass(frozen=True)
class DisclosureSettings:
    """#605: the permission-disclosure switch — "there IS an answer, but you
    can't read it" (withheld sources + the request-access channel).

    ``enabled`` (default True): every KB turn runs the scores-only disclosure
    probe over the user's discoverable collections. ``false`` is the operator
    kill switch — the probe is skipped entirely (one fewer ANN query per
    kb_search; faster) and no turn produces withheld sources. Per-chat UI
    toggles narrow this further; they can never turn disclosure ON when the
    operator switched it off here."""

    enabled: bool = True


@dataclass(frozen=True)
class ImageEmbedderSettings:
    """#519: which image embedder populates/queries ``DocChunk.embedding_img``.

    ``kind``:
    - ``none`` (default) — no image embedder; retrieval stays text-only,
      byte-for-byte the pre-#513 path. Existing deploys are unchanged.
    - ``perceptual`` — the dependency-light placeholder that clusters
      visually-similar images, so query-by-image works out of the box before
      the real model lands. Image-only (no text→image), so text retrieval is
      still byte-for-byte unchanged.
    - ``hash`` — the byte-hash stub (exact-match only); for tests.

    The real model (external team) will be a fourth kind (``http`` — a remote
    service) wired here without touching the core, mirroring ``sandbox kind:
    http``. Width always resolves to ``IMG_EMBED_DIM``."""

    kind: str = "none"


@dataclass(frozen=True)
class ImageFetchSettings:
    """#513 P6: fetch an HTML/MD upload's externally-linked images (``<img src>``
    / ``![](url)``) from an internal image server so each becomes its own
    first-class image SourceDoc (VLM-describable, P4-image-vector-able).

    ``allowed_hosts`` is the SSRF allowlist — ONLY these hosts are ever fetched.
    Empty (the default) ⇒ the fan-out is OFF: no image is ever fetched and ingest
    behaves exactly as before (referenced images stay inert links). Internal-
    network default: a straight GET, no auth."""

    allowed_hosts: list[str] = field(default_factory=list)
    timeout: float = 10.0


@dataclass(frozen=True)
class GitSettings:
    default_token: str = ""
    sync_check_interval_sec: int = 300
    # Issue #355: wall-clock daily auto-sync time for code collections, "HH:MM"
    # in the server's local timezone. Every code collection (one with a git_url)
    # is re-synced once a day at this time — there is no per-collection override.
    # The sweeper wakes every ``sync_check_interval_sec`` and fires the first tick
    # past this time each day. ``""`` / ``None`` ⇒ daily auto-sync OFF (manual
    # POST /sync only). Replaces the old per-collection ``sync_interval_hours``
    # interval (now a dormant field).
    daily_sync: str | None = "03:00"


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
    # #175: the LLM that drafts context cards from documents (自動 context card).
    # Same usage-entry shape as `retrieval_llm`; default = the bundled
    # `card-drafter` preset. `None` ⇒ card drafting disabled (the feature stays
    # mounted but proposes nothing).
    card_drafter: RetrievalLlmRef | None = field(
        default_factory=lambda: RetrievalLlmRef(preset="card-drafter"),
    )
    # Retriever behaviour knobs (expand / hyde / rerank defaults + LLM
    # ceilings). Independent from `retrieval_llm` — that names which LLM
    # to call; this controls how many calls and how aggressively.
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    # #605: permission disclosure ("answer exists, no permission") on/off.
    disclosure: DisclosureSettings = field(default_factory=DisclosureSettings)
    # #506: reconcile / cluster-sweeper thresholds (dedup duplicate proposals +
    # questions across runs). See ClusterSettings.
    cluster: ClusterSettings = field(default_factory=ClusterSettings)
    # Issue #195: per-turn cap on how many times the KB agent may call
    # `kb_search` in one reply (the KB chat turn + the ask_knowledge_base
    # bridge). Each kb_search runs the expensive multi-query / HyDE / rerank
    # cascade, and small models often re-search the same thing instead of
    # answering — this bounds that, keeping replies fast and focused. The tool
    # reports the remaining budget on every result and, once exhausted, tells
    # the model to answer from what it already retrieved. `null` ⇒ no cap (also
    # the behaviour for other surfaces like Topic Hub, which never set it).
    max_searches_per_turn: int | None = 3
    # Issue #334: the upper bound a per-message FE picker may request for that
    # one reply's kb_search budget. `max_searches_per_turn` is the default used
    # when the composer sends nothing; a concrete pick is clamped to [0, this]
    # (0 = "don't search this reply"). Independent of the default so an operator
    # can keep a low default while still allowing the user to dial searches up.
    max_searches_ceiling: int = 10
    code_embedder: CodeEmbedderSettings = field(default_factory=CodeEmbedderSettings)
    # #513 P6: fetch an HTML/MD upload's externally-linked images off an internal
    # image server (SSRF-allowlisted) into their own image SourceDocs. Empty
    # allowlist (default) ⇒ off — no fetch, ingest unchanged.
    image_fetch: ImageFetchSettings = field(default_factory=ImageFetchSettings)
    # #519: image-embedding backend for DocChunk.embedding_img (query-by-image).
    image_embedder: ImageEmbedderSettings = field(default_factory=ImageEmbedderSettings)
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
    # Issue #284: the multimodal model that drives the `make_deck` build loop —
    # it both *sees* rendered slides and *writes* the pptxgenjs fix. Same
    # usage-entry shape as `vlm_llm`; MUST be multimodal (it reads slide images).
    # `None` (the default) ⇒ reuse `vlm_llm` (the read_image / ingest VLM); both
    # off ⇒ `make_deck` reports no model configured (fail-loud, like read_image).
    deck_vlm: RetrievalLlmRef | None = None
    # Issue #105: the LLM-as-judge that scores a document's quality as a knowledge
    # source at index time (a chunk-based windowed map-reduce against the
    # collection's `quality_rubric`). Same usage-entry shape + preset cascade as
    # `retrieval_llm`. `None` (the default) ⇒ reuse `retrieval_llm` (so scoring
    # turns on the moment a collection sets a rubric — the rubric is the opt-in,
    # like `vlm_format_llm` reusing retrieval); both unset ⇒ scoring off (docs stay
    # un-scored = neutral; search ranking unaffected). A judge failure leaves a doc
    # un-scored, never un-indexed.
    quality_judge: RetrievalLlmRef | None = None
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
    # Whether `model` NATIVELY sees images. When True, a workspace turn feeds
    # attached image bytes straight into the main model's message (and the
    # `read_image` tool hands it the raw image) instead of routing every image
    # through the separate `kb.vlm_llm` describer — no main→VLM→main round-trip,
    # no lossy image→text step. Purely declarative: local Ollama VLM ids (e.g.
    # `ollama_chat/qwen2.5vl`) aren't in litellm's capability DB, so we don't
    # auto-detect. Default False keeps text-only models on the describer path.
    vision: bool = False
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
    # #196 busy-aware failover: ordered names of OTHER presets to fall over to
    # when this model is too busy (a fast error OR no first token in time). The
    # chain is literally `[this preset, *fallbacks]` — a fallback's own
    # `fallbacks` are NOT expanded (no recursion). Any role that references this
    # preset inherits the chain unchanged; roles don't override it.
    fallbacks: list[str] = field(default_factory=list)
    # Per-preset overrides of the global `failover:` budgets (None = inherit the
    # global default). TTFT is model-dependent — a slow hosted model legitimately
    # needs longer before "no first token yet" should be read as "busy".
    ttft_timeout_s: float | None = None
    cooldown_s: float | None = None
    idle_timeout_s: float | None = None
    # #196-followup: per-ENDPOINT same-endpoint retry budget (each preset in the
    # chain uses its own). None = inherit the global default.
    num_retries: int | None = None
    # CHAIN-level resilience, read only from the chain HEAD preset (a fallback's
    # own values are ignored, like its `fallbacks`): how many times to re-sweep
    # the whole chain (one round per backoff entry; `[]` = a single sweep) and the
    # total wall-clock budget for the turn before surfacing a busy failure.
    round_backoff_s: tuple[float, ...] | None = None
    total_deadline_s: float | None = None


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
        #
        # #537: `search_wiki` is deliberately absent. It greps wiki pages and hands
        # back isolated `page:line: text` matches; without `read_file` beside it the
        # holder can never open the page, follow a `[[wikilink]]`, or cite what it
        # found — the opposite of the index-first navigation the LLM-wiki design
        # rests on. #270's A/B convention keeps that leaf with the wiki maintainer /
        # reader; the KB agent consults the wiki through `ask_wiki`, which delegates
        # the whole navigation to the reader in a throwaway context.
        "allowed_tools": [
            "kb_search",
            "ask_wiki",
            "lookup_glossary",
            "request_wiki_update",
        ],
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
        "allowed_tools": [
            "kb_search",
            "ask_wiki",
            "lookup_glossary",
            "request_wiki_update",
        ],
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
        "allowed_tools": [
            "kb_search",
            "ask_wiki",
            "lookup_glossary",
            "request_wiki_update",
        ],
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
    # `card-drafter` (#175) — the LLM-only preset `kb.card_drafter` references to
    # draft context cards from documents. Same default model as retrieval;
    # operators override just this preset's `model` / `llm` to draft on a
    # different provider.
    "card-drafter": {
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
        vision=bool(d.get("vision", False)),
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
        fallbacks=list(d.get("fallbacks", [])),
        ttft_timeout_s=d.get("ttft_timeout_s"),
        cooldown_s=d.get("cooldown_s"),
        idle_timeout_s=d.get("idle_timeout_s"),
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
    # #231: the LLM-as-judge that scores sanity-matrix cells (ai_grade/ai_note)
    # and the per-model fitness verdict. Same usage-entry shape + preset cascade
    # as `kb.retrieval_llm`; a preset with `fallbacks` becomes a busy-aware
    # `FallbackLlm` (#196). `None` ⇒ AI scoring off (the ai columns stay empty).
    # Should be a capable model distinct from the models under test (no self-grade).
    judge_llm: RetrievalLlmRef | None = None


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
class FailoverSettings:
    """Global defaults for busy-aware LLM failover (#196 + #131).

    A preset overrides any of these per-model (slow models relax ``ttft``); these
    are the fallbacks when a preset leaves the field unset. ``ttft_timeout_s`` —
    streaming: no first token within this ⇒ the model is busy, switch + cool it
    down. ``cooldown_s`` — how long a busy ``(model, endpoint)`` is skipped.
    ``idle_timeout_s`` — a mid-stream stall longer than this raises (a stream
    already seen can't be transparently restarted, so it does NOT switch)."""

    ttft_timeout_s: float = 8.0
    cooldown_s: float = 30.0
    idle_timeout_s: float = 120.0
    # #196-followup: configurable resilience under sustained busy. ``num_retries``
    # quick same-endpoint retries before switching; ``round_backoff_s`` re-sweeps
    # the whole chain once per entry (cooldown-aware wait — at least this long, but
    # until a parked endpoint un-cools; ``[]`` = a single sweep, the original #196
    # behaviour); ``total_deadline_s`` caps the whole turn so it fails readably
    # instead of hanging forever. Per-preset overridable (num_retries per endpoint;
    # round_backoff_s / total_deadline_s from the chain head).
    num_retries: int = 2
    round_backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
    total_deadline_s: float = 120.0


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
    sandbox_host: SandboxHostSettings = field(default_factory=SandboxHostSettings)
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
    failover: FailoverSettings = field(default_factory=FailoverSettings)
