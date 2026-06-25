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
    """One step of indexing the SourceDoc ``doc_id`` (in ``collection_id``).

    ``kind`` (#227) dispatches the fan-out in the handler:

    - ``split`` (default): plan the doc — index it whole (small / multi-parser /
      no-parser), or fan it out into many ``process`` jobs (one per unit batch)
      when a single parser reports many units, so no job exceeds the broker's
      consumer-ack timeout.
    - ``process``: parse + chunk + embed the half-open unit batch
      ``[unit_start, unit_end)`` (``batch_index`` identifies it in the
      :class:`~workspace_app.resources.kb.IndexRun` join state).
    - ``finalize``: once every batch is accounted for, rejoin the staged text
      into ``SourceDoc.text``, flip status, and run the wiki hook — exactly once.
    """

    doc_id: str
    collection_id: str
    kind: str = "split"  # split | process | finalize
    unit_start: int = 0
    unit_end: int = 0
    batch_index: int = 0


class IndexJob(Job[IndexJobPayload]):
    """A queued index run. ``partition_key`` is the doc id (#134), so the same
    doc never indexes concurrently while different docs still parallelize across
    workers; ``status`` reflects PENDING / PROCESSING / COMPLETED / FAILED."""
