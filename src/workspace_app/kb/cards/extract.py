"""One document → what it STATES about the terms it uses.

Not a written definition. A document cannot know what the rest of the corpus
says, so any definition it authors is one facet, and a pipeline that then picks
a winner between facets throws the others away. What a document can supply is a
claim plus the sentence that makes it; those accumulate, and the definition is
derived from all of them later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msgspec

from ..llm import ILlm

_PROMPT = (Path(__file__).parent / "prompts" / "card_extraction.md").read_text(encoding="utf-8")


class Statement(msgspec.Struct, frozen=True):
    """One claim a document makes about a term, and the words that make it."""

    text: str
    quote: str


class TermCard(msgspec.Struct, frozen=True):
    """Everything ONE document had to say about ONE term."""

    term: str
    keys: list[str]
    statements: list[Statement]


def built_in_prompt() -> str:
    """The prompt as shipped, so a person can start from it rather than a blank
    file."""
    return _PROMPT


def extract_cards(llm: ILlm, text: str, *, prompt: str | None = None) -> list[TermCard]:
    """The terms this passage states something about."""
    template = prompt or _PROMPT
    reply = llm.collect(template.replace("{text}", text))
    return _parse(reply, text)


def _parse(reply: str, text: str) -> list[TermCard]:
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end < start:
        return []
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        return []
    return [card for raw in cards if (card := _card(raw, text)) is not None]


def _card(raw: Any, text: str) -> TermCard | None:
    if not isinstance(raw, dict):
        return None
    term = str(raw.get("term", "")).strip()
    if not term:
        return None
    keys = [str(k).strip() for k in raw.get("keys", []) if str(k).strip()]
    statements = [
        Statement(text=claim, quote=quote)
        for s in raw.get("statements", [])
        if isinstance(s, dict)
        and (claim := str(s.get("text", "")).strip())
        # The gate. A claim whose quote is not in the document is not a claim
        # the document made — and a model free to invent the sentence it is
        # quoting has given nobody anything to check, which would make the
        # whole requirement decoration.
        #
        # Non-empty FIRST: "" is a substring of every document, so the
        # membership test alone waves through a claim that quoted nothing —
        # which is the shape a model reaches for when it has a claim it cannot
        # source, i.e. exactly the one being kept out.
        and (quote := str(s.get("quote", "")).strip())
        and quote in text
    ]
    # A term with nothing left to say about it is not a card. Keeping it would
    # put a headword in the glossary that answers nothing when looked up.
    return TermCard(term=term, keys=keys or [term], statements=statements) if statements else None
