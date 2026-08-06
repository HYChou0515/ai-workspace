"""Durable queue type for the metric-extraction fan-out (#534).

One ``GraphJob`` JobType, a ``kind`` field discriminating the stages (like
``IndexJob`` #227 / ``EvalJob`` #535) — but SIMPLER: no finalize / CAS join,
because each doc's ``write_doc_graph`` is independent and idempotent, so there
is nothing to aggregate. The cronjob POSTs one ``kind="dispatch"``; it fans out
per opted-in collection (``split``), each of which fans out per batch of docs
(``batch``), and ends by queueing one ``kind="reconcile"`` — the pass that turns
the accumulated evidence into a vocabulary. ``partition_key`` is set at
``create()`` time.

The reconcile is a job KIND rather than a sweep so it inherits what the queue
already provides: retry, status, logging, and a worker pod that can consume it
independently of the extraction stages.
"""

from __future__ import annotations

import msgspec
from specstar.types import Job


class GraphJobPayload(msgspec.Struct):
    kind: str = "dispatch"  # dispatch | split | batch | reconcile
    collection_id: str = ""  # split | batch
    doc_ids: list[str] = []  # batch: the docs to (re)extract claims for
    # #697 — a `split` that must ask for the vocabulary once its batches are
    # queued. Only the HAND-PRESSED rebuild sets it: the weekly dispatch already
    # queues one reconcile for the whole run, and a reconcile is a whole-corpus
    # pass, so setting it per collection there would multiply that by the number
    # of opted-in collections. Absent ≡ off (no migration).
    reconcile_after: bool = False


class GraphJob(Job[GraphJobPayload]):
    """A queued graph-extraction job. ``partition_key`` = the collection for
    ``split`` (serialize a collection's own fan-out); ``None`` for ``batch`` jobs
    so they parallelize across the GPU fleet."""
