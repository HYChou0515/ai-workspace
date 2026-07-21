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

    Fires only when BOTH sides carry numbers. A side with none has nothing to
    disagree about — "回焊爐" beside "RO-3" is the general name next to the
    specific code, the commonest way a document declares an alias, and vetoing
    that would throw away the strongest evidence the vocabulary can get.
    """
    left = [int(n) for n in _DIGITS.findall(a)]
    right = [int(n) for n in _DIGITS.findall(b)]
    return bool(left) and bool(right) and left != right


# A bracket declares an ALIAS only when what is inside could itself be a name.
# "回焊爐(250°C)" states a setting; "產能(2024)" states a period; "Yield (%)" states
# a unit. Requiring a letter or an ideograph, and refusing anything that is only a
# number with trailing unit-ish characters, keeps those out.
_BRACKETED = re.compile(r"^(?P<outer>[^(（\[【]+)[(（\[【](?P<inner>[^)）\]】]+)[)）\]】]\s*$")
_HAS_NAME_CHAR = re.compile(r"[^\W\d_]", re.UNICODE)
_ONLY_MEASURE = re.compile(r"^[\d\s.,:%°/-]*[a-zA-Z°%]{0,3}$")


def declared_aliases(surface: str) -> list[tuple[str, str]]:
    """The equivalences a surface DECLARES about itself.

    "回焊爐(Reflow Oven)" is a document saying, in its own words, that these two
    names are one thing — the strongest basis short of a person, and it costs
    nothing to read: the declaration is written inside the surface, which is
    exactly why the entity key keeps the parentheticals the metric key strips.

    Returns pairs, or nothing when the bracket is not a declaration: a measurement
    ("250°C"), a period ("2024"), a unit ("%"), or a pair whose numbers disagree,
    which no document means as an alias.
    """
    match = _BRACKETED.match(surface.strip())
    if match is None:
        return []
    outer = match.group("outer").strip()
    inner = match.group("inner").strip()
    if not outer or not inner:
        return []
    if not _HAS_NAME_CHAR.search(inner) or _ONLY_MEASURE.match(inner):
        return []  # a setting, a period or a unit — not another name
    if differs_by_number(outer, inner):
        return []
    return [(outer, inner)]


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


def link_declared_aliases(spec: SpecStar) -> int:
    """Fold every declaration a document made into the vocabulary. Returns links
    created.

    One deck writing "回焊爐(Reflow Oven)" is enough for every other deck's
    "Reflow Oven" — including the ones that only ever use the English — to resolve
    to the same identity. That is what makes an entity page whole across
    languages, and no model was asked for it.

    Idempotent like the identical pass: an alias already carried by the entity is
    nothing to do, so a re-run adds neither entities nor links.
    """
    from .normalize import norm_surface

    mrm = spec.get_resource_manager(GraphMention)
    created = 0
    for r in mrm.list_resources(QB.all().build()):
        mention = r.data
        assert isinstance(mention, GraphMention)
        for outer, inner in declared_aliases(mention.surface):
            outer_key, inner_key = norm_surface(outer), norm_surface(inner)
            host = _entity_for_key(spec, norm_surface(mention.surface))
            if host is None:
                continue
            for key in (outer_key, inner_key):
                target = _entity_for_key(spec, key)
                if target == host:
                    continue
                if target is None:
                    # The alias was never mentioned on its own; the declaring
                    # surface is the only evidence, so the key joins the host.
                    _add_key(spec, host, key)
                    created += 1
                    continue
                _absorb(spec, host, target, evidence=mention.surface)
                created += 1
    return created


def _entity_for_key(spec: SpecStar, key: str) -> str | None:
    rm = spec.get_resource_manager(GraphEntity)
    for r in rm.list_resources((QB["norm_keys"].contains(key)).build()):
        return r.info.resource_id  # ty: ignore[unresolved-attribute]
    return None


def _add_key(spec: SpecStar, entity_id: str, key: str) -> None:
    rm = spec.get_resource_manager(GraphEntity)
    entity = rm.get(entity_id).data
    assert isinstance(entity, GraphEntity)
    if key not in entity.norm_keys:
        rm.update(
            entity_id, msgspec.structs.replace(entity, norm_keys=sorted([*entity.norm_keys, key]))
        )


def _absorb(spec: SpecStar, host_id: str, other_id: str, *, evidence: str) -> None:
    """Move another identity's keys, evidence-locations and links onto the host.

    The mentions themselves are untouched — only the links move, and they move
    carrying ``declared`` as their basis and the declaring surface as the place to
    go and check it. The absorbed identity is then empty of evidence, which the
    access scope already treats as invisible.
    """
    rm = spec.get_resource_manager(GraphEntity)
    host = rm.get(host_id).data
    other = rm.get(other_id).data
    assert isinstance(host, GraphEntity) and isinstance(other, GraphEntity)
    rm.update(
        host_id,
        msgspec.structs.replace(
            host,
            norm_keys=sorted(set(host.norm_keys) | set(other.norm_keys)),
            collection_ids=sorted(set(host.collection_ids) | set(other.collection_ids)),
        ),
    )
    lrm = spec.get_resource_manager(GraphEntityLink)
    for r in lrm.list_resources((QB["entity_id"] == other_id).build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        lrm.update(
            r.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(link, entity_id=host_id, basis="declared", evidence=evidence),
        )
    rm.update(other_id, msgspec.structs.replace(other, norm_keys=[], collection_ids=[]))
