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

import msgspec
from specstar import QB, SpecStar

from ...resources.graph import (
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
)
from ..llm import ILlm


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


def _mention_collection(spec: SpecStar, mention_id: str) -> list[str]:
    """Where a mention's evidence lives, for the link that points at it."""
    rm = spec.get_resource_manager(GraphMention)
    mention = rm.get(mention_id).data
    assert isinstance(mention, GraphMention)
    return [mention.collection_id]


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
            collection_ids=_mention_collection(spec, mention_id),
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
            labelled = [m for m in mentions if m.norm_kind == kind_key]
            if kind_key not in kind_ids:
                kind_display = _display_name([(m.kind, m.occurrences) for m in labelled])
                kind_ids[kind_key] = _find_or_create_entity(spec, kind_key, kind_display)
            # A kind's evidence is the mentions carrying the label. Without it the
            # kind has no collections, and the scope hides an identity nothing
            # vouches for — so the kind would be invisible on every page.
            _add_collections(spec, kind_ids[kind_key], {m.collection_id for m in labelled})

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


def name_predicates(spec: SpecStar) -> int:
    """Give every connection-word an identity. Returns identities created.

    A predicate is a name for a kind of connection, and "造成" and "leads to" are
    one connection written two ways — the same problem a thing has, so it gets the
    same answer rather than a mechanism of its own. Once they are identities, the
    four bases join them exactly as they join anything else, and a page shows one
    predicate instead of two.

    Relationships keep their verbatim predicate. This only ensures something
    exists to resolve it to; the row is evidence and is never rewritten.
    """
    rrm = spec.get_resource_manager(GraphRelationship)
    by_key: dict[str, list[tuple[str, int]]] = {}
    where: dict[str, set[str]] = {}
    for r in rrm.list_resources(QB.all().build()):
        rel = r.data
        assert isinstance(rel, GraphRelationship)
        if rel.norm_predicate:
            by_key.setdefault(rel.norm_predicate, []).append((rel.predicate, 1))
            where.setdefault(rel.norm_predicate, set()).add(rel.collection_id)
    created = 0
    for key, surfaces in by_key.items():
        existed = _entity_for_key(spec, key)
        entity_id = existed or _find_or_create_entity(spec, key, _display_name(surfaces))
        # A predicate's evidence is the relationships that use it. Without this it
        # carries no collections, and the scope reads "nothing vouches for this"
        # as invisible — to everyone, including the owner.
        _add_collections(spec, entity_id, where[key])
        created += 0 if existed else 1
    return created


def link_declared_aliases(spec: SpecStar) -> int:
    """Apply every equivalence a document STATED. Returns absorptions performed.

    Applied without review, and the reason is not that a model produced it — a
    resemblance comes from the same model and waits for a person. It is that this
    one points at a sentence: the link records the quote, so what it rested on can
    be read by anyone who doubts it. A declaration that could not be quoted never
    got this far.

    What keeps a mis-read list ("RO-3、RO-4") out is the extraction contract, not a
    rule here: the passage must explicitly state the two names are one thing, and
    the quote saying so must appear in it. Adding a rule about digits on top would
    be guessing at the shape of the mistake — and every such guess in this module
    has been wrong within a corpus or two.

    Idempotent: an equivalence already folded in leaves nothing to do.
    """
    mrm = spec.get_resource_manager(GraphMention)
    applied = 0
    for r in mrm.list_resources((QB["declared_same_as"].is_not_null()).build()):
        mention = r.data
        assert isinstance(mention, GraphMention)
        if not mention.declared_same_as:
            continue
        host = _entity_for_key(spec, mention.norm_surface)
        if host is None:
            continue
        for other_key in mention.declared_same_as:
            target = _entity_for_key(spec, other_key)
            if target is None or target == host:
                continue
            _absorb(
                spec,
                host,
                target,
                evidence=f"{mention.source_doc_id}: {mention.declared_quote}",
            )
            applied += 1
    return applied


def _entity_for_key(spec: SpecStar, key: str) -> str | None:
    rm = spec.get_resource_manager(GraphEntity)
    for r in rm.list_resources((QB["norm_keys"].contains(key)).build()):
        return r.info.resource_id  # ty: ignore[unresolved-attribute]
    return None


def _absorb(spec: SpecStar, host_id: str, other_id: str, *, evidence: str) -> None:
    """Move another identity's keys, evidence-locations and links onto the host.

    The mentions themselves are untouched — only the LINKS move, carrying their
    new basis and the words to go and check. The absorbed identity keeps no
    evidence, which the access scope already reads as invisible, so nothing has to
    be deleted and the absorption can be undone by moving the links back.
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
    rm.update(
        other_id,
        msgspec.structs.replace(other, norm_keys=[], collection_ids=[], merged_into=host_id),
    )


def link_resembling_entities(spec: SpecStar, llm: ILlm) -> int:
    """PROPOSE merges by handing the vocabulary to the model. Returns proposals.

    The whole list at once, not pair by pair. Asking about pairs costs a call per
    pair — N² of them — which is what forced the earlier attempts to invent a
    cheap test for which pairs deserved one. Every such test was wrong within a
    corpus or two: character overlap admitted "condition" against "dose" because
    Latin has twenty-six letters, and a rule refusing to merge terms whose digits
    differ also refused 第2型糖尿病 against 第二型糖尿病. Rules over spelling keep
    meeting exceptions because the question is not about spelling.

    Given the list, the model answers the question that was actually being asked —
    and it sees across languages, where no rule over characters ever could: 乳酸中毒
    and lactic acidosis are one thing to a reader and share not one character.

    It still only PROPOSES. That constraint is about who decides, not about how
    candidates are found, and conflating the two is what produced the heuristics
    in the first place.
    """
    entities = _live_entities(spec)
    if len(entities) < 2:
        return 0
    seen = _existing_proposals(spec)
    by_name: dict[str, list[str]] = {}
    for entity_id, entity in entities:
        by_name.setdefault(entity.canonical_name, []).append(entity_id)
    proposed = 0
    for batch in _batches(entities, _NAMES_PER_CALL):
        for group, why in _group(llm, batch):
            ids = [i for name in group for i in by_name.get(name, [])]
            host, rest = ids[0], ids[1:]
            for other in rest:
                if (host, other) in seen or (other, host) in seen:
                    continue
                seen.add((host, other))
                proposed += _propose(spec, host, other, why=why)
    return proposed


# How many names go into one adjudication call. Bounded by the model's context,
# not by any belief about the data. A vocabulary larger than this is split, and a
# pair that lands in different batches is simply not proposed this run — the pass
# repeats, the batches are ordered by name so they are stable, and a missed
# proposal is a merge nobody suggested, which is visible as two entries.
_NAMES_PER_CALL = 60

_GROUP_PROMPT = (
    "Below is a list of terms taken from technical documents. Some of them are "
    "different names for the SAME thing — a translation, an abbreviation, a "
    "spelling variant, a fuller form of the same name.\n\n"
    "Group ONLY those. Different equipment, different steps, a thing and a "
    "measurement of it, a category and a member of it, and two different values "
    "(500mg and 850mg, RO-3 and RO-4) are all DIFFERENT and must not be grouped.\n\n"
    "Terms:\n{names}\n\n"
    'Answer ONLY as JSON: {{"groups": [{{"names": ["…", "…"], "why": "<short>"}}]}}. '
    "Return an empty list if nothing belongs together."
)


def _batches(entities: list[tuple[str, GraphEntity]], size: int) -> list[list[str]]:
    names = sorted({e.canonical_name for _, e in entities})
    return [names[i : i + size] for i in range(0, len(names), size)]


def _group(llm: ILlm, names: list[str]) -> list[tuple[list[str], str]]:
    """The model's groupings, or nothing for an unreadable answer.

    Nothing, rather than a guess: this path can only ADD work for a person, so a
    confused reply should ask them nothing at all.
    """
    import json

    reply = llm.collect(_GROUP_PROMPT.format(names="\n".join(f"- {n}" for n in names)))
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    out: list[tuple[list[str], str]] = []
    for item in groups if isinstance(groups, list) else []:
        if not isinstance(item, dict):
            continue
        members = [str(n) for n in item.get("names", []) if str(n) in names]
        if len(members) < 2:
            continue  # a group of one groups nothing; a name it invented is not ours
        out.append((sorted(set(members)), str(item.get("why", "")).strip()))
    return out


def _live_entities(spec: SpecStar) -> list[tuple[str, GraphEntity]]:
    rm = spec.get_resource_manager(GraphEntity)
    out: list[tuple[str, GraphEntity]] = []
    for r in rm.list_resources(QB.all().build()):
        entity = r.data
        assert isinstance(entity, GraphEntity)
        if entity.collection_ids:  # an absorbed identity holds no evidence
            out.append((r.info.resource_id, entity))  # ty: ignore[unresolved-attribute]
    return out


def _existing_proposals(spec: SpecStar) -> set[tuple[str, str]]:
    """Pairs already raised, so a re-run neither re-asks the model nor stacks a
    second copy of the same question in front of a person.

    Includes pairs already DECIDED, not only those still waiting. A rejected pair
    coming back next week would re-spend the model and re-ask the person who
    already answered — which is how a review queue stops being read. The whole
    point of asking is that the answer is kept.
    """
    rm = spec.get_resource_manager(GraphEntityLink)
    out: set[tuple[str, str]] = set()
    for r in rm.list_resources(QB.all().build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        if link.proposed_from:
            out.add((link.entity_id, link.proposed_from))
    return out


def _propose(spec: SpecStar, host_id: str, other_id: str, *, why: str) -> int:
    """Record a merge proposal as pending links, without touching either identity.

    An identity with no links of its own still gets a proposal — one pending row
    naming no mention. A kind and a predicate ARE identities, which was the claim,
    but nothing MENTIONS "機台": things are labelled with it. Expressing every
    proposal over the other side's mentions therefore made those two kinds of
    identity unproposable, and the taxonomy stayed split by language while the
    design said it would not.
    """
    lrm = spec.get_resource_manager(GraphEntityLink)
    made = 0
    for r in lrm.list_resources((QB["entity_id"] == other_id).build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        if link.state != "active":
            continue
        lrm.create(
            GraphEntityLink(
                entity_id=host_id,
                mention_id=link.mention_id,
                basis="resembles",
                evidence=why,
                state="pending",
                proposed_from=other_id,
                collection_ids=list(link.collection_ids),
            )
        )
        made = 1
    if made:
        return made
    # The link needs the collections its subject's evidence lives in, or the scope
    # reads "nothing vouches for this" and hides the proposal from everyone — the
    # same fail-closed rule that has now caught three rows created without one.
    erm = spec.get_resource_manager(GraphEntity)
    other = erm.get(other_id).data
    assert isinstance(other, GraphEntity)
    lrm.create(
        GraphEntityLink(
            entity_id=host_id,
            mention_id="",  # nothing mentions a kind; the proposal is about the names
            basis="resembles",
            evidence=why,
            state="pending",
            proposed_from=other_id,
            collection_ids=list(other.collection_ids),
        )
    )
    return 1


def reconcile_vocabulary(spec: SpecStar, llm: ILlm | None = None) -> None:
    """Bring the vocabulary up to date with the evidence.

    The bases run in order of how much they can be trusted, and each line is
    independent: comment one out and that basis stops, with nothing else to
    change. The model line is last and takes ``llm=None`` to mean "skip", so it
    can also be turned off by configuration rather than by editing.
    """
    link_identical_mentions(spec)
    name_predicates(spec)
    link_declared_aliases(spec)
    if llm is not None:
        link_resembling_entities(spec, llm)  # ← comment out to stop proposing merges
