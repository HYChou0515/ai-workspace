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

from .agent_config import AgentConfig
from .check_run import CheckRun
from .citation_event import CitationEvent
from .conversation import Conversation, Message
from .kb import Collection, DocChunk, KbChat, SourceDoc, WikiBuildState, WikiPage
from .notification import Notification
from .sanity import SanityResult, sanity_result_id

__all__ = [
    "AgentConfig",
    "CitationEvent",
    "Collection",
    "Conversation",
    "DocChunk",
    "KbChat",
    "Message",
    "Notification",
    "SanityResult",
    "SourceDoc",
    "WikiBuildState",
    "WikiPage",
    "make_spec",
    "sanity_result_id",
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
    spec.add_model(
        Schema(SourceDoc, "v2").step(None, _reindex_only, source_type=SourceDoc),
        indexed_fields=["collection_id", IndexableField("content.size", index_key="content_size")],
    )
    # source_doc_id + collection_id indexed so counting a doc's chunks (and the
    # retriever's per-collection lookup) is a query — a non-indexed filter would
    # load + deserialize every chunk's embedding Vector, which is the hang.
    spec.add_model(DocChunk, indexed_fields=["source_doc_id", "collection_id"])
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
    # recipient indexed so "my notifications" is a query, not a full scan.
    spec.add_model(Notification, indexed_fields=["recipient"])
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
