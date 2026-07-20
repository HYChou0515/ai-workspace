"""The batch scoring core (#535) — the heart of the ``batch`` fan-out job.

For each sampled chunk: make a synthetic question (P2, dropped items excluded),
run it through the retriever (injected as a plain ``search`` callable so this
stays testable without a real Retriever / embedder / DB), and record where the
source came back — chunk-level and doc-level (P1). Pure over its injected seams;
the coordinator supplies the real ``ILlm`` + a ``search`` bound to the live
retriever at the right depth.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...resources.kb import RetrievedPassage
from ..llm import ILlm
from .generate import make_question
from .score import doc_rank, passage_rank, summarize

# (query, collection_ids) -> the retriever's DEEP ranked passages.
Search = Callable[[str, list[str]], list[RetrievedPassage]]


@dataclass(frozen=True)
class BatchResult:
    """One batch's per-item ranks (``None`` = a kept question whose source never
    came back within the search depth — a miss) plus the kept/dropped tallies."""

    chunk_ranks: list[int | None]
    doc_ranks: list[int | None]
    n_kept: int
    n_dropped: int


def score_batch(
    llm: ILlm,
    search: Search,
    collection_ids: list[str],
    chunks: list[tuple[str, str, str]],
) -> BatchResult:
    """Score a batch of ``(chunk_id, doc_id, text)``. A question the round-trip
    filter rejects is dropped (never a miss); a kept question whose source is
    absent from the ranked result is a ``None`` rank."""
    chunk_ranks: list[int | None] = []
    doc_ranks: list[int | None] = []
    dropped = 0
    for chunk_id, doc_id, text in chunks:
        question = make_question(llm, text)
        if question is None:
            dropped += 1
            continue
        ranked = search(question, collection_ids)
        chunk_ranks.append(passage_rank(chunk_id, ranked))
        doc_ranks.append(doc_rank(doc_id, ranked))
    return BatchResult(
        chunk_ranks=chunk_ranks,
        doc_ranks=doc_ranks,
        n_kept=len(chunk_ranks),
        n_dropped=dropped,
    )


@dataclass(frozen=True)
class Aggregated:
    """A whole run's metrics, ready to stamp onto an ``EvalResult``. Recall dicts
    are keyed by ``str(k)`` (JSON-friendly), chunk-level and doc-level."""

    n_kept: int
    n_dropped: int
    recall_chunk: dict[str, float]
    mrr_chunk: float
    recall_doc: dict[str, float]
    mrr_doc: float


def aggregate(results: list[BatchResult], ks: tuple[int, ...] = (1, 3, 5, 10)) -> Aggregated:
    """Concatenate every batch's per-item ranks and summarize both grains — what
    ``finalize`` writes after rejoining a run's ``EvalBatchStat`` rows."""
    chunk_ranks = [r for b in results for r in b.chunk_ranks]
    doc_ranks = [r for b in results for r in b.doc_ranks]
    chunk = summarize(chunk_ranks, ks)
    doc = summarize(doc_ranks, ks)
    return Aggregated(
        n_kept=sum(b.n_kept for b in results),
        n_dropped=sum(b.n_dropped for b in results),
        recall_chunk={str(k): v for k, v in chunk.recall.items()},
        mrr_chunk=chunk.mrr,
        recall_doc={str(k): v for k, v in doc.recall.items()},
        mrr_doc=doc.mrr,
    )
