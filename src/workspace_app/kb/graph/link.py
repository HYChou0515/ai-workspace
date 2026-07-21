"""#534 B — build the vocabulary from the evidence, deterministically.

The first and safest of the four bases: mentions whose comparison key is
identical are one thing. No model, no reviewer, and it does the bulk of the work —
most of what makes two surfaces differ is typing noise the key already removed.

A RECONCILE, not a build. It re-runs on a schedule, and a second pass must change
nothing: entities and links accumulate, and nothing is ever rebuilt from scratch,
because the links are where a human's decisions are recorded and a rebuild would
throw them away. Identity therefore survives re-extraction — the invariant the
whole two-layer split rests on.
"""

from __future__ import annotations

import re

import msgspec
from specstar import QB, SpecStar

from ...resources.graph import GraphEntity, GraphEntityLink, GraphMention

_DIGITS = re.compile(r"\d+")


def differs_by_number(a: str, b: str) -> bool:
    """Whether two surfaces carry different numbers — the one outright veto.

    RO-3 and RO-4, part A1203 and A1204: different things, and precisely the pairs
    a similarity score cannot separate, since they differ by one character out of
    many. Vetoed rather than queued for review, because asking a person to reject
    them one at a time spends the scarcest thing here on a question a rule answers.

    Compares VALUES, not spellings, so "RO-03" and "RO-3" do not trip it: whether
    those are one machine is a real question for a later basis to answer, and this
    rule must not pre-empt it by vetoing.
    """
    return [int(n) for n in _DIGITS.findall(a)] != [int(n) for n in _DIGITS.findall(b)]


def _find_or_create_entity(spec: SpecStar, key: str, display: str) -> str:
    """The entity whose keys already contain ``key``, or a new one.

    Looking the key up (rather than tracking what this run created) is what makes
    the pass idempotent AND makes later evidence join the identity that is already
    there instead of starting a second one beside it.
    """
    rm = spec.get_resource_manager(GraphEntity)
    for r in rm.list_resources((QB["norm_keys"].contains(key)).build()):
        return r.info.resource_id  # ty: ignore[unresolved-attribute]
    return rm.create(GraphEntity(canonical_name=display, norm_keys=[key])).resource_id


def _add_collections(spec: SpecStar, entity_id: str, collection_ids: set[str]) -> None:
    """Widen an entity's evidence-location list. That list is what the access scope
    reads, so it has to grow as evidence arrives — an entity whose list lags is
    invisible to people who should be able to see it."""
    rm = spec.get_resource_manager(GraphEntity)
    entity = rm.get(entity_id).data
    assert isinstance(entity, GraphEntity)
    merged = sorted(set(entity.collection_ids) | collection_ids)
    if merged != sorted(entity.collection_ids):
        rm.update(entity_id, msgspec.structs.replace(entity, collection_ids=merged))


def _set_kind(spec: SpecStar, entity_id: str, kind_id: str) -> None:
    rm = spec.get_resource_manager(GraphEntity)
    entity = rm.get(entity_id).data
    assert isinstance(entity, GraphEntity)
    if entity.kind_id != kind_id:
        rm.update(entity_id, msgspec.structs.replace(entity, kind_id=kind_id))


def _link(spec: SpecStar, entity_id: str, mention_id: str) -> bool:
    """Record that a mention belongs to an identity. Returns whether it was new.

    Never duplicated on a re-run: one mention belongs to one identity by this
    basis, so an existing link means there is nothing to do.
    """
    rm = spec.get_resource_manager(GraphEntityLink)
    existing = rm.list_resources((QB["mention_id"] == mention_id).build())
    if any(existing):
        return False
    rm.create(
        GraphEntityLink(
            entity_id=entity_id,
            mention_id=mention_id,
            basis="identical",
            evidence="norm_surface",
            state="active",
        )
    )
    return True


def link_identical_mentions(spec: SpecStar) -> int:
    """Attach every mention to the identity its key names. Returns links created.

    Kinds go through the SAME pass: "機台" is an identity like anything else, so
    the taxonomy comes out of the data instead of a list written in advance by
    someone outside the domain. The recursion stops there — a kind has no kind.
    """
    mrm = spec.get_resource_manager(GraphMention)
    # Group the evidence first: the display name should be the surface the corpus
    # actually used most, and that is only knowable once every mention is in hand.
    by_key: dict[str, list[GraphMention]] = {}
    ids_by_key: dict[str, list[str]] = {}
    for r in mrm.list_resources(QB.all().build()):
        mention = r.data
        assert isinstance(mention, GraphMention)
        if not mention.norm_surface:
            continue
        by_key.setdefault(mention.norm_surface, []).append(mention)
        ids_by_key.setdefault(mention.norm_surface, []).append(r.info.resource_id)  # ty: ignore[unresolved-attribute]

    kind_ids: dict[str, str] = {}
    for mentions in by_key.values():
        for kind_key in {m.norm_kind for m in mentions if m.norm_kind}:
            if kind_key not in kind_ids:
                kind_display = _display_name(
                    [(m.kind, m.occurrences) for m in mentions if m.norm_kind == kind_key]
                )
                kind_ids[kind_key] = _find_or_create_entity(spec, kind_key, kind_display)

    created = 0
    for key, mentions in by_key.items():
        display = _display_name([(m.surface, m.occurrences) for m in mentions])
        entity_id = _find_or_create_entity(spec, key, display)
        _add_collections(spec, entity_id, {m.collection_id for m in mentions})
        kinds = {m.norm_kind for m in mentions if m.norm_kind}
        if len(kinds) == 1:
            _set_kind(spec, entity_id, kind_ids[next(iter(kinds))])
        for mention_id in ids_by_key[key]:
            created += _link(spec, entity_id, mention_id)
    return created


def _display_name(candidates: list[tuple[str, int]]) -> str:
    """The surface the corpus used most — a name someone actually wrote, never a
    normalised string nobody did. Ties break on the surface itself so a re-run
    cannot shuffle the name."""
    return max(candidates, key=lambda pair: (pair[1], pair[0]))[0]
