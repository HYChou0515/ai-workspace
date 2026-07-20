"""Retrieval-eval resources (#535).

Three rows, mirroring the #227 index fan-out:

- ``EvalResult`` — the DURABLE per-(collection, run) baseline. Keeps history
  (keyed by ``run_label``), so a later run's recall@k / MRR can be compared to an
  earlier baseline. Viewable via specstar's auto ``GET /eval-result`` (multipod-
  safe — the number lives in the DB, not a pod's stdout).
- ``EvalRun`` — the fan-out JOIN state, one row per (collection, run), a mirror of
  ``IndexRun``: ``split`` seeds ``total``; each ``batch`` records its index in
  ``done`` / ``failed``; ``finalize`` runs once, gated by the CAS-claimed
  ``finalized``.
- ``EvalBatchStat`` — per-batch STAGED ranks, a mirror of ``IndexUnitText``:
  ``finalize`` rejoins all a run's batches into the ``EvalResult`` and deletes
  them. Transient.
"""

from __future__ import annotations

import hashlib

from msgspec import Struct, field


def eval_run_id(collection_id: str, run_label: str) -> str:
    """Deterministic, slash-free id for one (collection, run). ``collection_id``
    can hold a ``/`` (specstar ids can't), so the natural key is hashed."""
    return hashlib.sha256(f"{collection_id}\x00{run_label}".encode()).hexdigest()[:24]


def eval_batch_stat_id(collection_id: str, run_label: str, batch_index: int) -> str:
    """Per-batch staging id — the run id plus the batch index, so a run's batches
    list together and finalize can rejoin them."""
    return f"{eval_run_id(collection_id, run_label)}.b{batch_index}"


class EvalResult(Struct):  # → resource "eval-result"
    """One collection's retrieval-quality baseline for one run. ``recall_chunk`` /
    ``recall_doc`` map ``str(k)`` → recall@k; ``n_kept`` is the denominator (kept
    questions — dropped ones never counted). ``created_time`` is specstar's."""

    collection_id: str  # indexed — list a collection's runs
    run_label: str  # indexed — which run this belongs to (dispatch stamps it)
    seed: str = ""
    sample_size: int = 0
    n_generated: int = 0
    n_kept: int = 0
    n_dropped: int = 0
    recall_chunk: dict[str, float] = {}
    mrr_chunk: float = 0.0
    recall_doc: dict[str, float] = {}
    mrr_doc: float = 0.0


class EvalRun(Struct):  # → resource "eval-run"
    """#535 fan-out join state, one row per (collection, run) — mirror of
    ``IndexRun``. Correctness rests on CAS against the etag, never the queue."""

    collection_id: str
    run_label: str
    total: int  # number of batches the split seeded
    done: list[int] = field(default_factory=list)  # batch indices that finished OK
    failed: list[int] = field(default_factory=list)  # batch indices that gave up
    finalized: bool = False  # the exactly-once finalize gate (CAS-claimed)
    status: str = "running"  # running | done | error


class EvalBatchStat(Struct):  # → resource "eval-batch-stat"
    """#535 per-batch staged ranks — mirror of ``IndexUnitText``. ``chunk_ranks``
    / ``doc_ranks`` carry one entry per KEPT question (``None`` = the source never
    came back within the retriever's search depth). Finalize sums these across a
    run's batches into the ``EvalResult``, then deletes them. Transient."""

    collection_id: str
    run_label: str
    batch_index: int
    chunk_ranks: list[int | None] = field(default_factory=list)
    doc_ranks: list[int | None] = field(default_factory=list)
    n_kept: int = 0
    n_dropped: int = 0
