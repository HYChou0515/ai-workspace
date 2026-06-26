"""workspace_app resources — the typed records the API persists.

The single public way to get a SpecStar with these registered is
``make_spec(...)``: it constructs, configures, and registers in one
call. Direct ``SpecStar()`` usage at the API boundary leaves the
resource registry empty, which immediately breaks any code path that
asks for a resource manager (KeyError: Notification etc.).

``register_all`` exists only as the internal implementation detail
``make_spec`` calls — it is deliberately not in ``__all__``. If you
think you need it, you actually need ``make_spec``.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from specstar import BackendConfig, Schema, SpecStar
from specstar.crud.route_templates.migrate import MigrateRouteTemplate
from specstar.types import IndexableField

from ..workflow.run import WorkflowRun
from .agent_config import AgentConfig
from .check_run import CheckRun
from .citation_event import CitationEvent
from .conversation import Conversation, Message
from .kb import (
    Collection,
    ContextCard,
    DocChunk,
    IndexRun,
    IndexUnitText,
    KbChat,
    SourceDoc,
    WikiBuildState,
    WikiPage,
)
from .notification import Notification
from .sanity import (
    CustomSanityQuestion,
    SanityResult,
    SanityVerdict,
    sanity_result_id,
    sanity_verdict_id,
)

__all__ = [
    "AgentConfig",
    "CitationEvent",
    "Collection",
    "ContextCard",
    "Conversation",
    "CustomSanityQuestion",
    "DocChunk",
    "IndexRun",
    "IndexUnitText",
    "KbChat",
    "Message",
    "Notification",
    "SanityResult",
    "SanityVerdict",
    "SourceDoc",
    "WikiBuildState",
    "WikiPage",
    "make_spec",
    "sanity_result_id",
    "sanity_verdict_id",
]


def _reindex_only(record: Any) -> Any:
    """A no-op schema migration step (data unchanged) — used as ``step(None,
    _reindex_only, …)`` so migrating a pre-Schema (version ``None``) row to the
    current version re-extracts its indexed_data without altering the record.
    The reindex is the migrate's write-back side effect; the transform is
    identity."""
    return record


def make_spec(
    *,
    default_user: str | Callable[[], str] = "default-user",
    default_now: Callable[[], datetime] | None = None,
    backend: BackendConfig | None = None,
) -> SpecStar:
    """Build a fully-ready SpecStar — every resource the API references
    is already registered when this returns.

    Use everywhere that needs a SpecStar:
    - production wiring (``factories.get_spec``) overrides ``backend``
      with the deploy's postgres/disk profile and threads the request's
      ``get_user_id`` callable as ``default_user``.
    - tests get test-friendly defaults (``"default-user"``, in-memory
      backend) and override only the bits they care about.

    Direct ``SpecStar()`` construction at the API boundary is a bug —
    the registry stays empty and the first resource-manager lookup
    raises ``KeyError``. There is no separate ``register_all`` step;
    that's an implementation detail of this function."""
    spec = SpecStar()
    cfg: dict[str, Any] = {
        "default_user": default_user,
        "default_now": default_now or (lambda: datetime.now(UTC)),
    }
    if backend is not None:
        cfg["backend"] = backend
    spec.configure(**cfg)
    _register_all(spec)
    return spec


def _register_all(spec: SpecStar) -> None:
    """Register every workspace_app resource on ``spec``. Internal to
    ``make_spec`` — callers don't (and shouldn't) call this directly.

    `AgentConfig` is deliberately NOT registered: the agent-config
    picker is owned by the runner (Settings.agent_configs in
    config.yaml), surfaced via `/agent-configs` directly from
    `runner.list_configs()`. Persisting it as a specstar resource
    would let the FE write to it and diverge from the deploy's
    declared list."""
    # Opt into specstar's bulk-migration routes (POST /{model}/migrate/execute,
    # /migrate/single/{id}, /migrate/test) — they're opt-in, and must be
    # registered BEFORE the models so `spec.apply(app)` mounts them per model.
    # This is how a pre-index row gets backfilled: `migrate/execute` re-extracts
    # its indexed_data (write_back) so it lands in indexes added after it was
    # written (specstar discussion #365). Registered globally so every model has
    # the route, not just the KB ones.
    spec.add_route_template(MigrateRouteTemplate())
    # #89: register each App's own WorkItem resource (RcaInvestigation, …). The
    # legacy single-Investigation model was removed in P8 (see
    # docs/plan-app-templates.md). Late import keeps the apps layer's dependency
    # one-directional (apps → resources, not back).
    from ..apps.registry import register_apps

    register_apps(spec)
    # item_id indexed so the per-item conversation lookup is a query, not a full
    # scan. (#89: was investigation_id + a typed Ref; now an opaque key so one
    # Conversation table serves every App's items.)
    spec.add_model(Conversation, indexed_fields=["item_id"])
    spec.add_model(Collection)
    # A newly-added index only covers rows written AFTER it exists — specstar
    # extracts indexed_data at write time and does NOT auto-backfill pre-existing
    # rows (they group under `None`; specstar discussion #359). The backfill is
    # `rm.migrate(rid)`, which re-extracts indexed_data — but migrate needs a
    # `Schema` with a path FROM the rows' current version. SourceDoc +
    # CitationEvent gained their indexes after launch, so rows predating that are
    # version `None`; we give each a `Schema("v2")` with a no-op `step(None, …)`
    # (a pure reindex — no data change) so the migrate route can backfill old
    # rows: an operator POSTs `/source-doc/migrate/execute` (and
    # `/citation-event/migrate/execute`) once, which re-extracts their
    # indexed_data into the new indexes (see the MigrateRouteTemplate opt-in
    # above). New rows are written at "v2" already.
    #
    # collection_id indexed so listing a collection's documents is a query, not
    # a full scan (issue #14: ~100 docs hung). content.size is indexed (as a
    # scalar `content_size`) so the collection cards can SUM blob sizes per
    # collection via one `exp_aggregate_by` instead of materialising every doc.
    #
    # #263: `path` indexed so resolving a user-supplied filename → its
    # source-doc id is a query (exact or basename via `.contains`), the lookup
    # that backs the location-filtered kb_search. Bumped v2 → v3 with steps from
    # BOTH `None` (the bulk of production rows) AND `v2` (rows already migrated
    # once): adding an index to an already-`v2` model would NOT re-extract those
    # v2 rows (migrate is a no-op when a row is already at the target version),
    # so the new `path` index would silently miss them. v3 forces every row to
    # re-extract on the next `POST /source-doc/migrate/execute`.
    spec.add_model(
        Schema(SourceDoc, "v3")
        .step(None, _reindex_only, to="v3", source_type=SourceDoc)
        .step("v2", _reindex_only, to="v3", source_type=SourceDoc),
        indexed_fields=[
            "collection_id",
            IndexableField("content.size", index_key="content_size"),
            IndexableField("path", str),
        ],
    )
    # source_doc_id + collection_id indexed so counting a doc's chunks (and the
    # retriever's per-collection lookup) is a query — a non-indexed filter would
    # load + deserialize every chunk's embedding Vector, which is the hang.
    # #263: provenance locators indexed so a chunk can be fetched by its
    # structural location (page range / sheet) — a deterministic WHERE that
    # composes with the dense/sparse retrieval (the same way collection_id
    # already filters the vector query). `provenance` is a dict[str, Any] and
    # specstar's `_extract_by_path` walks INTO it, so `provenance.page` extracts
    # the subkey natively. int locators support range queries (operator-driven
    # ::numeric cast on every backend); str locators (sheet) use exact match.
    #
    # Schema v3 with steps from BOTH `None` and `v2` so an operator can backfill
    # the provenance indexes onto pre-existing chunks via
    # `POST /doc-chunk/migrate/execute` — re-extracting indexed_data from the
    # `provenance` already stored on each chunk, NOT re-parsing/re-embedding
    # (#263). DocChunk carried no Schema before, so its rows are version `None`;
    # the `v2` edge is defensive (harmless if no v2 row exists).
    spec.add_model(
        Schema(DocChunk, "v3")
        .step(None, _reindex_only, to="v3", source_type=DocChunk)
        .step("v2", _reindex_only, to="v3", source_type=DocChunk),
        indexed_fields=[
            "source_doc_id",
            "collection_id",
            IndexableField("provenance.page", int, index_key="page"),
            IndexableField("provenance.slide", int, index_key="slide"),
            IndexableField("provenance.sheet", str, index_key="sheet"),
            IndexableField("provenance.row", int, index_key="row"),
            IndexableField("provenance.jsonl_line", int, index_key="line"),
        ],
    )
    # #227: fan-out join state, one row per doc (id = doc id). `status` indexed
    # so the safety sweep can find runs still "running" with no live jobs; the
    # per-doc active-run guard is a point get by id.
    spec.add_model(IndexRun, indexed_fields=["status"])
    # #227: per-batch staged text (doc_id indexed so finalize lists a doc's
    # batches to rejoin into SourceDoc.text). Transient; deleted at finalize.
    spec.add_model(IndexUnitText, indexed_fields=["doc_id"])
    # Issue #50: collection_id indexed so a wiki's pages list (WikiFileStore.ls)
    # is a query, not a full scan.
    spec.add_model(WikiPage, indexed_fields=["collection_id"])
    # Issue #59: one durable build-status row per collection (id = collection
    # id). Read by the /wiki/status endpoint; written by whichever pod runs
    # the maintenance. The WikiMaintenanceJob model itself is registered by
    # the coordinator (it needs the runtime handler).
    spec.add_model(WikiBuildState)
    # shared_with indexed so "chats shared with me" is a contains-query (owner
    # filtering uses the built-in created_by meta index).
    spec.add_model(KbChat, indexed_fields=["shared_with"])
    # #106 context cards. collection_id indexed → list a collection's cards /
    # load the match() vocab is a query; norm_keys indexed → get(term)'s exact
    # element-membership lookup (same list-membership index as KbChat.shared_with).
    spec.add_model(ContextCard, indexed_fields=["collection_id", "norm_keys"])
    # recipient indexed so "my notifications" is a query, not a full scan.
    spec.add_model(Notification, indexed_fields=["recipient"])
    # #100: workflow runs. item_id indexed so "an item's runs" is a query; status
    # so "active runs" (the concurrency cap) is a query, not a full scan. The
    # filesystem is the journal (manual §9), so this resource holds status, not
    # step results.
    spec.add_model(WorkflowRun, indexed_fields=["item_id", "status"])
    # document_id + collection_id indexed so the "cited N×" tallies are a
    # group-by aggregate (`exp_aggregate_by` → {key: count}) instead of a full
    # scan of the append-only log on every list call. Schema("v2") + the None
    # reindex step (see SourceDoc above) so pre-index events backfill via migrate.
    spec.add_model(
        Schema(CitationEvent, "v2").step(None, _reindex_only, source_type=CitationEvent),
        indexed_fields=["document_id", "collection_id"],
    )
    # check_id indexed so a per-check history query ("when did the VLM
    # stop passing?") never falls back to a full scan.
    spec.add_model(CheckRun, indexed_fields=["check_id"])
    # Model-sanity matrix cells (current-only). model indexed so the FE lists
    # one model's grid without a full scan; auto routes serve GET /sanity-result.
    spec.add_model(SanityResult, indexed_fields=["model"])
    # #231: one fitness verdict per model (current-only). model indexed so a
    # verdict get/list is a query; auto routes serve GET /sanity-verdict.
    spec.add_model(SanityVerdict, indexed_fields=["model"])
    # #231: user-authored sanity questions (AI-only graded). Few rows (operator
    # authored via the 題目管理 panel), so the coordinator lists them all + filters
    # in Python; auto-CRUD routes (POST/GET/PUT/DELETE /custom-sanity-question)
    # own the lifecycle. category indexed for a possible 題組 filter.
    spec.add_model(CustomSanityQuestion, indexed_fields=["category"])
