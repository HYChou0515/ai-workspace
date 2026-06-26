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
import datetime as dt
import logging
import math
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import msgspec
from specstar import QB, Schema, SpecStar
from specstar.events import OnSuccessPatch, do
from specstar.types import ResourceAction, ResourceIDNotFoundError, TaskStatus

from ..resources import DocChunk, IndexRun, IndexUnitText, SourceDoc
from .index_jobs import IndexJob, IndexJobPayload
from .index_run import IndexRunStore
from .job_audit import preserve_job_creator

if TYPE_CHECKING:
    from specstar.events import EventContext

    from .ingest import Ingestor
    from .wiki.coordinator import WikiMaintenanceCoordinator

_LOGGER = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while waiting for the queue to drain
# #227: each fan-out batch numbers its chunks from batch_index * this stride, so
# independent process jobs never collide on `seq` (which is cosmetic ordering —
# merge adjacency uses char offsets). Far above any realistic chunks-per-batch.
_SEQ_STRIDE = 1_000_000


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
        unit_batch_sizes: dict[str, int] | None = None,
        default_unit_batch: int = 8,
        get_user_id: Callable[[], str] | None = None,
    ) -> None:
        self._spec = spec
        self._ingestor = ingestor
        self._wiki = wiki_coordinator
        # #186: who an enqueue is credited to. In a request that's the real user
        # (specstar resolves it as the spec default on any manager that still
        # carries it — e.g. SourceDoc); production injects the same `get_user_id`.
        # Only ever called on the request-side `enqueue`; the worker fan-out reuses
        # the run's requester (`job.info.created_by`) instead.
        self._get_user_id = get_user_id or (lambda: self._spec.get_resource_manager(SourceDoc).user)
        # #227 fan-out: units-per-process-job, per parser class. PDF/PPTX go to
        # the VLM (~slow seconds/page) so they batch small; row-based parsers
        # (CSV/Excel/JSON) batch large (cheap parse, the embed is the cost). Each
        # batch must stay well under the broker's consumer-ack timeout (~30 min).
        self._unit_batch_sizes = unit_batch_sizes or {
            "PdfParser": 8,
            "PptxParser": 8,
            "CsvParser": 500,
            "ExcelParser": 500,
            "JsonParser": 200,
        }
        self._default_unit_batch = default_unit_batch
        self._runs = IndexRunStore(spec)
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
        # #186: let specstar's job lifecycle preserve each job's creator instead
        # of stamping the worker's default. Every enqueue below sets the user via
        # `using()`, so the manager needs no fallback default.
        preserve_job_creator(self._job_rm)
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
        # Coalesce a pending split, AND don't start a fresh fan-out while one is
        # already in flight for this doc (#227): the active IndexRun is the
        # queue-agnostic guard that replaces partition_key serialization (which
        # the RabbitMQ backend ignores) against two runs racing _delete_chunks.
        if self._has_pending_job(doc_id) or self._runs.is_active(doc_id):
            return False
        # #186: stamp the split job with the requester (this runs in their
        # request). specstar then preserves it across the whole fan-out lifecycle.
        with self._job_rm.using(user=self._get_user_id()):
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
        """Dispatch one index step by ``kind`` (#227). Runs OFF the request path
        (the consumer's own thread)."""
        payload = job.data.payload
        # #186: the human who triggered this run. The initial split job is created
        # in the requester's request (the producer runs there), so get_user_id
        # stamps its ``created_by`` with the real user; the worker then propagates
        # that onto every derived artifact it writes (fan-out jobs + chunks). The
        # SourceDoc itself is NOT credited to the requester — its content was not
        # re-authored, so it stays its own owner (#83, via _last_updater).
        requester = job.info.created_by
        if payload.kind == "process":
            self._handle_process(payload, requester)
        elif payload.kind == "finalize":
            # finalize writes the SourceDoc (credited to its own owner, #83); the
            # requester only rides into the wiki hook it chains.
            self._handle_finalize(payload, requester)
        else:
            self._handle_split(payload, requester)

    def _last_updater(self, doc_id: str) -> str | None:
        """The doc's last updater (#83): a job pod has no request user, so the
        index must run AS the real uploader or it erases ``updated_by``. ``None``
        means the doc was deleted between enqueue and run."""
        try:
            return self._spec.get_resource_manager(SourceDoc).get(doc_id).info.updated_by
        except ResourceIDNotFoundError:
            return None

    def _handle_split(self, payload, requester: str) -> None:
        """Plan the index: run it whole (small / multi-parser / no-parser), or
        fan it out into per-unit-range ``process`` jobs when one parser reports
        many units. Seeds the ``IndexRun`` join state BEFORE enqueuing any
        process job, so no early finisher can finalize prematurely."""
        doc_id, cid = payload.doc_id, payload.collection_id
        updater = self._last_updater(doc_id)
        if updater is None:
            return
        try:
            units, parser_id = self._ingestor.fanout_units(doc_id)
        except Exception:  # noqa: BLE001 — can't plan → fall back to a single whole-doc job
            _LOGGER.exception("IndexCoordinator: fan-out planning failed for %s", doc_id)
            units, parser_id = 1, ""
        if units <= 1:
            self._index_whole(doc_id, updater, requester)
            return
        batch = self._unit_batch_sizes.get(parser_id, self._default_unit_batch)
        nbatches = math.ceil(units / batch)
        self._ingestor.prepare_fanout(doc_id)  # clear chunks ONCE before fan-out
        self._runs.start(doc_id, cid, total=nbatches)
        # #186: credit the fan-out jobs to the requester (not the bare worker
        # default) so the chain reading job.created_by stays the real user.
        with self._job_rm.using(user=requester):
            for b in range(nbatches):
                start = b * batch
                self._job_rm.create(
                    IndexJob(
                        payload=IndexJobPayload(
                            doc_id=doc_id,
                            collection_id=cid,
                            kind="process",
                            unit_start=start,
                            unit_end=min(units, start + batch),
                            batch_index=b,
                        ),
                        # No partition_key (#227): process jobs are meant to parallelize
                        # across pods; the CAS join, not the queue, guards correctness.
                        partition_key=None,
                    )
                )

    def _index_whole(self, doc_id: str, updater: str, requester: str) -> None:
        """The unchanged single-job path: chunk + embed the whole doc, flip its
        status, run the wiki hook. Errors are logged + swallowed (the Ingestor
        marks the doc status=error; a bad doc must not wedge the queue).

        Two acting users are bound at once (#186): the SourceDoc stays its own
        owner (``updater``, #83), while the regenerated DocChunks are credited to
        the ``requester`` who triggered the run. Both managers are spec
        singletons, so binding them here covers every write the (synchronous,
        same-thread) Ingestor makes through ``get_resource_manager`` — no Ingestor
        change needed."""
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        chunk_rm = self._spec.get_resource_manager(DocChunk)
        try:
            with doc_rm.using(user=updater), chunk_rm.using(user=requester):
                self._ingestor.index(doc_id, source_doc_rm=doc_rm)
        except Exception:  # noqa: BLE001 — doc is marked error by the Ingestor
            _LOGGER.exception("IndexCoordinator: indexing failed for %s", doc_id)
            return
        self._wiki_hook(doc_id, requester)

    def _handle_process(self, payload, requester: str) -> None:
        """Index ONE unit batch end-to-end, stage its text, record it done, and —
        if it's the one that completes the set — win the finalize gate and enqueue
        the finalize job. A failure RE-RAISES so the broker retries it; a
        permanent failure dead-letters and the safety sweep records it failed."""
        doc_id = payload.doc_id
        updater = self._last_updater(doc_id)
        if updater is None:
            return  # doc deleted between split and run
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        chunk_rm = self._spec.get_resource_manager(DocChunk)
        # #227 SEQ_STRIDE: each batch numbers its chunks from batch_index*stride so
        # independent process jobs never collide on seq (cosmetic ordering only).
        seq_base = payload.batch_index * _SEQ_STRIDE
        # #186: SourceDoc → its owner (updater); DocChunks → the requester.
        with doc_rm.using(user=updater), chunk_rm.using(user=requester):
            text = self._ingestor.index_units(
                doc_id,
                (payload.unit_start, payload.unit_end),
                seq_base=seq_base,
                source_doc_rm=doc_rm,
            )
        self._stage_text(doc_id, payload.batch_index, text)
        self._runs.mark_done(doc_id, payload.batch_index)
        if self._runs.claim_finalize(doc_id):
            self._enqueue_finalize(doc_id, payload.collection_id, requester)

    def _enqueue_finalize(self, doc_id: str, collection_id: str, requester: str) -> None:
        # #186: the job manager has no default user, so a finalize job MUST be
        # stamped explicitly — with the run's requester (preserved through the
        # fan-out) on the normal path, or the doc's owner as a best-effort
        # fallback on the safety-sweep recovery path.
        with self._job_rm.using(user=requester):
            self._job_rm.create(
                IndexJob(
                    payload=IndexJobPayload(
                        doc_id=doc_id, collection_id=collection_id, kind="finalize"
                    ),
                    partition_key=None,
                )
            )

    def _handle_finalize(self, payload, requester: str) -> None:
        """Exactly-once close-out of a fan-out: rejoin the staged batch text into
        ``SourceDoc.text``, flip status (``error`` if any batch failed, else
        ``ready``), clear staging, close the run, and run the wiki hook."""
        doc_id = payload.doc_id
        run = self._runs.get(doc_id)
        if run is None:  # pragma: no cover — finalize implies a run exists
            return
        # Idempotent: a duplicate finalize (sweep re-enqueue, redelivery) must NOT
        # re-run — staging is already cleared, so it would wipe SourceDoc.text.
        if run.status != "running":
            return
        updater = self._last_updater(doc_id)
        if updater is None:
            self._clear_staged_text(doc_id)
            return
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        doc = doc_rm.get(doc_id).data
        assert isinstance(doc, SourceDoc)
        status = "error" if run.failed else "ready"
        detail = "" if status == "ready" else f"{len(run.failed)} batch(es) failed to index"
        text = self._joined_staged_text(doc_id)
        with doc_rm.using(user=updater):
            doc_rm.update(
                doc_id,
                msgspec.structs.replace(
                    doc, status=status, status_detail=detail, text=text or None
                ),
            )
        self._clear_staged_text(doc_id)
        # The RUN is done either way (its job is complete); the doc carries the
        # ready/error verdict. `error` keeps the failed batches visible for ops.
        self._runs.finish(doc_id, status="error" if run.failed else "done")
        if status == "ready":
            self._wiki_hook(doc_id, requester)

    # ── safety sweep (#227): recover the fan-out failure branch ──────
    def sweep_stuck_runs(self, *, stuck_after_seconds: float = 3600.0) -> list[str]:
        """Backstop the fan-out's two failure modes and return the doc ids it
        re-drove. Runs periodically (and is queue-agnostic):

        - **finalize-recovery** — a run whose batches are all accounted for
          (``done ∪ failed == total``) but whose finalize never ran (the trigger
          winner crashed): claim the gate if open and (re)enqueue finalize. No
          grace, because the gate condition is already met.
        - **stuck-recovery** — a run with missing batches and no progress for
          ``stuck_after_seconds``. Each live batch advances the run as it
          finishes, so a stalled run stops updating; the missing batches
          dead-lettered, so record them ``failed`` and then finalize.

        Finalize is idempotent (it no-ops once the run leaves ``running``), so a
        re-enqueue that races a normal finalize is harmless."""
        acted: list[str] = []
        now = dt.datetime.now(dt.UTC)
        rm = self._spec.get_resource_manager(IndexRun)
        for res in rm.list_resources((QB["status"] == "running").build()):
            run = res.data
            assert isinstance(run, IndexRun)
            doc_id = res.info.resource_id  # ty: ignore[unresolved-attribute]
            age = (now - res.info.updated_time).total_seconds()  # ty: ignore[unresolved-attribute]
            accounted = set(run.done) | set(run.failed)
            if len(accounted) < run.total:
                if age < stuck_after_seconds:
                    continue  # still progressing (recent update) — leave it alone
                for i in range(run.total):
                    if i not in accounted:
                        self._runs.mark_failed(doc_id, i)
            # #186: the sweep runs in a worker with no request and no original
            # requester to recover, so the re-driven finalize job is credited to
            # the doc's owner (best effort). A gone doc has no owner — its finalize
            # would no-op anyway, so skip it (and avoid a bare, user-less create).
            requester = self._last_updater(doc_id)
            if requester is None:
                continue
            # Gate is now met (already, or after marking the missing failed).
            if self._runs.claim_finalize(doc_id):
                self._enqueue_finalize(doc_id, run.collection_id, requester)  # won → finalize
                acted.append(doc_id)
            elif run.finalized and age >= stuck_after_seconds:
                # Claimed earlier but the run never closed out — the finalize job
                # was lost (winner crashed after claiming). Re-drive it; finalize
                # is idempotent, and the grace avoids spamming a healthy in-flight
                # finalize.
                self._enqueue_finalize(doc_id, run.collection_id, requester)
                acted.append(doc_id)
        return acted

    def _wiki_hook(self, doc_id: str, requester: str) -> None:
        if self._wiki is None:
            return
        try:
            # on_doc_indexed is async (it enqueues a wiki job); drive it with a
            # fresh loop since we're on the consumer's worker thread. #186: this
            # runs in a worker with no request, so hand it the run's requester to
            # credit the wiki job + build-state to.
            asyncio.run(self._wiki.on_doc_indexed(doc_id, requested_by=requester))
        except Exception:  # noqa: BLE001 — a wiki-hook failure must not fail the index job
            _LOGGER.exception("IndexCoordinator: wiki hook failed for %s", doc_id)

    # ── fan-out text staging (#227) ──────────────────────────────────
    def _stage_text(self, doc_id: str, batch_index: int, text: str) -> None:
        rm = self._spec.get_resource_manager(IndexUnitText)
        rm.create_or_update(
            f"{doc_id}.t{batch_index}",
            IndexUnitText(doc_id=doc_id, batch_index=batch_index, text=text),
        )

    def _joined_staged_text(self, doc_id: str) -> str:
        rm = self._spec.get_resource_manager(IndexUnitText)
        rows = [r.data for r in rm.list_resources((QB["doc_id"] == doc_id).build())]
        rows = [r for r in rows if isinstance(r, IndexUnitText)]
        rows.sort(key=lambda r: r.batch_index)
        return "\n\n".join(r.text for r in rows if r.text).strip()

    def _clear_staged_text(self, doc_id: str) -> None:
        rm = self._spec.get_resource_manager(IndexUnitText)
        for r in rm.list_resources((QB["doc_id"] == doc_id).build()):
            rm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]

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
