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

import msgspec
from specstar import BackendConfig, Schema, SpecStar
from specstar.crud.route_templates.migrate import MigrateRouteTemplate
from specstar.types import IndexableField

from ..kb.chat_permission import kbchat_permission_event_handler
from ..perm.checker import (
    collection_permission_event_handler,
    source_doc_permission_event_handler,
)
from ..perm.scope import (
    GroupsProvider,
    collection_access_scope,
    conversation_access_scope,
    kbchat_access_scope,
    source_doc_access_scope,
)
from ..workflow.run import WorkflowRun
from .agent_config import AgentConfig
from .check_run import CheckRun
from .citation_event import CitationEvent
from .conversation import Conversation, Message
from .eval import (
    EvalBatchStat,
    EvalResult,
    EvalRun,
    eval_batch_stat_id,
    eval_run_id,
)
from .graph import GraphClaim
from .groups import Group, groups_of
from .kb import (
    CachedChunk,
    ClusterMember,
    CodeWikiBuildRun,
    Collection,
    ContextCard,
    DocChunk,
    DocQuestion,
    IndexCache,
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
    "CachedChunk",
    "CitationEvent",
    "ClusterMember",
    "CodeWikiBuildRun",
    "Collection",
    "ContextCard",
    "Conversation",
    "CustomSanityQuestion",
    "DocChunk",
    "DocQuestion",
    "Group",
    "IndexCache",
    "IndexRun",
    "IndexUnitText",
    "KbChat",
    "Message",
    "Notification",
    "EvalBatchStat",
    "EvalResult",
    "GraphClaim",
    "EvalRun",
    "eval_batch_stat_id",
    "eval_run_id",
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


def _backfill_token_count(record: Any) -> Any:
    """#88 migration step (v3 → v4): recompute ``SourceDoc.token_count`` from the
    already-stored extracted ``text`` so pre-#88 rows get a chunk-based token
    estimate on the next ``POST /source-doc/migrate/execute`` — no re-parse /
    re-embed. Unlike ``_reindex_only`` this DOES transform the record."""
    from ..kb.tokens import count_tokens

    assert isinstance(record, SourceDoc)
    return msgspec.structs.replace(record, token_count=count_tokens(record.text or ""))


def _migrate_kbchat_shared_with(record: Any) -> Any:
    """#304 one-off migration (``None`` → v2): fold each KbChat's legacy
    ``shared_with`` list into a first-class ``Permission`` and clear the legacy
    field so the ``kbchat_access_scope`` fallback goes inert.

    The shared_with → Permission equivalence lives in ``kb.chat_permission``
    (``permission_from_shared_with``) so a migrated and an un-migrated chat are
    authorized identically. A row that already carries a ``permission`` (written
    at v2) is left untouched."""
    from ..kb.chat_permission import permission_from_shared_with

    assert isinstance(record, KbChat)
    if record.permission is not None:
        return record
    return msgspec.structs.replace(
        record, permission=permission_from_shared_with(record.shared_with), shared_with=[]
    )


def make_spec(
    *,
    default_user: str | Callable[[], str] = "default-user",
    default_now: Callable[[], datetime] | None = None,
    backend: BackendConfig | None = None,
    superusers: frozenset[str] = frozenset(),
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
    _register_all(spec, superusers)
    return spec


def _groups_provider(spec: SpecStar) -> GroupsProvider:
    """#307 — a `user -> groups` resolver bound to THIS spec, fed to the access
    scope + write checker so a `group:<id>` grant resolves to its members. A thin
    closure so `perm/` needn't import the `Group` resource (dependency direction:
    resources → perm)."""
    return lambda user: groups_of(spec, user)


def _register_all(spec: SpecStar, superusers: frozenset[str] = frozenset()) -> None:
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

    register_apps(spec, superusers)
    # #307: a flat, owner-managed logical Group. `members` indexed so resolving a
    # user → the groups they're in (`groups_of`) is a `members.contains(user)`
    # query, not a scan. No access_scope / permission (owner-managed via routes),
    # so resolving groups can't recurse into a permission check.
    spec.add_model(Group, indexed_fields=["members"])
    # #307: the user → groups resolver every #262 access_scope + write checker
    # folds in (so a `group:<id>` grant covers its members). Injected here — it
    # needs `spec` to query the Group model, which keeps `perm/` free of a resource
    # import. Closed over THIS spec so tests with isolated specs stay isolated.
    groups = _groups_provider(spec)
    # item_id indexed so the per-item conversation lookup is a query, not a full
    # scan. (#89: was investigation_id + a typed Ref; now an opaque key so one
    # Conversation table serves every App's items.) #306 PR3: the denormalized
    # item-permission mirror (`item_visibility` / `item_read_chat` / `item_created_by`)
    # is indexed so `conversation_access_scope` gates reading the thread on the
    # item's `read_chat` at the storage layer (the item's own scope never covers the
    # Conversation auto-CRUD). Registered AFTER `groups` so a `group:` read_chat
    # grant matches. A pre-#306 chat (absent mirror cell) reads as public via isna().
    spec.add_model(
        Conversation,
        indexed_fields=[
            "item_id",
            ("item_visibility", str),
            ("item_read_chat", list),
            "item_created_by",
        ],
        access_scope=conversation_access_scope(superusers, groups),
    )
    # #262: `permission.read_meta` / `.visibility` drive the access_scope (row-
    # level visibility) — see perm.scope. Indexed so the scope filters at storage.
    # #262: `permission_checker=` is SHADOWED by specstar's spec-level AllowAll
    # default (`self.permission_checker or permission_checker`), so the per-verb
    # write ACL is attached via the per-model `event_handlers` slot instead — see
    # perm.checker.collection_permission_event_handler. `access_scope` (row-level
    # read/list visibility → 404) IS threaded straight through and composes: scope
    # decides "does this row exist for me?", the checker decides "may I write it?".
    spec.add_model(
        Collection,
        indexed_fields=[
            ("permission.visibility", str),
            ("permission.read_meta", list),
            # Global-collection concept: the baseline scope is a query over this.
            ("is_global", bool),
            # #534: the graph dispatch queries the metric-extraction opt-in set.
            ("use_graph", bool),
        ],
        access_scope=collection_access_scope(superusers, groups),
        event_handlers=[collection_permission_event_handler(superusers, groups)],
    )
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
    # Its `field_type=int` is load-bearing: the collections dashboard's
    # `ForeignAggregate(Sum(content_size))` only pushes down to a real GROUP BY
    # (vs streaming every doc into Python) when the field carries a declared
    # numeric type (specstar #406/#407). `token_count` below is likewise `int`;
    # `updated_time` (the `latest_doc` Max) is a meta column, auto-eligible.
    # Adding `field_type` is registration-only (query-time push-down eligibility
    # + result coercion) — the stored value is unchanged, so it needs NO Schema
    # bump / re-extraction. A test guards this (test_collections.py).
    #
    # #263: `path` indexed so resolving a user-supplied filename → its
    # source-doc id is a query (exact or basename via `.contains`), the lookup
    # that backs the location-filtered kb_search. Bumped v2 → v3 with steps from
    # BOTH `None` (the bulk of production rows) AND `v2` (rows already migrated
    # once): adding an index to an already-`v2` model would NOT re-extract those
    # v2 rows (migrate is a no-op when a row is already at the target version),
    # so the new `path` index would silently miss them. v3 forces every row to
    # re-extract on the next `POST /source-doc/migrate/execute`.
    #
    # #88: `token_count` indexed so the collection grid can SUM a chunk-based
    # "≈ N tokens" estimate per collection (replacing the FE's raw-blob bytes/4
    # guess). Bumped v3 → v4 with a DATA step (`_backfill_token_count`, NOT a
    # no-op reindex) so the same migrate route recomputes token_count from each
    # pre-#88 row's already-stored `text` — no re-parse / re-embed. New rows get
    # it at index time (see kb.ingest / kb.index_coordinator).
    #
    # #105: `quality_score` indexed so the document list can sort by quality
    # ("show me the worst docs") and the retriever can batch-load candidate doc
    # scores to down-weight bad docs. Bumped v4 → v5 with a no-op reindex step
    # (the score is computed by the judge at index time, NOT by migrate — there
    # is no LLM in a migration). Pre-#105 rows stay un-scored (`quality_score`
    # decodes to `None`, the neutral default) and only become countable in the
    # new index after `POST /source-doc/migrate/execute` re-extracts them; they
    # are never penalised in search while un-scored.
    # #395: `status` + `status_detail` + `content.content_type` +
    # `content.file_id` indexed so the document list is servable from search
    # METAS alone — the old `list_resources` path fetched every row's full data
    # blob (including the multi-KB extracted `text`) one SELECT at a time, only
    # to keep a dozen small fields. These four were the only list-rendered
    # fields not already in `indexed_data` (`file_id` is how the FE builds
    # sibling-image / download blob URLs straight from a row, #87/#247); with
    # them the list/status endpoints never touch the data table (blobs are
    # read exclusively by the open-a-document path).
    # `status_detail` is display data, not a filter key — indexing it is a
    # deliberate projection trade-off (its writer caps it at 240 chars).
    # Bumped v5 → v6 with a no-op reindex step; pre-#395 rows surface a missing
    # `status` until the operator runs `POST /source-doc/migrate/execute`
    # (the list treats that window as "ready" — the overwhelmingly common
    # terminal state).
    #
    # #303: `collection_visibility` + `collection_read_meta` + `collection_created_by`
    # indexed so the `source_doc` access_scope filters a doc by its (denormalized)
    # collection visibility at the storage layer (hiding even the auto-CRUD
    # `GET /source-doc/{id}`). Bumped v6 → v7 with a reindex step. NOTE (#494):
    # a pre-#303 row has NO `collection_visibility` cell at all (the field wasn't
    # indexed when it was written), so the access scope's "absent ≡ public" clause
    # MUST be `isna()` (absent-OR-null), NOT `is_null()` (present-null only) —
    # `is_null()` does not match an absent cell on postgres/sqlite, which 404'd
    # legacy docs on open until it was fixed to `isna()`. The actual per-collection
    # values are backfilled by the fan-out (doc-create + the collection permission
    # setter), NOT by migrate (a migrate step can't load the parent collection).
    #
    # #308: `permission.visibility` + `permission.read_meta` + `permission.read_content`
    # indexed so a doc's OWN read override (SourceDoc.permission — the intersect-with-
    # collection tightening) is filterable at the storage layer: the `source_doc`
    # access_scope ANDs a doc-override predicate onto the collection-mirror one, and the
    # `denied_doc_ids` denylist (list + AI-retrieval) queries overridden docs by
    # `permission.visibility IS NOT NULL` and authorizes each from its indexed grant
    # lists — a METAS-ONLY read (no data blob), so it stays inside the #395 list budget.
    # Bumped v7 → v8 with a reindex step: `permission` defaults to `None`, so a
    # pre-#308 row's `permission.visibility` is absent/null ≡ "no override" — the
    # storage-scope's `isna()` clause (absent-OR-null, #494) passes it through on
    # every backend. Real overrides are
    # written fresh at v8 by the doc-permission endpoint, never by migrate.
    spec.add_model(
        Schema(SourceDoc, "v9")
        .step(None, _reindex_only, to="v3", source_type=SourceDoc)
        .step("v2", _reindex_only, to="v3", source_type=SourceDoc)
        .step("v3", _backfill_token_count, to="v4", source_type=SourceDoc)
        .step("v4", _reindex_only, to="v5", source_type=SourceDoc)
        .step("v5", _reindex_only, to="v6", source_type=SourceDoc)
        .step("v6", _reindex_only, to="v7", source_type=SourceDoc)
        .step("v7", _reindex_only, to="v8", source_type=SourceDoc)
        # #513 P7: `parent_doc_id` indexed so the doc list can exclude attachments
        # and a doc's attachments are a query. No-op reindex step (the value is a
        # plain field written at ingest, not computed by migrate); pre-#513 rows
        # decode to "" (= top-level) and become countable in the new index after
        # `POST /source-doc/migrate/execute`.
        .step("v8", _reindex_only, to="v9", source_type=SourceDoc),
        indexed_fields=[
            "collection_id",
            IndexableField("content.size", int, index_key="content_size"),
            IndexableField("path", str),
            IndexableField("token_count", int),
            IndexableField("quality_score", int),
            IndexableField("status", str),
            IndexableField("status_detail", str),
            IndexableField("content.content_type", str, index_key="content_type"),
            IndexableField("content.file_id", str, index_key="file_id"),
            IndexableField("collection_visibility", str),
            IndexableField("collection_read_meta", list),
            IndexableField("collection_created_by", str),
            IndexableField("permission.visibility", str),
            IndexableField("permission.read_meta", list),
            IndexableField("permission.read_content", list),
            IndexableField("parent_doc_id", str),
        ],
        access_scope=source_doc_access_scope(superusers, groups),
        # #308: gate a per-doc `permission` (override) write to the collection owner
        # so the auto-CRUD `PUT /source-doc/{id}` can't bypass the dedicated
        # endpoint's owner-only rule. Never denies the high-volume ingest / index /
        # mirror-fan-out writes (they don't touch `permission`).
        event_handlers=[source_doc_permission_event_handler(superusers)],
    )
    # source_doc_id + collection_id indexed so counting a doc's chunks (and the
    # retriever's per-collection lookup) is a query — a non-indexed filter would
    # load + deserialize every chunk's embedding Vector, which is the hang.
    # #104: source_doc_id is no longer a Ref/cascade (a chunk is bound to CONTENT,
    # not a deletable doc); it stays indexed only as the legacy/coalescing FALLBACK
    # for pre-#104 chunks whose source_file_id == "". Retiring it (stop writing,
    # then physically drop) is a later PR once prod is reindexed — see #104 plan.
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
    #
    # #104: `source_file_id` (the chunk's content hash) indexed so the dedup GC
    # can reclaim a content's chunks by hash and retrieval can expand a hit to
    # every path sharing it. Schema v3 → v4 with a `_reindex_only` step: the field
    # defaults "" on pre-#104 rows (re-extracts to "" — harmless, no real hash
    # ever equals ""), and a fresh index stamps the real value. Backfilling the
    # real hash onto old chunks is a reindex (denormalize-from-parent), not this
    # migrate — see #104 plan P4.
    spec.add_model(
        Schema(DocChunk, "v6")
        .step(None, _reindex_only, to="v3", source_type=DocChunk)
        .step("v2", _reindex_only, to="v3", source_type=DocChunk)
        .step("v3", _reindex_only, to="v4", source_type=DocChunk)
        # specstar 0.12.1 keeps a Vector out of `indexed_data` (it has a pgvector
        # column), but only for NEW writes; existing rows keep the fat JSONB — a
        # 4096-float array per embedding, indexed element-by-element by the GIN —
        # until rewritten. DocChunk carries THREE embeddings, so this is the bulk
        # of the bloat. migrate SKIPS rows already at the latest version, so this
        # no-op reindex gives every v4 row a v5 delta: `POST /doc-chunk/migrate/
        # execute` then re-extracts + re-saves each, and 0.12.1 strips the vectors
        # on the way out. Operator then `REINDEX`es the GIN to reclaim the space.
        .step("v4", _reindex_only, to="v5", source_type=DocChunk)
        # v6: `text` becomes an indexed field (its `TrigramIndex` GIN backs the
        # sparse arm's `.fuzzy()` corpus pre-narrowing). specstar extracts an indexed
        # field into `indexed_data` at WRITE time only, so pre-v6 rows have no
        # `indexed_data->>'text'` and are invisible to `.fuzzy()` until re-extracted.
        # This no-op reindex gives every v5 row a v6 delta; `POST /doc-chunk/migrate/
        # execute` then re-extracts + re-saves each (folding `text` into indexed_data),
        # after which an operator builds/`REINDEX`es the pg_trgm GIN. NOT a
        # reparse/reembed — the text is already on each row.
        .step("v5", _reindex_only, to="v6", source_type=DocChunk),
        indexed_fields=[
            "source_doc_id",
            "source_file_id",
            "collection_id",
            # The chunk text, extracted into `indexed_data` so its `TrigramIndex` GIN
            # (declared on the field) can serve the sparse arm's `.fuzzy(term)` corpus
            # narrowing. Duplicates the text into the jsonb — the accepted cost of
            # keyword-narrowing without loading the whole collection (2a).
            "text",
            IndexableField("provenance.page", int, index_key="page"),
            IndexableField("provenance.slide", int, index_key="slide"),
            IndexableField("provenance.sheet", str, index_key="sheet"),
            IndexableField("provenance.row", int, index_key="row"),
            IndexableField("provenance.jsonl_line", int, index_key="line"),
        ],
    )
    # #390: cross-path index-result cache. Content-addressed (id = the composite
    # key), shared across docs/collections, so no Ref and no indexed_fields — it
    # is only ever a point get/put/delete by id. specstar's auto `updated_time`
    # meta is enough for a future GC sweep ("drop rows unused for N days").
    spec.add_model(IndexCache)
    # #227: fan-out join state, one row per doc (id = doc id). `status` indexed
    # so the safety sweep can find runs still "running" with no live jobs; the
    # per-doc active-run guard is a point get by id.
    # #395: `collection_id` + unit progress indexed so the document list joins
    # progress via ONE collection-scoped metas search instead of a per-indexing-
    # doc point get in the row loop (an N+1 exactly when a fresh upload has the
    # whole page indexing). The CAS batch writes re-extract indexed_data on
    # every bump, so the metas read is live. Runs are short-lived join state —
    # rows from before this index simply miss the progress join (a brief 0/0
    # bar) and need no Schema/migrate ceremony.
    spec.add_model(
        IndexRun,
        indexed_fields=[
            "status",
            "collection_id",
            IndexableField("units_done", int),
            IndexableField("units_total", int),
        ],
    )
    # #281 P4: code-wiki build fan-out join state, one row per collection (id =
    # collection id). `status` indexed so a future safety sweep can find runs
    # still "running"; the active-run coalescing guard is a point get by id.
    spec.add_model(CodeWikiBuildRun, indexed_fields=["status"])
    # #227: per-batch staged text (doc_id indexed so finalize lists a doc's
    # batches to rejoin into SourceDoc.text). Transient; deleted at finalize.
    spec.add_model(IndexUnitText, indexed_fields=["doc_id"])
    # #414: card-gen fan-out join state, one row per run (id = the id enqueue
    # returns + the FE polls). `status` indexed so a future safety sweep can find
    # runs still "running"; the per-run reads are point gets by id. The run +
    # staging structs live in kb.card_gen (next to ProposedCard/DocDigest, so they
    # carry those typed fields directly); imported LAZILY here — a top-level import
    # would cycle (resources → kb.card_gen → kb.context_cards → resources).
    from ..kb.card_gen import CardGenRun, CardGenUnit, CardProposal

    # #506: ``collection_id`` newly indexed so the per-collection 待審核 tab queries
    # ``(collection_id, status)`` instead of scanning every collection's runs. It's a
    # NEW index, so existing rows (version ``None``) carry no extracted
    # ``collection_id`` and the scoped query would MISS old pending runs — exactly the
    # backlog we're trying to speed up. A no-op ``Schema("v2")`` reindex step lets an
    # operator backfill them via ``POST /card-gen-run/migrate/execute`` (new rows are
    # indexed on write); until then the GLOBAL inbox (status-only query) still sees
    # every row, so nothing is lost, only the per-collection tab under-counts old rows.
    spec.add_model(
        Schema(CardGenRun, "v2").step(None, _reindex_only, to="v2", source_type=CardGenRun),
        indexed_fields=["status", "collection_id"],
    )
    # #414: per-doc staged digest (run_id indexed so finalize lists a run's units
    # to merge + raise questions from). Transient; deleted at finalize.
    spec.add_model(CardGenUnit, indexed_fields=["run_id"])
    # #511: proposals as first-class rows (extracted from CardGenRun.proposals) so
    # the 待審核 views page via native offset/limit. collection_id + decision indexed
    # → the flat "active proposals in this collection" query; run_id indexed → list
    # a run's proposals (finalize idempotency check + commit). Ordered by meta
    # created_time (no index needed, like the doc list). id = prop:{run}:{pid}.
    spec.add_model(CardProposal, indexed_fields=["collection_id", "run_id", "decision"])
    # Issue #50: collection_id indexed so a wiki's pages list (WikiFileStore.ls)
    # is a query, not a full scan.
    spec.add_model(WikiPage, indexed_fields=["collection_id"])
    # Issue #59: one durable build-status row per collection (id = collection
    # id). Read by the /wiki/status endpoint; written by whichever pod runs
    # the maintenance. The WikiMaintenanceJob model itself is registered by
    # the coordinator (it needs the runtime handler).
    spec.add_model(WikiBuildState)
    # #304: a KbChat carries a first-class `Permission`; reads/lists are gated at
    # the storage layer by `kbchat_access_scope` (absent-permission ≡ PRIVATE,
    # unlike a collection's absent ≡ public) and auto-CRUD writes by the
    # resource-agnostic permission event handler. `permission.visibility` +
    # `permission.read_meta` are indexed so the "chats I can see" predicate is a
    # query. The legacy `shared_with` index stays so pre-#304 rows remain visible
    # to their shared users (the scope's fallback clause) until an operator runs
    # `POST /kb-chat/migrate/execute`, which folds `shared_with` into `permission`
    # and clears it (Schema `None` → v2, `_migrate_kbchat_shared_with`). New rows
    # are written at v2 with a `permission` already set (create → private).
    spec.add_model(
        Schema(KbChat, "v2").step(None, _migrate_kbchat_shared_with, source_type=KbChat),
        indexed_fields=[
            "shared_with",
            ("permission.visibility", str),
            ("permission.read_meta", list),
        ],
        access_scope=kbchat_access_scope(superusers),
        event_handlers=[kbchat_permission_event_handler(superusers)],
    )
    # #106 context cards. collection_id indexed → list a collection's cards /
    # load the match() vocab is a query; norm_keys indexed → get(term)'s exact
    # element-membership lookup (same list-membership index as KbChat.shared_with).
    spec.add_model(ContextCard, indexed_fields=["collection_id", "norm_keys"])
    # #377 doc-clarification questions. collection_id + status indexed → the global
    # inbox ("open questions in collections I can edit") is a query; norm_key indexed
    # → term-question dedup is an exact element lookup; kind indexed → split term vs
    # description without a scan.
    spec.add_model(DocQuestion, indexed_fields=["collection_id", "status", "kind", "norm_key"])
    # #506 P6 reconcile projection. collection_id scopes the native cosine query;
    # cluster_key indexed → the inbox's GROUP BY (one row per concept, P7);
    # state indexed → hide suppressed/inactive members without a scan; norm_key
    # indexed → the deterministic exact-match fast path; kind indexed → grade against
    # card members only + split proposal/term_question in the inbox. The `embedding`
    # Vector is declared on the struct (Annotated[..., Vector]) — NOT an indexed_field.
    # A `Schema` wrapper (it had none) so the vector cleanup can reach these rows.
    # ClusterMember's `embedding` Vector bloats `indexed_data` exactly like
    # DocChunk's; without a migration, `migrate/execute` raised outright, so its
    # rows could never be rewritten lean. The `None -> v1` reindex is identity —
    # its only effect is the write-back that re-extracts indexed_data (specstar
    # 0.12.1 then drops the vector). Same operator flow as doc-chunk.
    spec.add_model(
        Schema(ClusterMember, "v1").step(None, _reindex_only, to="v1", source_type=ClusterMember),
        indexed_fields=["collection_id", "cluster_key", "state", "norm_key", "kind"],
    )
    # recipient indexed so "my notifications" is a query, not a full scan; dedup_key
    # indexed so a workflow send_notification's "already sent?" is a query (#435 P5).
    spec.add_model(Notification, indexed_fields=["recipient", "dedup_key"])
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
    # #535: retrieval-eval. `EvalResult` is the durable per-(collection, run)
    # baseline — `collection_id` + `run_label` indexed so the FE lists a
    # collection's runs. `EvalRun` is the fan-out join state (mirror `IndexRun`):
    # `status` + `collection_id` indexed for the safety sweep / per-collection
    # lookup; the per-run reads are point gets by id. `EvalBatchStat` is transient
    # per-batch staging (mirror `IndexUnitText`), indexed by (collection, run) so
    # finalize lists a run's batches to rejoin, then deletes them.
    spec.add_model(EvalResult, indexed_fields=["collection_id", "run_label"])
    spec.add_model(EvalRun, indexed_fields=["status", "collection_id"])
    spec.add_model(EvalBatchStat, indexed_fields=["collection_id", "run_label"])
    # #534: flat metric-claim table. collection_id (Ref, auto) + norm_metric +
    # period indexed to filter a metric's values across decks and (later) rollup;
    # source_doc_id indexed so a re-extraction can wipe+rewrite one doc's claims.
    spec.add_model(
        GraphClaim,
        indexed_fields=["collection_id", "norm_metric", "period", "source_doc_id"],
    )
    spec.add_model(SanityResult, indexed_fields=["model"])
    # #231: one fitness verdict per model (current-only). model indexed so a
    # verdict get/list is a query; auto routes serve GET /sanity-verdict.
    spec.add_model(SanityVerdict, indexed_fields=["model"])
    # #231: user-authored sanity questions (AI-only graded). Few rows (operator
    # authored via the 題目管理 panel), so the coordinator lists them all + filters
    # in Python; auto-CRUD routes (POST/GET/PUT/DELETE /custom-sanity-question)
    # own the lifecycle. category indexed for a possible 題組 filter.
    spec.add_model(CustomSanityQuestion, indexed_fields=["category"])
