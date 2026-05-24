"""LLM rerank — let the model reorder the merged passages by how well they
answer the query (a cheap stand-in for a cross-encoder; use a cross-encoder
here instead when one is available). The LLM is injected; parsing is pure.
"""

from __future__ import annotations

import re

from ..resources.kb import RetrievedPassage
from .llm import Llm

_INT = re.compile(r"\d+")


def rerank_passages(
    llm: Llm, query: str, passages: list[RetrievedPassage]
) -> list[RetrievedPassage]:
    """Reorder `passages` by the model's relevance ranking. The model is shown
    the numbered passages and replies with the order (most relevant first);
    passages it omits keep their original order at the end."""
    if not passages:
        return passages
    listing = "\n".join(f"[{i + 1}] {p.text}" for i, p in enumerate(passages))
    prompt = (
        "Rank the passages by how well they answer the question, most relevant "
        f"first. Reply with the passage numbers in order.\n\nQuestion: {query}\n\n{listing}"
    )

    order: list[int] = []
    seen: set[int] = set()
    for m in _INT.finditer(llm.complete(prompt)):
        n = int(m.group())
        if 1 <= n <= len(passages) and n not in seen:
            seen.add(n)
            order.append(n)
    order.extend(n for n in range(1, len(passages) + 1) if n not in seen)
    return [passages[n - 1] for n in order]
