"""LlmCardDrafter (#175) — the production ``CardDrafter``: an ``ILlm`` reads one
document and drafts glossary cards from it as JSON, parsed defensively.

This is the LLM half of "自動 context card". It mirrors ``InsightExtractor``'s
shape (document text → prompt → ``llm.collect`` → tolerant JSON parse), but emits
``CardDraft``s (title / keys / body / confident / snippet) instead of insight
nodes. The classify intent is #205's ``→collections`` classify, run per document:
draft a card for each unknown term, list every alias as its own key, flag
confidence, and quote the supporting passage as provenance.

Parsing is deliberately tolerant — small models wrap JSON in ```json fences, add
preambles, or emit the wrong shape — so a bad response yields ``[]`` (never
raises) and malformed cards are dropped, matching the repo's structured-LLM
contract.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .card_gen import CardDraft
from .llm import ILlm

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (Path(__file__).parent / "prompts" / "card_drafting.md").read_text(
    encoding="utf-8"
)


def drafting_prompt(doc_text: str, *, doc_path: str = "", template: str | None = None) -> str:
    """The exact prompt one document is drafted with. ``str.replace`` (not
    ``.format``) so the JSON example in the template's braces is left alone."""
    return (template or _DEFAULT_PROMPT).replace("{path}", doc_path).replace("{document}", doc_text)


class LlmCardDrafter:
    """Draft glossary cards from a document via one ``ILlm`` pass. Caps the
    number of cards per document so a pathological response can't flood review."""

    def __init__(
        self, llm: ILlm, *, prompt_template: str | None = None, max_cards: int = 30
    ) -> None:
        self._llm = llm
        self._template = prompt_template or _DEFAULT_PROMPT
        self._max_cards = max_cards

    def draft(self, *, doc_path: str, doc_text: str) -> list[CardDraft]:
        raw = self._llm.collect(
            drafting_prompt(doc_text, doc_path=doc_path, template=self._template)
        )
        return _parse_drafts(raw, max_n=self._max_cards)


class NullCardDrafter:
    """The drafter used when no card-drafting LLM is configured: it proposes
    nothing. The generation feature stays mounted (routes exist, a run COMPLETEs
    with zero proposals) instead of 503-ing, so the FE degrades cleanly."""

    def draft(self, *, doc_path: str, doc_text: str) -> list[CardDraft]:
        return []


def _parse_drafts(raw: str, *, max_n: int) -> list[CardDraft]:
    """Parse the LLM's ``{"cards": [...]}`` response into ``CardDraft``s. Tolerant
    of leading prose / fenced blocks (peel the first ``{...}``); each card needs a
    non-empty string ``keys`` list and string title/body/snippet, else it's
    dropped. ``confident`` defaults to true when absent. Returns at most ``max_n``;
    ``[]`` for any unrecoverable parse error — never raises."""
    try:
        obj = json.loads(_extract_json_object(raw))
        items = obj.get("cards", [])
        if not isinstance(items, list):
            return []
    except (json.JSONDecodeError, ValueError, AttributeError):
        logger.warning("CardDrafter: LLM response was not parseable JSON")
        return []
    out: list[CardDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        keys = item.get("keys")
        if not (isinstance(keys, list) and keys and all(isinstance(k, str) for k in keys)):
            continue
        title, body, snippet = item.get("title", ""), item.get("body", ""), item.get("snippet", "")
        if not (isinstance(title, str) and isinstance(body, str) and isinstance(snippet, str)):
            continue
        usable = [k for k in keys if k.strip()]
        if not usable:
            continue
        out.append(
            CardDraft(
                keys=usable,
                title=title,
                body=body,
                confident=bool(item.get("confident", True)),
                snippet=snippet,
            )
        )
        if len(out) >= max_n:
            break
    return out


def _extract_json_object(raw: str) -> str:
    """Return the substring from the first ``{`` to its matching ``}``. Tolerates
    a ```json fence or a preamble around the object. (Mirrors the helper in
    ``insight_extractor`` — kept local so this lean drafter doesn't import the
    LlamaIndex-heavy module.)"""
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    raise ValueError("unterminated JSON object")
