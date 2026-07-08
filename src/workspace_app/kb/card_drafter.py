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
import re
from pathlib import Path
from typing import Any

from .card_gen import CardDraft, DescriptionQuestionDraft, DocDigest, TermQuestionDraft
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
    """Digest a document via one ``ILlm`` pass into confident cards + the
    questions it raised instead of guessing (#377). Caps the number of cards per
    document so a pathological response can't flood review."""

    def __init__(
        self, llm: ILlm, *, prompt_template: str | None = None, max_cards: int = 30
    ) -> None:
        self._llm = llm
        self._template = prompt_template or _DEFAULT_PROMPT
        self._max_cards = max_cards

    def digest(self, *, doc_path: str, doc_text: str) -> DocDigest:
        # recover_reasoning (#494): a vLLM reasoning model can route the JSON reply
        # into the reasoning channel (max_tokens before </think>), leaving content
        # empty; recover it so the drafter parses the answer instead of silently
        # digesting nothing.
        raw = self._llm.collect(
            drafting_prompt(doc_text, doc_path=doc_path, template=self._template),
            recover_reasoning=True,
        )
        return _parse_digest(raw, max_cards=self._max_cards, doc_path=doc_path)


class NullCardDrafter:
    """The drafter used when no card-drafting LLM is configured: it proposes
    nothing. The generation feature stays mounted (routes exist, a run COMPLETEs
    with zero proposals / no questions) instead of 503-ing, so the FE degrades
    cleanly."""

    def digest(self, *, doc_path: str, doc_text: str) -> DocDigest:
        return DocDigest()


def _parse_digest(raw: str, *, max_cards: int, doc_path: str = "") -> DocDigest:
    """Parse the LLM's ``{"cards": [...], "term_questions": [...],
    "description_questions": [...]}`` response into a ``DocDigest`` (#377).
    Tolerant of reasoning ``<think>…</think>`` spans, code fences, and preamble
    braces (see ``_find_digest_object``); each section is parsed independently and
    malformed items are dropped. Any unrecoverable parse error yields an EMPTY
    digest — never raises.

    #494 observability: a digest that ends up empty is the exact silent failure
    that produced a green card-gen run with 0 cards + 0 questions. Distinguish and
    LOG the two ways it happens — no parseable object at all vs. a parsed-but-empty
    one — each tied to ``doc_path`` with a prefix of the raw reply, so the next
    occurrence is diagnosable instead of invisible."""
    obj = _find_digest_object(raw)
    if obj is None:
        logger.warning(
            "CardDrafter: no parseable digest JSON in the response "
            "(doc_path=%s raw_len=%d prefix=%r) — nothing to draft from",
            doc_path,
            len(raw),
            raw[:200],
        )
        return DocDigest()
    digest = DocDigest(
        cards=_parse_cards(obj.get("cards", []), max_n=max_cards),
        term_questions=_parse_term_questions(obj.get("term_questions", [])),
        description_questions=_parse_description_questions(obj.get("description_questions", [])),
    )
    if not (digest.cards or digest.term_questions or digest.description_questions):
        logger.warning(
            "CardDrafter: parsed the response but the digest is EMPTY (0 cards, 0 "
            "questions) (doc_path=%s raw_len=%d keys=%s) — the document yielded "
            "nothing, or the response shape was unexpected",
            doc_path,
            len(raw),
            sorted(obj.keys())[:10],
        )
    return digest


def _parse_cards(items: Any, *, max_n: int) -> list[CardDraft]:
    """Each card needs a non-empty string ``keys`` list and string
    title/body/snippet, else it's dropped. ``confident`` defaults to true when
    absent. Returns at most ``max_n``."""
    if not isinstance(items, list):
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


def _parse_term_questions(items: Any) -> list[TermQuestionDraft]:
    """Each term question needs a non-blank string ``term``; ``question`` defaults
    to empty. Malformed items are dropped."""
    if not isinstance(items, list):
        return []
    out: list[TermQuestionDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        term, question = item.get("term", ""), item.get("question", "")
        if not (isinstance(term, str) and isinstance(question, str) and term.strip()):
            continue
        out.append(TermQuestionDraft(term=term, question=question))
    return out


def _parse_description_questions(items: Any) -> list[DescriptionQuestionDraft]:
    """Each description question needs a non-blank string ``quote``; ``question``
    defaults to empty. Malformed items are dropped."""
    if not isinstance(items, list):
        return []
    out: list[DescriptionQuestionDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        quote, question = item.get("quote", ""), item.get("question", "")
        if not (isinstance(quote, str) and isinstance(question, str) and quote.strip()):
            continue
        out.append(DescriptionQuestionDraft(quote=quote, question=question))
    return out


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_DIGEST_KEYS = ("cards", "term_questions", "description_questions")


def _find_digest_object(raw: str) -> dict[str, Any] | None:
    """The digest object in a raw LLM reply, tolerant of the ways a reasoning
    model (#494) mangles it. Returns the parsed ``dict``, or ``None`` when nothing
    parseable is present.

    - **Strips well-formed** ``<think>…</think>`` spans first, so a SCRATCH object
      the model drafted inside its thinking never shadows the real answer after
      it. An UNTERMINATED ``<think>`` (the reply that landed in the reasoning
      channel and was recovered by ``collect``) is left intact so its JSON is
      still found.
    - **Scans every top-level balanced** ``{…}`` (string-aware, so a ``}`` inside
      a JSON string value doesn't close the object early — the old first-``{``
      extractor mis-handled both this and a preamble brace).
    - **Prefers a digest-shaped object** (one carrying ``cards`` /
      ``term_questions`` / ``description_questions``) over a stray ``{…}`` in
      prose; falls back to the first parseable object so a genuinely off-shape
      reply still parses to an (empty) digest the caller logs, rather than
      raising."""
    stripped = _THINK_RE.sub("", raw)
    texts = [stripped] if stripped == raw else [stripped, raw]
    fallback: dict[str, Any] | None = None
    for text in texts:
        for candidate in _balanced_objects(text):
            obj = _try_object(candidate)
            if obj is None:
                continue
            if any(k in obj for k in _DIGEST_KEYS):
                return obj
            if fallback is None:
                fallback = obj
    return fallback


def _balanced_objects(text: str) -> list[str]:
    """Every top-level, brace-balanced ``{…}`` substring of ``text``, left to
    right. String-aware: braces inside a double-quoted JSON string (respecting
    ``\\`` escapes) don't move the depth, so a ``}`` in a value can't close the
    object early."""
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, c in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out


def _try_object(candidate: str) -> dict[str, Any] | None:
    """``json.loads(candidate)`` if it is a JSON object, else ``None`` (a JSON
    array/scalar isn't a digest)."""
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None
