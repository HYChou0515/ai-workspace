"""All the documents → one card per term, its body derived from the evidence.

Pure computation. This module holds no ``SpecStar`` and imports nothing that
does, so "it cannot write to the store" is a property of the code rather than a
promise about it — which is what makes it safe to run a candidate criterion
against real documents.

The body is DERIVED, never authored-then-chosen. That is the whole point: no
document knows what the others say, so a definition written per document is one
facet, and picking a winner between facets throws the rest away. Recomputing
from the accumulated statements makes merging the normal case, and makes it
re-runnable — a new document changes the body by being added to the evidence,
not by overwriting an answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import msgspec

from ..context_cards import derive_norm_keys, norm
from ..llm import ILlm
from .extract import Statement, TermCard, extract

_SYNTHESIS = (Path(__file__).parent / "prompts" / "card_synthesis.md").read_text(encoding="utf-8")


class DocSource(msgspec.Struct, frozen=True):
    """One document, as plain text. No store, no collection, no permissions."""

    doc_id: str
    text: str


class Card(msgspec.Struct):
    """One term, everything the corpus states about it, and the body that says so."""

    keys: list[str]
    title: str
    body: str
    statements: list[Statement]
    sources: list[str]

    @property
    def norm_keys(self) -> list[str]:
        return derive_norm_keys(self.keys)


def built_in_synthesis_prompt() -> str:
    """The prompt as shipped, so a person can start from it rather than a blank
    file. Named apart from the ``synthesis_prompt`` PARAMETER below: same word,
    opposite direction, and shadowing it inside ``build_cards`` would make the
    default unreachable from the one place that needs it."""
    return _SYNTHESIS


class Build(msgspec.Struct, frozen=True):
    """The cards, and what the extraction cost to get them.

    ``offered`` counts the claims the model put forward; ``grounded`` counts the
    ones whose quote was really in the document. Everything that survives is
    grounded BY CONSTRUCTION, so without the pair the criterion always measures
    as perfect however much it invented — and inventing is the failure this whole
    design exists to remove.
    """

    cards: list[Card]
    offered: int
    grounded: int


def build(
    llm: ILlm,
    docs: list[DocSource],
    *,
    extract_prompt: str | None = None,
    synthesis_prompt: str | None = None,
) -> Build:
    """Read every document, group what they said by term, write one body each."""
    found: list[tuple[str, TermCard]] = []
    offered = grounded = 0
    for doc in docs:
        got = extract(llm, doc.text, prompt=extract_prompt)
        offered += got.proposed
        grounded += got.kept
        found.extend((doc.doc_id, card) for card in got.cards)
    cards = [_synthesise(llm, group, prompt=synthesis_prompt) for group in _group(found)]
    return Build(cards=cards, offered=offered, grounded=grounded)


def build_cards(
    llm: ILlm,
    docs: list[DocSource],
    *,
    extract_prompt: str | None = None,
    synthesis_prompt: str | None = None,
) -> list[Card]:
    """The cards alone, for callers with no use for the extraction counts."""
    return build(llm, docs, extract_prompt=extract_prompt, synthesis_prompt=synthesis_prompt).cards


def _group(found: list[tuple[str, TermCard]]) -> list[list[tuple[str, TermCard]]]:
    """Cards that share ANY normalised key describe the same term.

    Normalised the way `lookup_glossary` normalises, because two cards a reader
    cannot tell apart at lookup time are the same card as far as the reader is
    concerned.
    """
    groups: list[list[tuple[str, TermCard]]] = []
    keys: list[set[str]] = []
    for doc_id, card in found:
        mine = set(derive_norm_keys(card.keys))
        hit = next((i for i, ks in enumerate(keys) if ks & mine), None)
        if hit is None:
            groups.append([(doc_id, card)])
            keys.append(mine)
        else:
            groups[hit].append((doc_id, card))
            keys[hit] |= mine
    return groups


def _synthesise(llm: ILlm, group: list[tuple[str, TermCard]], *, prompt: str | None) -> Card:
    statements: list[Statement] = []
    seen: set[str] = set()
    for _, card in group:
        for statement in card.statements:
            if statement.text not in seen:
                seen.add(statement.text)
                statements.append(statement)
    keys: list[str] = []
    seen_keys: set[str] = set()
    for _, card in group:
        for key in card.keys:
            if (n := norm(key)) and n not in seen_keys:
                seen_keys.add(n)
                keys.append(key)
    term = group[0][1].term
    reply = llm.collect(
        (prompt or _SYNTHESIS)
        .replace("{term}", term)
        .replace("{statements}", "\n".join(f"- {s.text}  「{s.quote}」" for s in statements))
    )
    title, body = _read(reply)
    return Card(
        keys=keys,
        title=title or term,
        body=body,
        statements=statements,
        # Which documents this rests on, so a reader can go and check.
        sources=sorted({doc_id for doc_id, _ in group}),
    )


def _read(reply: str) -> tuple[str, str]:
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end < start:
        return "", ""
    try:
        # The slice starts at a "{", so whatever parses out of it is an object —
        # no isinstance guard here, it could never be false.
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return "", ""
    return str(data.get("title", "")).strip(), str(data.get("body", "")).strip()
