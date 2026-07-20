"""Synthetic question generation for the retrieval eval (#535).

Promptagator (Dai et al., *Promptagator: Few-shot Dense Retrieval From 8
Examples*, 2022): prompt an LLM to write a question a passage answers, then keep
only the questions the model itself judges answerable from that passage (a
round-trip quality filter). A garbled or too-generic question is DROPPED, not
scored as a retrieval miss — the filter guards against bad eval items, and it
judges QUESTION QUALITY, never retrievability (filtering on "was it retrieved"
would silently discard exactly the hard cases and inflate the score). The corpus
is the label; there are no human queries. Every call streams (``ILlm.collect``).
"""

from __future__ import annotations

from ..llm import ILlm

_GENERATE = (
    "You are shown a passage from a document. Write ONE specific, self-contained "
    "question that this passage directly answers. Output only the question.\n\n"
    "Passage:\n{passage}"
)

_ANSWERABLE = (
    "Passage:\n{passage}\n\n"
    "Question: {question}\n\n"
    "Can this question be answered specifically and unambiguously using ONLY the "
    "passage above? Answer with just 'yes' or 'no'."
)


def generate_question(llm: ILlm, passage_text: str) -> str:
    """Ask the model for one question the passage answers (stripped)."""
    return llm.collect(_GENERATE.format(passage=passage_text)).strip()


def is_answerable(llm: ILlm, question: str, passage_text: str) -> bool:
    """The round-trip quality filter: does the model judge ``question`` specifically
    answerable from ``passage_text``? Anything but a leading 'yes' is a drop."""
    reply = llm.collect(_ANSWERABLE.format(passage=passage_text, question=question))
    return reply.strip().lower().startswith("yes")


def make_question(llm: ILlm, passage_text: str) -> str | None:
    """Generate a question and keep it only if the model judges it answerable from
    the passage. ``None`` ⇒ dropped (excluded from the eval denominator, never a
    miss)."""
    question = generate_question(llm, passage_text)
    if not question:
        return None
    return question if is_answerable(llm, question, passage_text) else None
