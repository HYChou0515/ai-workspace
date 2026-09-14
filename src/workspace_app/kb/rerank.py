"""LLM rerank — let the model reorder the merged passages by how well they
answer the query (a cheap stand-in for a cross-encoder; use a cross-encoder
here instead when one is available). The LLM is injected; parsing is pure.
"""

from __future__ import annotations

import logging
import re

from ..resources.kb import RetrievedPassage
from .llm import ILlm, OnChunk

_INT = re.compile(r"\d+")

logger = logging.getLogger(__name__)


def _seen_by_reranker(p: RetrievedPassage, cap: int | None) -> str:
    """The text one candidate contributes to the listing: its context trimmed
    to `cap` chars around the hit, the bare hit at ``0``, everything at ``None``."""
    if not p.context_text or cap is None:
        return p.context_text or p.text
    if cap <= 0:
        return p.text
    ctx = p.context_text
    if len(ctx) <= cap:
        return ctx
    at = ctx.find(p.text)
    if at < 0:  # the hit is not a verbatim slice of the context — keep its head
        return ctx[:cap]
    # Centre the window on the hit; clamp to the ends.
    lead = max(0, (cap - len(p.text)) // 2)
    start = max(0, at - lead)
    end = min(len(ctx), start + cap)
    start = max(0, end - cap)
    return ctx[start:end]


def rerank_passages(
    llm: ILlm,
    query: str,
    passages: list[RetrievedPassage],
    *,
    on_progress: OnChunk | None = None,
    context_cap: int | None = None,
) -> list[RetrievedPassage]:
    """Reorder `passages` by the model's relevance ranking. The model is shown
    the numbered passages and replies with the order (most relevant first);
    passages it omits keep their original order at the end. Streams the model's
    work to `on_progress`.

    `context_cap` (plan-rag-context P6) bounds what each candidate contributes
    to the one listwise prompt: the neighbouring context (P2) is trimmed to at
    most that many chars, centred on the hit so the matched text is always in
    the window. `None` = uncapped; `0` = the bare hit. Without a bound the
    prompt grows ~4× (English) / ~27× (Chinese) at the default context width,
    and a reranker whose window is smaller truncates from the FRONT — the
    question — and its reply's numbers are then noise applied silently."""
    if not passages:
        logger.debug("rerank: no passages to rerank")
        return passages
    # plan-rag-context P2: rank the neighbouring CONTEXT (what the agent will
    # read), not the bare hit — a fragment that lacks the answer its neighbours
    # hold is exactly the passage expansion exists to rescue.
    listing = "\n".join(
        f"[{i + 1}] {_seen_by_reranker(p, context_cap)}" for i, p in enumerate(passages)
    )
    prompt = (
        "Rank the passages by how well they answer the question, most relevant "
        f"first. Reply with the passage numbers in order.\n\nQuestion: {query}\n\n{listing}"
    )

    order: list[int] = []
    seen: set[int] = set()
    for m in _INT.finditer(llm.collect(prompt, on_chunk=on_progress)):
        n = int(m.group())
        if 1 <= n <= len(passages) and n not in seen:
            seen.add(n)
            order.append(n)
    order.extend(n for n in range(1, len(passages) + 1) if n not in seen)
    logger.debug("rerank: llm reordered %d passages", len(passages))
    return [passages[n - 1] for n in order]
