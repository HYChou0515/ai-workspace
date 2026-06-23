"""IndexCoordinator (#82) — durable, cross-pod indexing queue + worker.

Mirrors ``WikiMaintenanceCoordinator`` (#59): the upload/reindex/sync routes
``enqueue`` an ``IndexJob`` and return immediately; a background consumer
(``start_consume(block=False)``) drains it in its OWN thread — off the
request-serving event loop and off the shared ``asyncio.to_thread`` pool — so a
slow synchronous embedder call can no longer starve other requests (the bug the
in-process ``_index_sem`` + ``to_thread`` couldn't fix). Jobs carry
``partition_key = doc_id`` (#134): the same doc never indexes twice at once (no
torn chunk set, and it lets a reindex coalesce on a cheap indexed lookup), but
the key is per-DOC not per-collection, so different docs still spread across
workers — embedder throughput is scaled by running more worker pods (k8s
replicas / HPA), each a single consumer doing one job at a time.

After indexing a doc, it hands the doc to the wiki coordinator (the same
index → wiki chain the old ``_index_and_maintain`` did), so enabling the wiki
path still folds new sources in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from specstar import QB, Schema, SpecStar
from specstar.events import OnSuccessPatch, do
from specstar.types import ResourceAction, ResourceIDNotFoundError, TaskStatus

from ..resources import SourceDoc
from .index_jobs import IndexJob, IndexJobPayload

if TYPE_CHECKING:
    from specstar.events import EventContext

    from .ingest import Ingestor
    from .wiki.coordinator import WikiMaintenanceCoordinator

_LOGGER = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while waiting for the queue to drain


class IndexCoordinator:
    """Enqueue + background-consume KB indexing jobs. Jobs are unpartitioned,
    so workers parallelize freely — scale embedder throughput by adding worker
    pods, not by per-collection serialization."""

    def __init__(
        self,
        spec: SpecStar,
        ingestor: Ingestor,
        *,
        wiki_coordinator: WikiMaintenanceCoordinator | None = None,
        message_queue_factory: object | None = None,
    ) -> None:
        self._spec = spec
        self._ingestor = ingestor
        self._wiki = wiki_coordinator
        # The queue backend MUST be set PER-MODEL (a global configure() doesn't
        # propagate to a real pg/disk backend) — pass the config-selected factory
        # straight to add_model. Default = the specstar Simple queue (multipod
        # via the shared backend). Same wiring as WikiMaintenanceCoordinator.
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        # `status` is queried by the consumer (`pop`) + `_active_count`;
        # `partition_key` (= the doc id, #134) by `pop`'s per-key serialization
        # and by `_has_pending_job`'s coalesce lookup.
        spec.add_model(
            Schema(IndexJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(IndexJob)
        self._consuming = False

    # ── enqueue (producer) ───────────────────────────────────────────
    def enqueue(self, doc_id: str, collection_id: str) -> bool:
        """Queue ``doc_id`` for indexing and return immediately — the work runs
        in the background consumer (this or another pod). Returns ``True`` if a
        job was created, ``False`` if it coalesced onto one already pending.

        ``partition_key = doc_id`` (#134): the queue's ``pop()`` never runs two
        jobs that share a key at once, so the SAME doc is never indexed twice
        concurrently — concurrent ``_delete_chunks`` + chunk re-create would
        otherwise race to a torn / duplicated chunk set. It's a per-DOC key, NOT
        per-collection, so different docs still carry different keys and any
        worker claims them freely: embedder throughput still scales by pod count
        (k8s replicas / HPA), which is why index jobs avoided a per-*collection*
        key in the first place. ``collection_id`` rides on the payload (the wiki
        hook + observability read it).

        Coalesce (#134): if a reindex for this doc is already PENDING, don't pile
        on another. Mashing the reindex button (or rapid edits) otherwise
        enqueues N full re-index jobs — each re-chunks, re-embeds and re-triggers
        the wiki — and holds the doc at status="indexing" until all N drain, so
        it looks permanently stuck. The single queued job re-reads the doc when
        it runs, so it picks up the latest content regardless. A job already
        PROCESSING does NOT block a new enqueue: it may have read stale content
        before an edit landed, so that edit still needs its own rerun (coalescing
        collapses only the *pending* tail, never the in-flight run)."""
        if self._has_pending_job(doc_id):
            return False
        self._job_rm.create(
            IndexJob(
                payload=IndexJobPayload(doc_id=doc_id, collection_id=collection_id),
                partition_key=doc_id,
            )
        )
        return True

    def _has_pending_job(self, doc_id: str) -> bool:
        """True if an unclaimed index job already targets ``doc_id``. An indexed
        ``(status, partition_key)`` count — ``partition_key`` IS ``doc_id`` — so
        coalescing is a cheap point lookup, never a scan of the whole queue (the
        upload route enqueues one job per archive member on the event loop)."""
        pending_for_doc = QB["status"].eq(TaskStatus.PENDING) & (QB["partition_key"] == doc_id)
        return self._job_rm.count_resources(pending_for_doc.build()) > 0

    # ── reindex-on-edit trigger (#87) ────────────────────────────────
    def install_reindex_on_edit(self) -> None:
        """Wire a ``SourceDoc`` ``on_success(patch)`` handler so a content edit —
        the FE's specstar-native blob-upload + CAS ``PATCH /source-doc/{id}`` —
        auto-enqueues a reindex, with no custom edit endpoint.

        Scoped to ``patch``, NOT ``update``: ``rm.patch`` fires both patch +
        update events, but the index worker's own ``rm.update(status="ready")``
        and the reindex route's ``rm.update(status="indexing")`` fire ONLY update
        — so a patch-scoped handler never sees them and the reindex can't loop.

        Registered post-``add_model`` (the idiom for a handler that needs a
        collaborator built after the model): ``event_handlers`` is a plain list,
        so appending here is equivalent to passing it at ``add_model`` time. Call
        once, after the coordinator exists."""
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        # `event_handlers` is on the concrete ResourceManager, not the
        # IResourceManager interface ty sees (same as `.message_queue` below).
        doc_rm.event_handlers.extend(  # ty: ignore[unresolved-attribute]
            do(self._on_doc_patched).on_success(ResourceAction.patch)
        )

    def _on_doc_patched(self, ctx: EventContext) -> None:
        """Enqueue a reindex for a just-patched doc. Runs SYNCHRONOUSLY in the
        PATCH request stack, AFTER the revision commits (so ``get`` sees the new
        data). A raise here would misclassify the committed patch as a failure
        (→ HTTP 500), so every error is swallowed — the user's edit still
        succeeds; a missed reindex is recoverable via the manual reindex route.

        The handler is deliberately dumb (always enqueue on any patch): the only
        patches to SourceDoc are content edits, and json-patch vs merge-patch
        carry different ``patch_data`` shapes, so path-sniffing here would be
        brittle. (If metadata-only patches are ever added, dedupe in the worker
        by comparing ``content.file_id`` instead.)"""
        # Only patch-success is wired (`on_success(patch)`); the isinstance also
        # narrows the broad EventContext to OnSuccessPatch for the type checker.
        if not isinstance(ctx, OnSuccessPatch):  # pragma: no cover — wiring guarantees it
            return
        try:
            doc_rm = self._spec.get_resource_manager(SourceDoc)
            doc = doc_rm.get(ctx.resource_id).data
            assert isinstance(doc, SourceDoc)  # narrow Struct|Unset for ty
            self.enqueue(ctx.resource_id, doc.collection_id)
        except Exception:  # noqa: BLE001 — never turn a committed edit into a 500
            _LOGGER.exception(
                "IndexCoordinator: reindex-on-edit enqueue failed for %s", ctx.resource_id
            )

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        self._ensure_consuming()

    def wait_idle(self, timeout: float = 10.0) -> None:
        """Block (synchronously) until the index queue drains — WITHOUT stopping
        the consumer. A sync drain point for tests and graceful operations
        (unlike ``aclose``, which also tears the consumer down). Requires a
        running consumer (started at app startup)."""
        self._ensure_consuming()
        deadline = time.monotonic() + timeout
        while self._active_count() != 0:
            if time.monotonic() >= deadline:  # pragma: no cover — safety net; drains fast
                raise TimeoutError(f"index queue did not drain within {timeout:.0f}s")
            time.sleep(_DRAIN_INTERVAL)

    def _stop_consuming(self) -> None:
        self._consuming = False
        # A consumer that never received a job may not have a started thread to
        # join (e.g. a pod that got no uploads, then shuts down) — stopping it is
        # then a harmless no-op.
        with contextlib.suppress(RuntimeError):
            self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]

    # ── consume (handler — runs in the queue's consumer thread) ──────
    def _handle(self, job) -> None:  # job: Resource[IndexJob]
        """Index one doc, then fold it into the wiki. Runs OFF the request path
        (the consumer's own thread). Exceptions are logged + swallowed: the
        Ingestor already marks the SourceDoc status=error, and a deterministic
        failure must not wedge the partition (returning normally → COMPLETED)."""
        doc_id = job.data.payload.doc_id
        # #83: a job pod has no request user, so an unguarded index() would stamp
        # the SourceDoc's updated_by with the bare default — erasing the real
        # uploader. Read the doc's last updater and run the index AS that user so
        # updated_by survives the mechanical reindex. (`using` binds this exact
        # rm instance, so we hand it to index() to use for its writes.)
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        try:
            last_updater = doc_rm.get(doc_id).info.updated_by
        except ResourceIDNotFoundError:
            return  # doc deleted between enqueue and run — nothing to index
        try:
            with doc_rm.using(user=last_updater):
                self._ingestor.index(doc_id, source_doc_rm=doc_rm)
        except Exception:  # noqa: BLE001 — doc is marked error by the Ingestor; don't wedge the queue
            _LOGGER.exception("IndexCoordinator: indexing failed for %s", doc_id)
            return
        if self._wiki is not None:
            try:
                # on_doc_indexed is async (it enqueues a wiki job); drive it with
                # a fresh loop since we're on the consumer's worker thread.
                asyncio.run(self._wiki.on_doc_indexed(doc_id))
            except Exception:  # noqa: BLE001 — a wiki-hook failure must not fail the index job
                _LOGGER.exception("IndexCoordinator: wiki hook failed for %s", doc_id)

    # ── lifecycle ────────────────────────────────────────────────────
    async def aclose(self) -> None:
        """Await all in-flight indexing (graceful shutdown / test sync) by
        polling until the queue drains, then stop the consumer. Starts the
        consumer first so a direct-construction caller still flushes."""
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
