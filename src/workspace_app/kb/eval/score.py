"""Pure metric core for the retrieval eval (#535).

Given a synthetic question's source chunk and the retriever's DEEP ranked result
(``search(depth=…)`` well past the top_k a user sees, so a buried chunk reads as
rank 37 rather than "absent"), locate where the source came back and turn that
into recall@k / MRR. Nothing here touches the DB, the LLM, or the job queue — it
is a function of ``(source, ranked passages)`` so it unit-tests without any of
them.

A ``hit`` is at the MERGED-PASSAGE grain the retriever actually returns: the
retriever coalesces adjacent chunks into a ``RetrievedPassage`` carrying every
constituent ``source_chunk_ids``, so the source chunk is found by membership in
that list, not by a chunk-for-chunk identity.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...resources.kb import RetrievedPassage


def passage_rank(source_chunk_id: str, ranked: list[RetrievedPassage]) -> int | None:
    """1-based rank of the first passage whose ``source_chunk_ids`` contains the
    source chunk, or ``None`` if it never appears in ``ranked``."""
    for rank, passage in enumerate(ranked, start=1):
        if source_chunk_id in passage.source_chunk_ids:
            return rank
    return None


def doc_rank(source_doc_id: str, ranked: list[RetrievedPassage]) -> int | None:
    """1-based rank of the first passage from ``source_doc_id`` — the looser,
    secondary signal: a question written from one chunk may be validly answered
    by a sibling chunk of the same doc, so doc-level recall is more forgiving
    than the chunk-level ``passage_rank``."""
    for rank, passage in enumerate(ranked, start=1):
        if passage.document_id == source_doc_id:
            return rank
    return None


def recall_at_k(ranks: list[int | None], k: int) -> float:
    """Fraction of eval items whose source came back at rank ``≤ k`` (a miss —
    ``None`` — never counts). ``0.0`` for an empty set."""
    if not ranks:
        return 0.0
    hits = sum(1 for r in ranks if r is not None and r <= k)
    return hits / len(ranks)


def mrr(ranks: list[int | None]) -> float:
    """Mean reciprocal rank of the source across eval items; a miss (``None``)
    contributes ``0``. ``0.0`` for an empty set."""
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks)


@dataclass(frozen=True)
class EvalMetrics:
    """The scored summary for one eval run (one collection, chunk- or doc-level).
    ``recall`` maps each requested ``k`` to recall@k; ``n`` is the number of eval
    items scored (kept questions, not the sample size — dropped questions never
    reach here)."""

    n: int
    recall: dict[int, float]
    mrr: float


def summarize(ranks: list[int | None], ks: tuple[int, ...] = (1, 3, 5, 10)) -> EvalMetrics:
    """Package per-item ranks into recall@each-k + MRR."""
    return EvalMetrics(n=len(ranks), recall={k: recall_at_k(ranks, k) for k in ks}, mrr=mrr(ranks))
