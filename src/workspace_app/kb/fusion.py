"""Rank fusion + diversification for hybrid retrieval.

`reciprocal_rank_fusion` combines several ranked lists (dense, sparse, per
multi-query variant) into one — the standard, parameter-light way to blend
heterogeneous scorers. `mmr` re-orders by relevance while penalizing redundancy.
Both are pure functions over keys / vectors so they're trivially testable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


def reciprocal_rank_fusion(ranked_lists: Sequence[Sequence[str]], *, k: int = 60) -> list[str]:
    """Fuse ranked lists of keys. Each key scores ``sum(1 / (k + rank))`` over
    the lists it appears in (rank is 1-based). Returns keys by descending score;
    ties broken by key for determinism."""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, key in enumerate(lst, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))


def mmr(
    candidates: Sequence[str],
    *,
    relevance: dict[str, float],
    similarity: Callable[[str, str], float],
    lambda_: float = 0.5,
    k: int | None = None,
) -> list[str]:
    """Maximal Marginal Relevance: greedily pick items balancing relevance with
    dissimilarity to already-picked items. `relevance[c]` is the query-relevance
    score; `similarity(a, b)` ∈ [0, 1]. Returns up to `k` reordered keys."""
    remaining = list(candidates)
    selected: list[str] = []
    limit = len(remaining) if k is None else k
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda c: lambda_ * relevance[c]
            - (1 - lambda_) * max((similarity(c, s) for s in selected), default=0.0),
        )
        selected.append(best)
        remaining.remove(best)
    return selected
