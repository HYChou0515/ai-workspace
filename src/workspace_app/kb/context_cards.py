"""Context cards (#106) — a lightweight, deterministic glossary path beside
``kb_search``. A card maps several ``keys`` (term + surface forms) to a short
``body`` explanation; lookups are exact key membership over a derived,
normalised ``norm_keys`` — no embedding, no LLM, no agent loop.

This module owns the deterministic core: ``norm`` (the normalisation every
caller, internal or external, can replicate) and ``derive_norm_keys`` (the
indexed lookup surface materialised on write).
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from specstar import QB

from ..resources.kb import ContextCard

if TYPE_CHECKING:
    from specstar import SpecStar


def norm(s: str) -> str:
    """Deterministic key normalisation, shared by author-time derivation and
    every lookup. NFKC (fold full/half-width + compatibility) → ``casefold``
    (Unicode-aware lower) → collapse all whitespace runs to single spaces and
    strip. Intentionally simple so an external caller can reproduce it exactly.
    """
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def derive_norm_keys(keys: list[str]) -> list[str]:
    """The indexed lookup surface materialised on write: each key normalised,
    blanks dropped, deduped, and sorted so the stored list is deterministic
    (stable across re-authoring with the same keys in a different order)."""
    return sorted({n for k in keys if (n := norm(k))})


def lookup(spec: SpecStar, collection_id: str, terms: list[str]) -> dict[str, list[ContextCard]]:
    """Deterministic exact-key lookup, scoped to one collection. For each input
    term, return every card whose `norm_keys` contains `norm(term)` — exact
    element membership (so `"M4"` never matches a `"M40"` card). The result is
    keyed by the ORIGINAL input term (terms that miss map to an empty list)."""
    rm = spec.get_resource_manager(ContextCard)
    out: dict[str, list[ContextCard]] = {}
    for term in terms:
        q = (QB["collection_id"] == collection_id) & QB["norm_keys"].contains(norm(term))
        cards: list[ContextCard] = []
        for r in rm.list_resources(q.build()):
            data = r.data
            assert isinstance(data, ContextCard)  # narrow Struct|Unset for ty
            cards.append(data)
        out[term] = cards
    return out


def find_cards_by_key(
    spec: SpecStar, collection_id: str, term: str
) -> list[tuple[str, ContextCard]]:
    """Exact-key lookup like ``lookup``, but for ONE term and returning each hit
    paired with its resource id (#111). The id is what update / upsert callers target
    — a blind ``ContextCard`` struct carries none. Same ``norm`` + ``.contains`` exact
    membership, scoped to one collection."""
    rm = spec.get_resource_manager(ContextCard)
    q = (QB["collection_id"] == collection_id) & QB["norm_keys"].contains(norm(term))
    out: list[tuple[str, ContextCard]] = []
    for r in rm.list_resources(q.build()):
        data = r.data
        assert isinstance(data, ContextCard)  # narrow Struct|Unset for ty
        out.append((r.info.resource_id, data))  # ty: ignore[unresolved-attribute]
    return out


def build_vocab(cards: list[ContextCard]) -> dict[str, list[ContextCard]]:
    """Index a collection's cards by normalised key → the cards carrying it, for
    the internal `match(text)` pre-scan. One card lands under each of its
    `norm_keys`; the SAME object instance is shared across its keys so `match`
    can dedupe by identity."""
    vocab: dict[str, list[ContextCard]] = {}
    for card in cards:
        for k in card.norm_keys:
            vocab.setdefault(k, []).append(card)
    return vocab


def _word_ascii(ch: str) -> bool:
    """The 'word-continuation' class for boundary checks: ASCII letters, digits
    and underscore. CJK is deliberately NOT in it — Chinese has no word breaks,
    so a CJK key must be allowed to match mid-sentence."""
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _hits(nt: str, key: str) -> bool:
    """Whether `key` occurs in the normalised text `nt` at least once WITHOUT
    being glued into a longer ASCII word — rejecting `m4` inside `m40` or `etch`
    inside `foobar_etch`, while letting CJK keys match embedded. `str.find`
    returning -1 doubles as the "absent" check, so this is a single pass."""
    start = 0
    while (i := nt.find(key, start)) != -1:
        j = i + len(key)
        left_ok = i == 0 or not (_word_ascii(key[0]) and _word_ascii(nt[i - 1]))
        right_ok = j == len(nt) or not (_word_ascii(key[-1]) and _word_ascii(nt[j]))
        if left_ok and right_ok:
            return True
        start = i + 1
    return False


def match(text: str, vocab: dict[str, list[ContextCard]], *, cap: int = 10) -> list[ContextCard]:
    """Deterministically scan free `text` for any card key in `vocab` and return
    the matched cards (deduped, stable order, capped). Single pass: keys are
    sorted for a stable order, each tested with `_hits`; cards are deduped by
    identity (a card hit by several keys appears once)."""
    nt = norm(text)
    seen: set[int] = set()
    out: list[ContextCard] = []
    for k in sorted(k for k in vocab if _hits(nt, k)):
        for card in vocab[k]:
            if id(card) not in seen:
                seen.add(id(card))
                out.append(card)
    return out[:cap]


def cards_for_collections(spec: SpecStar, collection_ids: list[str]) -> list[ContextCard]:
    """Load every card across the given collections — the corpus the internal
    `match(text)` pre-scan builds its vocab from."""
    return [c for _, c in cards_with_ids_for_collections(spec, collection_ids)]


def cards_with_ids_for_collections(
    spec: SpecStar, collection_ids: list[str]
) -> list[tuple[str, ContextCard]]:
    """Like ``cards_for_collections`` but each card is paired with its resource id
    (#111) — so a matched card can be targeted for ``update_context_card``."""
    rm = spec.get_resource_manager(ContextCard)
    out: list[tuple[str, ContextCard]] = []
    for cid in collection_ids:
        for r in rm.list_resources((QB["collection_id"] == cid).build()):
            data = r.data
            assert isinstance(data, ContextCard)  # narrow Struct|Unset for ty
            out.append((r.info.resource_id, data))  # ty: ignore[unresolved-attribute]
    return out


def match_with_ids(
    text: str, pairs: list[tuple[str, ContextCard]], *, cap: int = 10
) -> list[tuple[str, ContextCard]]:
    """``match`` over (id, card) pairs, returning the matched cards WITH their ids
    (#111). Maps each matched card back to its id by object identity — the same
    identity ``match`` dedupes on."""
    id_by_identity = {id(c): rid for rid, c in pairs}
    cards = [c for _, c in pairs]
    return [(id_by_identity[id(c)], c) for c in match(text, build_vocab(cards), cap=cap)]


def card_context_block(cards: list[ContextCard], *, ids: list[str] | None = None) -> str:
    """Render matched cards as a labelled context block to inject into the KB
    chat agent's turn — empty string when nothing matched (so the caller adds
    nothing). Each entry leads with its term(s) so the model can attribute the
    explanation, and the preamble tells it these are authoritative (no search
    needed for them).

    When ``ids`` (a list parallel to ``cards``) is given, each entry's heading also
    carries ``[card_id: <rid>]`` (#111) so the agent's ``lookup_glossary`` output is a
    read-before-write surface — it can target a card for ``update_context_card``. The
    route-injection path passes no ids and stays id-less."""
    if not cards:
        return ""
    parts = [
        "Internal glossary entries relevant to the question — treat them as "
        "authoritative and do not search the knowledge base for these terms:"
    ]
    for i, c in enumerate(cards):
        label = c.title or (c.keys[0] if c.keys else "")
        aliases = ", ".join(c.keys)
        header = f"### {label}"
        if aliases and aliases != label:
            header += f" ({aliases})"
        if ids is not None:
            header += f" [card_id: {ids[i]}]"
        parts.append(f"{header}\n{c.body}")
    return "\n\n".join(parts)
