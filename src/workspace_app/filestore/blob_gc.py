"""Blob GC as a job (#245; a coordinator since the `won lease` OOM).

specstar ships the ref-count blob GC (`SpecStar.gc`, issue #370): its
``reconcile`` pass rescans every model's revisions for the live ``file_id`` set,
quarantines newly-orphaned blobs (``t1`` grace), restores any still referenced,
and permanently deletes quarantined blobs past ``t2``. It's explicit + user
scheduled — the library never runs it on a background thread.

That rescan is `collect_all_referenced_file_ids`: for every model with a
``Binary`` field, ``list(dump_meta(None))`` + ``dump_resources_bulk`` over every
resource — every REVISION of every ``WorkspaceFile`` / ``SourceDoc`` /
``WikiPage`` / ``IndexCache``, in memory. Run on an API pod's timer that was the
#804 class of failure again: the pod's last line was ``blob-gc: won lease``.

So the reconcile is a **job**. The API's ``blob_gc_sweeper`` is a pure producer
(one ``ScanLease`` window ⇒ one :meth:`enqueue_reconcile`), and this coordinator
runs it wherever the ``blob-gc`` JobType is consumed: a worker pod, or the API
itself in the all-in-one deploy (``run_consumers=True``).

The live set is built from the REGISTERED models only, so the consuming process
must register every ``Binary``-bearing model or their blobs read as orphans and
are deleted after ``t2``. specstar skips models without ``Binary`` fields
outright (``collect_all_referenced_file_ids`` returns ``(set(), True)``), so
that is the four above — three from ``make_spec`` and ``WorkspaceFile`` from
``SpecstarFileStore.__init__``. ``tests/test_worker.py`` pins that the worker's
spec registers every one the API's does.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import msgspec
from specstar import QB, Schema, SpecStar
from specstar.types import Job, TaskStatus

if TYPE_CHECKING:
    from ..monitor import IMonitor
    from .protocol import FileStore

logger = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DONE = [TaskStatus.COMPLETED, TaskStatus.FAILED]
_DRAIN_INTERVAL = 0.02
# ONE partition: two reconciles must never overlap — each quarantines and
# deletes against its own view of the live set.
_PARTITION = "blob-gc"


class BlobGcPayload(msgspec.Struct):
    kind: str = "reconcile"


class BlobGcJob(Job[BlobGcPayload]):
    """A queued blob-GC pass. ``partition_key`` is fixed (:data:`_PARTITION`)
    so passes serialise fleet-wide."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class BlobGcCoordinator:
    """Queue + run the blob-GC reconcile. ``t1`` / ``t2`` are the grace periods
    (`filestore.gc_t1` / `gc_t2`); ``monitor`` receives the #407 ``blob_gc``
    GcStats event and, when a ``filestore`` is wired, the ``ws_census``
    WorkspaceFile snapshot on the same cadence; ``now`` is the clock seam
    (deterministic tests + scheduling)."""

    def __init__(
        self,
        spec: SpecStar,
        *,
        t1: str,
        t2: str,
        monitor: IMonitor | None = None,
        filestore: FileStore | None = None,
        message_queue_factory: object | None = None,
        now: Callable[[], dt.datetime] = _utcnow,
    ) -> None:
        self._spec = spec
        self._t1 = t1
        self._t2 = t2
        self._monitor = monitor
        self._filestore = filestore
        self._now = now
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(BlobGcJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(BlobGcJob)
        self._consuming = False

    # ── producer (the API's sweeper tick) ────────────────────────────
    def enqueue_reconcile(self) -> None:
        """Ask for one reconcile pass. Synchronous (a pure specstar enqueue) so
        the API's timer thread can call it directly. Coalesces onto a pass
        already queued/running (the fleet-wide "one asker per window" is the
        API's ``ScanLease``; this covers an ask that overlaps a pass in flight).
        No retry budget: the next window asks again, which is the cadence the
        in-process sweep this replaced retried at — the queue's default (3)
        would run a failing reconcile four times back to back on the worker."""
        if self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build()) > 0:
            return
        self._job_rm.create(
            BlobGcJob(
                payload=BlobGcPayload(kind="reconcile"), partition_key=_PARTITION, max_retries=0
            )
        )

    # ── consume ──────────────────────────────────────────────────────
    def _handle(self, job) -> None:  # job: Resource[BlobGcJob]
        payload = job.data.payload
        assert isinstance(payload, BlobGcPayload)
        if payload.kind != "reconcile":
            logger.warning("blob-gc: unknown job kind %r", payload.kind)
            return
        # Prune BEFORE the pass, whatever the pass does: a reconcile that fails
        # every window would otherwise leave one FAILED row per window for as
        # long as the failure lasts.
        self._prune_finished()
        self._reconcile()

    def _prune_finished(self) -> None:
        """Hard-delete the earlier finished rows. A pass is asked for on a timer,
        forever, and every ask is a durable job row; nothing else reclaims job
        rows. The running job is PROCESSING (never matched here), so at most one
        finished row — COMPLETED or FAILED — survives between windows. Best
        effort: housekeeping must not fail the pass."""
        for r in self._job_rm.list_resources(QB["status"].in_(_DONE).build()):
            with contextlib.suppress(Exception):
                self._job_rm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]

    def _reconcile(self) -> None:
        started = time.monotonic()
        stats = self._spec.gc(mode="reconcile", t1=self._t1, t2=self._t2, now=self._now())
        logger.info(
            "blob-gc: reconcile complete quarantined=%d restored=%d deleted=%d live=%d",
            stats.quarantined,
            stats.restored,
            stats.deleted,
            stats.live,
        )
        if self._monitor is None:
            return
        self._monitor.record(
            {
                "kind": "blob_gc",
                "t": int(time.time() * 1000),  # wall-clock, for the summary's time window
                "mode": stats.mode,
                "quarantined": stats.quarantined,
                "restored": stats.restored,
                "deleted": stats.deleted,
                "live": stats.live,
                "scan_complete": stats.scan_complete,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
        if self._filestore is None:
            return
        # #407: on the same durable-maintenance cadence, snapshot the
        # WorkspaceFile cardinality (total rows / distinct workspaces / largest
        # workspace) so the ws_census trend shows whether the per-file model
        # grows unbounded — the archive-vs-keep signal. The handler runs on the
        # queue's consumer thread, which owns no event loop.
        census = asyncio.run(self._filestore.census())  # ty: ignore[unresolved-attribute]
        self._monitor.record({"kind": "ws_census", "t": int(time.time() * 1000), **census})

    # ── consumption machinery (mirrors graph / sanity / index / eval) ─
    @property
    def consuming(self) -> bool:
        return self._consuming

    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        self._ensure_consuming()

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    def _stop_consuming(self) -> None:
        with contextlib.suppress(RuntimeError):
            self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]
        self._consuming = False

    async def aclose(self) -> None:
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
