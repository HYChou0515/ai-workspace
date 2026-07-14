"""Defect library (#513) — a scope-aware view over context cards.

A defect entry is an ordinary :class:`ContextCard` whose keys are
*scope-qualified* (``<scope>|<code>`` — the scope being a machine, station-type
or layer identifier). :func:`resolve` walks a caller-supplied scope chain from
most-specific to broadest and returns the first card that carries the code at
that level, so shared morphology knowledge lives once at type/layer level while
an individual machine can override it. No new resource — just the exact-key
membership that context cards already provide, keyed by convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .context_cards import cards_with_ids_for_collections, find_cards_by_key, norm

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..resources.kb import ContextCard


def scope_key(scope: str, code: str) -> str:
    """The stored/queried key for a defect ``code`` at a given ``scope``."""
    return f"{scope}|{code}"


def resolve(
    spec: SpecStar, collection_id: str, code: str, scope_chain: list[str]
) -> tuple[str, ContextCard] | None:
    """Most-specific-wins lookup of a defect ``code`` within a scope chain.

    ``scope_chain`` is ordered specific → broad (e.g. ``[machine, type, layer]``).
    A card keyed by the bare ``code`` (no scope) is the broadest fallback, tried
    after the whole chain. Returns the first ``(resource_id, card)`` found at the
    most-specific level that carries the code, or ``None`` if nothing matches.
    """
    for scope in scope_chain:
        hits = find_cards_by_key(spec, collection_id, scope_key(scope, code))
        if hits:
            return hits[0]
    hits = find_cards_by_key(spec, collection_id, code)  # global bare-code card
    return hits[0] if hits else None


def candidates_in_scope(
    spec: SpecStar, collection_id: str, scope_chain: list[str]
) -> list[tuple[str, ContextCard]]:
    """Every defect entry visible at a station — the classification candidate set
    for use case B (#513 P3).

    Enumerates each defect ``code`` carried by a card at a scope in ``scope_chain``
    (a ``<scope>|<code>`` key whose scope is in the chain) or by a bare global key,
    then resolves each code most-specific-wins via :func:`resolve`. So a machine
    card overriding a shared type card for the same code appears ONCE (the machine
    card), and a card keyed only for an off-chain station is excluded. Returns
    ``(resource_id, card)`` deduped by card, ordered by code."""
    norm_scopes = {norm(s) for s in scope_chain}
    codes: set[str] = set()
    for _, card in cards_with_ids_for_collections(spec, [collection_id]):
        for k in card.norm_keys:
            prefix, sep, code = k.partition("|")
            if sep:
                if prefix in norm_scopes:  # a scoped key at an in-chain scope
                    codes.add(code)
            else:
                codes.add(prefix)  # a bare-code global candidate
    out: list[tuple[str, ContextCard]] = []
    seen: set[str] = set()
    for code in sorted(codes):
        got = resolve(spec, collection_id, code, scope_chain)
        if got is not None and got[0] not in seen:
            seen.add(got[0])
            out.append(got)
    return out


def _entry_label(card: ContextCard) -> str:
    """A defect entry's display name for the VLM prompt — its title, else the
    bare code parsed off its first scope-qualified key (``etch|M4`` → ``M4``)."""
    if card.title:
        return card.title
    if card.keys:
        _, sep, code = card.keys[0].partition("|")
        return code if sep else card.keys[0]
    return "(unnamed)"


def build_classification_prompt(candidates: list[ContextCard], context: str | None = None) -> str:
    """The VLM question for use case B (#513 P3): look at the defect image and
    rank it against a FIXED shortlist of in-scope defect entries, citing each
    entry's morphology / judgement criteria (the text is the anchor — a reference
    image is optional). ``context`` is the user's free-text note (station,
    conditions), included only when given. Pure — the caller supplies the image
    to the describer alongside this question."""
    lines = [
        "You are classifying a defect image against a fixed list of known defect "
        "types. Look at the image and decide which type(s) it best matches.",
        "",
        "Candidate defect types (name — morphology / judgement criteria):",
    ]
    for i, card in enumerate(candidates, 1):
        lines.append(f"\n{i}. {_entry_label(card)}\n{card.body}")
    if context:
        lines.append(f"\nUser context: {context}")
    lines.append(
        "\nRank the candidates from most to least likely for THIS image. For each, "
        "cite the specific morphology that supports or rules it out. If none fit "
        "well, say so rather than forcing a match."
    )
    return "\n".join(lines)
