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

from ...resources.graph import (
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
)
from ..llm import ILlm

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

    The number veto still applies. A model transcribing "RO-3、RO-4" as a
    declaration has transcribed a list, and no document means that as an alias.

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
            if differs_by_number(mention.norm_surface, other_key):
                continue
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


# How much two keys must have in common before the model is asked about them.
# Every pair would be too many calls, so a cheap test narrows the field. Missing a
# pair costs a merge nobody proposed — visible as two entries someone can still
# join by hand — while asking about everything costs money on every single run.
_MIN_OVERLAP = 0.34

_JUDGE_PROMPT = (
    "Two terms from technical documents. Are they two names for the SAME thing, "
    "or two different things?\n\n"
    "A: {a}\nB: {b}\n\n"
    "Different equipment, different steps of a process, a thing and a measurement "
    "OF that thing, and a general category and one specific item in it are all "
    "DIFFERENT. Say they are the same only if one is simply another way of "
    "writing the other.\n\n"
    'Answer ONLY as JSON: {{"same": true or false, "why": "<one short sentence>"}}'
)


def _bigrams(text: str) -> set[str]:
    squashed = text.replace(" ", "")
    if len(squashed) < 2:
        return set(squashed)  # nothing to pair — fall back to the character itself
    return {squashed[i : i + 2] for i in range(len(squashed) - 1)}


def _overlap(a: str, b: str) -> float:
    """How much two terms have in common, as a fraction of the smaller one.

    ADJACENT CHARACTER PAIRS, not characters. Latin has twenty-six letters, so any
    two English words share most of theirs — comparing characters admitted
    "condition" against "dose" and asked the model about nearly every pair in the
    corpus, which is the O(n²) cost the narrowing exists to prevent. It only ever
    looked selective because the first corpus tried was Chinese, where the
    character set is large enough to hide the flaw.

    A pair carries position as well as identity, so it separates words in both
    scripts while still matching real variants: 乳酸中毒 against 乳酸性酸中毒, or a
    plural against its singular.

    Crude on purpose either way — it decides only who gets ASKED, and the model
    and then a person both still stand between it and any change.
    """
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def link_resembling_entities(spec: SpecStar, llm: ILlm) -> int:
    """PROPOSE merges the model believes in. Returns proposals made.

    Never applies anything. A resemblance points at nothing outside the model —
    unlike a declaration, which points at a sentence anyone can read — so it waits
    for a person, and that is the whole reason this basis is separated from the
    other three.

    Cost is shaped by that. Pairs a rule already settles never reach the model
    (numbers that disagree are vetoed first: spending a call, and then someone's
    attention, to reject RO-3 against RO-4 is waste), and a cheap overlap test
    narrows the rest.

    A proposal is recorded as PENDING links from the other identity's mentions to
    this one, so accepting it is the same absorption a declaration performs and
    rejecting it leaves nothing behind.
    """
    entities = _live_entities(spec)
    proposed = 0
    seen = _existing_proposals(spec)
    for i, (a_id, a) in enumerate(entities):
        for b_id, b in entities[i + 1 :]:
            if (a_id, b_id) in seen or (b_id, a_id) in seen:
                continue
            a_key, b_key = a.canonical_name, b.canonical_name
            if differs_by_number(a_key, b_key):
                continue
            if _overlap(a_key, b_key) < _MIN_OVERLAP:
                continue
            verdict = _adjudicate(llm, a_key, b_key)
            if verdict is None:
                continue
            proposed += _propose(spec, a_id, b_id, why=verdict)
    return proposed


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


def _adjudicate(llm: ILlm, a: str, b: str) -> str | None:
    """The model's verdict, or ``None`` for "no" and for anything unreadable.

    An unparseable answer is a no: this path can only ADD work for a person, so
    the safe reading of a confused reply is to ask nothing.
    """
    import json

    reply = llm.collect(_JUDGE_PROMPT.format(a=a, b=b))
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("same") is not True:
        return None
    return str(data.get("why", "")).strip() or "the model judged these the same"


def _propose(spec: SpecStar, host_id: str, other_id: str, *, why: str) -> int:
    """Record a merge proposal as pending links, without touching either identity."""
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
    return made


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
