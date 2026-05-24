"""Query transformation for retrieval — multi-query expansion (and, later,
HyDE). The LLM is injected; the prompt + response parsing is pure and tested.
"""

from __future__ import annotations

import re

from .llm import Llm

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")  # leading bullet / "1." / "2)"


def expand_queries(llm: Llm, query: str, *, n: int = 3) -> list[str]:
    """Original query plus up to `n` LLM-generated alternative phrasings
    (deduped, original always first)."""
    prompt = (
        f"Rewrite the user's search query as {n} alternative phrasings that would "
        f"help retrieve relevant documents. One per line, no numbering.\n\nQuery: {query}"
    )
    out = [query]
    for line in llm.complete(prompt).splitlines():
        variant = _BULLET.sub("", line).strip()
        if variant and variant not in out:
            out.append(variant)
        if len(out) == n + 1:
            break
    return out
