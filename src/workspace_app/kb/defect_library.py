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

from .context_cards import find_cards_by_key

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
