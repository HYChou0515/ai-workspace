"""BM25 — the sparse (keyword) half of hybrid retrieval. Pure in-process scorer
over a collection's chunk texts; returns chunk ids ranked by score, dropping
docs that match no query term (they only dilute the fused ranking).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Sequence

_WORD = re.compile(r"\w+")

logger = logging.getLogger(__name__)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens (``\\w+``) — the BM25 term surface. Exposed so the
    retriever's trigram corpus pre-narrowing (2a) fuzzy-matches on the SAME terms
    BM25 will score, keeping the candidate set a superset of BM25's scored docs."""
    return _WORD.findall(text.lower())


def bm25_rank(
    query: str, corpus: Sequence[tuple[str, str]], *, k1: float = 1.5, b: float = 0.75
) -> list[str]:
    """Rank ``(id, text)`` docs against ``query`` by BM25; ids by descending
    score, ties broken by id. Docs scoring 0 (no query term) are excluded."""
    q_terms = set(tokenize(query))
    if not q_terms or not corpus:
        logger.debug("bm25: no query terms or empty corpus, 0 ranked")
        return []
    docs = [(doc_id, tokenize(text)) for doc_id, text in corpus]
    n = len(docs)
    avgdl = sum(len(toks) for _, toks in docs) / n
    # document frequency per query term
    df = {t: sum(1 for _, toks in docs if t in toks) for t in q_terms}

    scores: dict[str, float] = {}
    for doc_id, toks in docs:
        tf = Counter(toks)
        score = 0.0
        for t in q_terms:
            if tf[t] == 0:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            denom = tf[t] + k1 * (1 - b + b * len(toks) / avgdl)
            score += idf * (tf[t] * (k1 + 1)) / denom
        if score > 0:
            scores[doc_id] = score
    logger.debug("bm25: ranked %d of %d docs", len(scores), n)
    return sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))
