"""Answer formatting (#377) — turning a human's raw answer to a term question
into a clean context-card ``(title, body)``. This is NOT hallucination: the
knowledge is the human's; the LLM only tidies it into a definition. A verbatim
fallback keeps the feature working with no model wired.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from .llm import ILlm

logger = logging.getLogger(__name__)

_FORMAT_PROMPT = (
    'A human has answered the question "what does the term below mean?". Turn '
    "their answer into a glossary card: a short display title and a concise "
    "definition body (1–4 sentences of Markdown). Do NOT add facts they did not "
    "give — only tidy their wording. Output ONLY this JSON object, no prose, no "
    'code fence:\n\n{"title": "...", "body": "..."}\n\nTerm: {term}\n\nAnswer:\n{answer}'
)


class AnswerCardFormatter(Protocol):
    """The seam that shapes a human's answer into a card. Production wraps an
    ``ILlm`` + a formatting prompt; tests inject a fake; the verbatim formatter is
    the no-LLM fallback."""

    def format(self, *, term: str, answer: str) -> tuple[str, str]:
        """Return ``(title, body)`` for a card built from the answer to
        "what is ``term``?"."""
        ...


class VerbatimAnswerFormatter:
    """The no-LLM fallback: the term is the title, the answer is the body — the
    human's words verbatim, untouched."""

    def format(self, *, term: str, answer: str) -> tuple[str, str]:
        return term, answer


class LlmAnswerCardFormatter:
    """Tidy a human's answer into a card ``(title, body)`` via one ``ILlm`` pass.
    Tolerant like the card drafter: a response that isn't a JSON object with
    non-empty string ``title`` + ``body`` falls back to the verbatim ``(term,
    answer)`` — the human's own words, never invented."""

    def __init__(self, llm: ILlm) -> None:
        self._llm = llm

    def format(self, *, term: str, answer: str) -> tuple[str, str]:
        prompt = _FORMAT_PROMPT.replace("{term}", term).replace("{answer}", answer)
        raw = self._llm.collect(prompt)
        try:
            obj = json.loads(_first_json_object(raw))
            title, body = obj.get("title"), obj.get("body")
        except (json.JSONDecodeError, ValueError, AttributeError):
            logger.warning("AnswerFormatter: LLM response was not parseable JSON")
            return term, answer
        if isinstance(title, str) and isinstance(body, str) and title.strip() and body.strip():
            return title, body
        return term, answer  # missing / wrong-typed fields — keep the human's words


def _first_json_object(raw: str) -> str:
    """The substring from the first ``{`` to its matching ``}`` — tolerates a
    fence or preamble around the object (same shape as the card drafter's helper)."""
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    raise ValueError("unterminated JSON object")
