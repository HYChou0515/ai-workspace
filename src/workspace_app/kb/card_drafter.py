"""LlmCardDrafter (#175) — the production ``CardDrafter``: an ``ILlm`` reads one
document and records what it STATES about the terms it uses.

Not definitions. A document cannot see what the rest of the corpus says about a
term, so a definition it authors is one facet — and the pipeline that then
picked between facets could only lose the others. What it supplies is a claim
plus the sentence that makes it; those accumulate on the card and the definition
is written from all of them (docs/plan-context-card-evidence.md).

The parse and the QUOTE GATE are ``kb.cards.extract``'s, shared with the offline
tuning loop. One parser, one criterion: a second copy is how the criterion a
person tuned and the criterion that ships drift apart.

Parsing is deliberately tolerant — small models wrap JSON in ```json fences, add
preambles, or emit the wrong shape — so a bad response yields an empty digest
(never raises) and malformed cards are dropped.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .card_gen import DocDigest
from .cards.extract import parse_cards
from .llm import ILlm

logger = logging.getLogger(__name__)

#: The criterion the corpus owner tuned offline. The SAME file the tuning loop
#: reads, so what was measured is what ships.
_DEFAULT_PROMPT = (Path(__file__).parent / "cards" / "prompts" / "card_extraction.md").read_text(
    encoding="utf-8"
)


def drafting_prompt(doc_text: str, *, doc_path: str = "", template: str | None = None) -> str:
    """The exact prompt one document is drafted with. ``str.replace`` (not
    ``.format``) so the JSON example in the template's braces is left alone.

    ``{text}`` is the placeholder the tuned prompt uses; ``{document}`` and
    ``{path}`` are honoured too so a hand-written template keeps working.
    """
    return (
        (template or _DEFAULT_PROMPT)
        .replace("{path}", doc_path)
        .replace("{document}", doc_text)
        .replace("{text}", doc_text)
    )


class LlmCardDrafter:
    """Digest a document via one ``ILlm`` pass into the terms it states something
    about. Caps the number per document so a pathological response can't flood
    review."""

    def __init__(
        self, llm: ILlm, *, prompt_template: str | None = None, max_cards: int = 30
    ) -> None:
        self._llm = llm
        self._template = prompt_template or _DEFAULT_PROMPT
        self._max_cards = max_cards

    def digest(self, *, doc_path: str, doc_text: str, collection_id: str = "") -> DocDigest:
        # collection_id is unused here: the one-shot drafter sees only the document
        # (that's exactly the open loop #506's agentic drafter closes). Accepted so
        # both drafters satisfy the one CardDrafter signature the coordinator calls.
        # recover_reasoning (#494): a vLLM reasoning model can route the JSON reply
        # into the reasoning channel (max_tokens before </think>), leaving content
        # empty; recover it so the drafter parses the answer instead of silently
        # digesting nothing.
        raw = self._llm.collect(
            drafting_prompt(doc_text, doc_path=doc_path, template=self._template),
            recover_reasoning=True,
        )
        return _parse_digest(raw, doc_text, max_cards=self._max_cards, doc_path=doc_path)


class NullCardDrafter:
    """The drafter used when no card-drafting LLM is configured: it proposes
    nothing. The generation feature stays mounted (routes exist, a run COMPLETEs
    with zero proposals / no questions) instead of 503-ing, so the FE degrades
    cleanly."""

    def digest(self, *, doc_path: str, doc_text: str, collection_id: str = "") -> DocDigest:
        return DocDigest()


def _parse_digest(raw: str, doc_text: str, *, max_cards: int, doc_path: str = "") -> DocDigest:
    """Parse the model's reply into a ``DocDigest``.

    The cards — and the quote gate that drops any claim whose quote is not
    verbatim in ``doc_text`` — come from ``kb.cards.extract``, the same code the
    offline tuning loop scores. No questions are ever produced: the drafter used
    to ask about terms it could not define, and removing that branch without
    adding silence would have left only guessing (see the plan).

    #494 observability: a digest that ends up empty is the exact silent failure
    that produced a green card-gen run with 0 cards. Distinguish and LOG the two
    ways it happens — nothing parseable at all vs. parsed-but-empty — each tied to
    ``doc_path`` with a prefix of the raw reply.
    """
    got = parse_cards(raw, doc_text)
    if got.proposed == 0 and not got.cards:
        logger.warning(
            "CardDrafter: no usable cards in the response "
            "(doc_path=%s raw_len=%d prefix=%r) — nothing to draft from",
            doc_path,
            len(raw),
            raw[:200],
        )
    elif got.proposed and not got.cards:
        logger.warning(
            "CardDrafter: every claim was DROPPED by the quote gate "
            "(doc_path=%s offered=%d) — the model is not quoting the document",
            doc_path,
            got.proposed,
        )
    return DocDigest(cards=list(got.cards[:max_cards]))
