"""Durable indexing queue types (#82).

KB indexing (chunk + embed) used to run on the request path via
``asyncio.to_thread`` on the shared default thread pool — so a slow synchronous
embedder HTTP call held a thread for seconds and, under load, starved the
request path (the app froze while a doc indexed). These types move indexing
onto a specstar job queue, exactly like wiki maintenance (#59):

  - ``IndexJobPayload`` — one SourceDoc to chunk + embed.
  - ``IndexJob`` — the ``specstar.Job`` carrying it. ``partition_key`` is set to
    the doc id (#134): specstar serialises only jobs that share a key, so the
    same doc never indexes twice at once (no torn chunk set), but *different*
    docs carry different keys and any consumer claims them freely. Embedder load
    is bounded by the number of worker pods — each pod runs a single consumer
    doing one job at a time, and you add pods (k8s replicas / HPA) to go faster
    — rather than by serialising a *collection's* docs.

The job is enqueued with ``rm.create(...)`` and consumed by
``rm.start_consume(block=False)`` in ``IndexCoordinator`` — its handler runs in
the consumer's own thread, OFF the request-serving event loop, so indexing can
never again starve other requests. Durable (survives restart) + cross-pod.
"""

from __future__ import annotations

import msgspec
from specstar.types import Job


class IndexJobPayload(msgspec.Struct):
    """One unit of indexing: chunk + embed the SourceDoc ``doc_id`` (which lives
    in ``collection_id``)."""

    doc_id: str
    collection_id: str


class IndexJob(Job[IndexJobPayload]):
    """A queued index run. ``partition_key`` is the doc id (#134), so the same
    doc never indexes concurrently while different docs still parallelize across
    workers; ``status`` reflects PENDING / PROCESSING / COMPLETED / FAILED."""
