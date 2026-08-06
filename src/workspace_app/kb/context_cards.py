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
from collections.abc import Callable
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


def _live(collection_id: str):
    """Every card read goes through this: one collection, tombstones excluded.

    ``list_resources`` returns soft-deleted rows, so without the ``is_deleted``
    predicate a deleted card keeps answering — quoted back as an authoritative
    definition, and handed to an upsert as a target whose ``update`` then raises.
    specstar carries ``is_deleted`` as a meta field, so this is a predicate the
    backend applies, not a filter each caller has to remember to reapply."""
    return (QB["collection_id"] == collection_id) & (QB.is_deleted() == False)  # noqa: E712


def lookup(spec: SpecStar, collection_id: str, terms: list[str]) -> dict[str, list[ContextCard]]:
    """Deterministic exact-key lookup, scoped to one collection. For each input
    term, return every card whose `norm_keys` contains `norm(term)` — exact
    element membership (so `"M4"` never matches a `"M40"` card). The result is
    keyed by the ORIGINAL input term (terms that miss map to an empty list)."""
    rm = spec.get_resource_manager(ContextCard)
    out: dict[str, list[ContextCard]] = {}
    for term in terms:
        q = _live(collection_id) & QB["norm_keys"].contains(norm(term))
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
    q = _live(collection_id) & QB["norm_keys"].contains(norm(term))
    out: list[tuple[str, ContextCard]] = []
    for r in rm.list_resources(q.build()):
        data = r.data
        assert isinstance(data, ContextCard)  # narrow Struct|Unset for ty
        out.append((r.info.resource_id, data))  # ty: ignore[unresolved-attribute]
    return out


def effective_keys(keys: list[str], title: str) -> list[str]:
    """The keys a card is actually authored under: the given ``keys``, or the ``title``
    when none of them normalises to a usable lookup key — so an entry someone filed
    with only a name stays findable instead of becoming unreachable.

    One definition for every surface that authors a card (create / update / upsert /
    collection import). It used to be copy-pasted at each, which is how the collection
    importer ended up carrying this half of the rule and not the other half (#701)."""
    eff = list(keys)
    if not derive_norm_keys(eff) and title.strip():
        return [title]
    return eff


def pick_upsert_target(
    candidates: Callable[[str], list[tuple[str, ContextCard]]], keys: list[str]
) -> tuple[str, ContextCard, int] | None:
    """The card an upsert of ``keys`` would OVERWRITE, as ``(card_id, card,
    sharing_count)`` — or ``None`` when no key names one yet, i.e. this upsert creates.

    The FIRST key with a hit wins and its first card is the target. ``sharing_count``
    is how many cards carry that matched key: ``> 1`` means the term is ambiguous
    (it names several cards) and only the first is overwritten, which a review
    surface can then say out loud rather than letting it pass silently.

    This is the "which card does this become" decision itself, shared rather than
    mirrored (#701). Every caller has to reach the same answer — a preview that
    resolved differently from the commit it previews would show a diff against a
    card the commit does not touch — and copies drift: the collection importer's
    copy never had this half at all, so importing the same archive twice grew a
    second card instead of updating the first.

    ``candidates`` is where the cards come from, and it is the ONLY thing callers
    vary: one query per key for a single authoring action, a pre-loaded snapshot for
    a batch restore that must not see its own writes. Splitting the SOURCE from the
    RULE is what lets a batch caller stay linear without owning a second copy of the
    rule. What stays per-surface beyond that is only the WRITE — who it is stamped
    as, and whether a stale read is guarded — never the target.
    """
    for key in keys:
        hits = candidates(key)
        if hits:
            return hits[0][0], hits[0][1], len(hits)
    return None


def resolve_upsert_target(
    spec: SpecStar, collection_id: str, keys: list[str]
) -> tuple[str, ContextCard, int] | None:
    """``pick_upsert_target`` against live storage — one indexed query per key.

    The right source for a SINGLE authoring action, where reading the current state is
    the point. A batch restore wants ``CardSnapshot`` instead: repeating this per item
    is a query per key per card, and it also lets the batch see cards it just wrote."""
    return pick_upsert_target(lambda key: find_cards_by_key(spec, collection_id, key), keys)


class CardSnapshot:
    """A collection's cards as they stood at ONE moment, for resolving a whole batch.

    Two problems, one object. **Correctness**: resolving each item against live storage
    means a batch sees its own writes, so two manifest entries sharing a key collapse
    — the second finds the first and overwrites it, and a plain export→import quietly
    loses a card. A snapshot cannot grow mid-restore, so entries can only ever pair
    with cards that predate the batch. **Cost**: one load instead of a query per key
    per item, which is the difference between linear and quadratic on a collection
    whose cards a generator wrote by the thousand.

    ``claim`` is what keeps the pairing one-to-one: an existing card taken by an
    earlier entry is withdrawn from the pool, so a later entry under the same key
    pairs with the NEXT card carrying it, or creates. Without that, N entries sharing
    a key would all land on the same card and N-1 of them would vanish.
    """

    def __init__(self, pairs: list[tuple[str, ContextCard]]) -> None:
        self._by_key: dict[str, list[tuple[str, ContextCard]]] = {}
        for rid, card in pairs:
            for nk in card.norm_keys:
                self._by_key.setdefault(nk, []).append((rid, card))
        self._claimed: set[str] = set()

    def candidates(self, key: str) -> list[tuple[str, ContextCard]]:
        """The unclaimed cards carrying ``key`` — the ``pick_upsert_target`` source."""
        return [(rid, c) for rid, c in self._by_key.get(norm(key), []) if rid not in self._claimed]

    def claim(self, card_id: str) -> None:
        """Withdraw a card from the pool: this batch has already paired with it."""
        self._claimed.add(card_id)


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


def mentions(nt: str, key: str) -> bool:
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
    sorted for a stable order, each tested with `mentions`; cards are deduped by
    identity (a card hit by several keys appears once)."""
    nt = norm(text)
    seen: set[int] = set()
    out: list[ContextCard] = []
    for k in sorted(k for k in vocab if mentions(nt, k)):
        for card in vocab[k]:
            if id(card) not in seen:
                seen.add(id(card))
                out.append(card)
    return out[:cap]


def cards_with_ids_for_collections(
    spec: SpecStar, collection_ids: list[str]
) -> list[tuple[str, ContextCard]]:
    """Load every card across the given collections — the corpus the internal
    `match(text)` pre-scan builds its vocab from — each paired with its resource id
    (#111), so a matched card can be targeted for ``update_context_card``."""
    rm = spec.get_resource_manager(ContextCard)
    out: list[tuple[str, ContextCard]] = []
    for cid in collection_ids:
        for r in rm.list_resources(_live(cid).build()):
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


# A glossary entry is a definition read in passing, not a document: long enough
# for a paragraph or two of curated meaning, short enough that a whole block of
# them still leaves room for the passages they annotate.
CARD_BODY_MAX_CHARS = 2_000
CARD_BLOCK_MAX_CHARS = 20_000


def _capped_body(body: str) -> str:
    """One card's definition, trimmed at a sentence-agnostic boundary with a
    marker — the term stays defined, it just stops being a document."""
    if len(body) <= CARD_BODY_MAX_CHARS:
        return body
    return body[:CARD_BODY_MAX_CHARS] + "\n[definition truncated — open the card to read the rest]"


def _entries(cards: list[ContextCard], ids: list[str] | None) -> list[str]:
    """One rendered entry per card, in order.

    ``ids`` marks the READ-BEFORE-WRITE surface (``lookup_glossary``), and there
    the body is rendered IN FULL. ``update_context_card``'s ``expected_body``
    guard is an exact string compare, so handing back a trimmed body would make
    every card over the budget permanently un-updatable — and its conflict error
    says "re-read it and retry", which would loop forever. Truncation belongs to
    the path where the block is injected *unasked*, not to the one where the
    agent went and looked the card up."""
    out: list[str] = []
    for i, c in enumerate(cards):
        label = c.title or (c.keys[0] if c.keys else "")
        aliases = ", ".join(c.keys)
        header = f"### {label}"
        if aliases and aliases != label:
            header += f" ({aliases})"
        if ids is not None:
            header += f" [card_id: {ids[i]}]"
        out.append(f"{header}\n{c.body if ids is not None else _capped_body(c.body)}")
    return out


def _fits(entries: list[str]) -> int:
    """How many leading entries fit the block budget (at least one)."""
    used = 0
    shown = 0
    for entry in entries:
        if used + len(entry) > CARD_BLOCK_MAX_CHARS and shown:
            break
        used += len(entry)
        shown += 1
    return shown


def shown_card_count(cards: list[ContextCard], *, ids: list[str] | None = None) -> int:
    """How many of `cards` ``card_context_block`` would actually render.

    Callers record the cards they injected so a term isn't defined twice in one
    turn — and marking one the block DROPPED would silence that term for the
    rest of the turn, so the definition would never arrive at all. Same helpers
    as the renderer, so the two can't drift."""
    return _fits(_entries(cards, ids))


def card_context_block(cards: list[ContextCard], *, ids: list[str] | None = None) -> str:
    """Render matched cards as a labelled context block to inject into the KB
    chat agent's turn — empty string when nothing matched (so the caller adds
    nothing). Each entry leads with its term(s) so the model can attribute the
    explanation, and the preamble tells it these are authoritative (no search
    needed for them).

    When ``ids`` (a list parallel to ``cards``) is given, each entry's heading also
    carries ``[card_id: <rid>]`` (#111) so the agent's ``lookup_glossary`` output is a
    read-before-write surface — it can target a card for ``update_context_card``. The
    route-injection path passes no ids and stays id-less.

    Both budgets below exist because this block is injected *automatically* —
    on every ``kb_search``, up to ``cap`` cards at a time, without the agent
    asking for it. The match count was capped from the start; the LENGTH never
    was, so one card someone pasted a spec into became a tax on every search of
    that collection. A definition is meant to be read in passing, so a long one
    is truncated rather than allowed to crowd out the passages it annotates."""
    if not cards:
        return ""
    entries = _entries(cards, ids)
    shown = _fits(entries)
    parts = [
        "Internal glossary entries relevant to the question — treat them as "
        "authoritative and do not search the knowledge base for these terms:",
        *entries[:shown],
    ]
    if shown < len(cards):
        parts.append(
            f"[{shown} of {len(cards)} matching glossary entries shown — "
            f"look the rest up by term with lookup_glossary]"
        )
    return "\n\n".join(parts)
