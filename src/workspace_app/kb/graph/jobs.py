"""Durable queue type for the metric-extraction fan-out (#534).

One ``GraphJob`` JobType, a ``kind`` field discriminating the stages (like
``IndexJob`` #227 / ``EvalJob`` #535) — but SIMPLER: no finalize / CAS join,
because each doc's ``write_doc_claims`` is independent and idempotent, so there
is nothing to aggregate. The cronjob POSTs one ``kind="dispatch"``; it fans out
per opted-in collection (``split``), each of which fans out per batch of docs
(``batch``). ``partition_key`` is set at ``create()`` time.
"""

from __future__ import annotations

import msgspec
from specstar.types import Job


class GraphJobPayload(msgspec.Struct):
    kind: str = "dispatch"  # dispatch | split | batch
    collection_id: str = ""  # split | batch
    doc_ids: list[str] = []  # batch: the docs to (re)extract claims for


class GraphJob(Job[GraphJobPayload]):
    """A queued graph-extraction job. ``partition_key`` = the collection for
    ``split`` (serialize a collection's own fan-out); ``None`` for ``batch`` jobs
    so they parallelize across the GPU fleet."""
