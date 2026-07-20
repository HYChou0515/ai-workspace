"""Durable queue type for the retrieval-eval fan-out (#535).

One ``EvalJob`` JobType, a ``kind`` field discriminating the four stages — the
same shape as ``IndexJob`` (#227). The cronjob POSTs a single ``kind="dispatch"``
job; that fans out per-collection (``split``), each of which fans out per-batch
(``batch``), joined by ``EvalRun`` CAS into a ``finalize``. ``partition_key`` is
set at ``create()`` time, not on the payload — ``batch`` jobs use ``None`` to
parallelize across the GPU fleet (correctness rests on the CAS join, never the
queue).
"""

from __future__ import annotations

import msgspec
from specstar.types import Job


class EvalJobPayload(msgspec.Struct):
    kind: str = "dispatch"  # dispatch | split | batch | finalize
    run_label: str = ""  # the run these jobs belong to (dispatch stamps it)
    collection_id: str = ""  # split | batch | finalize
    seed: str = ""  # sampling seed (dispatch → split)
    sample_size: int = 0  # 0 ⇒ the coordinator default (dispatch → split)
    batch_index: int = 0  # batch
    chunk_ids: list[str] = []  # batch: the sampled chunk ids this batch scores


class EvalJob(Job[EvalJobPayload]):
    """A queued eval job. ``partition_key`` = the collection for split/finalize
    (serialize a collection's own stages); ``None`` for batch jobs (parallelize)."""
