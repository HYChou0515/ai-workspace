"""#697 — record a computed graph. The ONLY place the graph touches the store.

Everything interesting happens in :mod:`build`, which has no ``SpecStar`` handle
and therefore cannot write. This module is the other half: it takes what that
produced and puts it away, and it decides nothing.

Keeping the decision and the recording apart is what makes the extraction
criterion tunable at all — the read-only inspection tool runs the same
computation and serialises the result instead of calling this.

Ids are recomputed from the rows rather than carried beside them. They are
content-addressed (``mention_id``, ``relationship_id``), so the row already
determines its id, and a copy travelling alongside would be a second place for
it to be wrong.
"""

from __future__ import annotations

from specstar import QB, MergePatch, SpecStar

from ...resources.graph import (
    GraphClaim,
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
    link_id,
    mention_id,
    relationship_id,
)
from .build import DocGraph, Vocabulary
from .paging import indexed_rows

# Every model keyed on the document that produced it. A re-extraction clears the
# document's rows across ALL of them, so the three layers can never disagree
# about which version of a document they came from.
_PER_DOC = (GraphMention, GraphRelationship, GraphClaim)


def wipe_doc_graph(spec: SpecStar, source_doc_id: str) -> int:
    """Drop every row one document produced. Returns how many went.

    Called before a re-extraction (so re-running replaces rather than
    accumulates) and whenever the document itself is torn down — including a
    RENAME, which re-creates the deck under a new id and would otherwise leave
    the old id's rows dangling: no re-extraction ever touches an id it is not
    processing, so they would be counted twice forever.

    Hard-delete, not soft: a soft ``delete`` still shows up in
    ``list_resources``, so a re-run would accumulate anyway.
    """
    removed = 0
    for model in _PER_DOC:
        rm = spec.get_resource_manager(model)
        stale = [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in rm.list_resources((QB["source_doc_id"] == source_doc_id).build())
        ]
        for rid in stale:
            rm.permanently_delete(rid)
        removed += len(stale)
    return removed


def persist_doc_graph(spec: SpecStar, graph: DocGraph) -> None:
    """Write one document's rows. The caller wiped what the document wrote
    before, so this only creates."""
    mrm = spec.get_resource_manager(GraphMention)
    for mention in graph.mentions:
        mrm.create(mention, resource_id=mention_id(mention.source_doc_id, mention.surface))
    rrm = spec.get_resource_manager(GraphRelationship)
    for rel in graph.relationships:
        rrm.create(
            rel,
            resource_id=relationship_id(
                rel.source_doc_id, rel.chunk_id, rel.subject, rel.predicate, rel.object
            ),
        )
    crm = spec.get_resource_manager(GraphClaim)
    for claim in graph.claims:
        crm.create(claim)


def persist_vocabulary(spec: SpecStar, vocabulary: Vocabulary) -> tuple[int, int]:
    """Bring the stored vocabulary to the computed one. Returns ``(entities, links)``.

    A RECONCILE to a target state, not a rebuild. Every id is derived from what
    the row says, so the same decision keeps the same row, a decision the evidence
    no longer supports is the one that goes, and there is no moment when the
    vocabulary is empty — a job killed halfway leaves the previous answer standing
    rather than an entity page that has vanished.

    Nothing here materialises a stored row. Both tables are compared through
    their INDEX: an entity's five fields are all indexed, and a link is
    content-addressed on everything about it except ``state``, ``evidence`` and
    ``collection_ids``. Over a corpus that is the difference between one decode
    per identity per week and none (#689).

    ``state`` is deliberately outside the address. It is the one field a PERSON
    writes — accepting or rejecting a merge proposal — so leaving an existing row
    alone is what keeps that decision from being recomputed away the same night.

    Of the two remaining, only ``collection_ids`` is brought back onto the
    computed value, and the split is the index rather than a judgement call:
    ``collection_ids`` is indexed and is what the access scope reads, so a row
    left on the collections that were true the day it was created is a
    visibility rule frozen in the past — a proposal whose subject gains evidence
    elsewhere never reaches the reviewers who now own half the question, and an
    empty queue looks exactly like a queue with nothing in it. ``evidence`` is
    not indexed on purpose (nobody else reads it, and indexing every link to
    carry display text is precisely the cost #689 is about), so noticing a
    change to it would mean decoding every row in the corpus. It keeps whatever
    it was first written with — and it keeps it because the write is a merge
    PATCH naming one field, not a replacement of the row, so `state`, `basis`
    and `evidence` are not merely left unchanged but unreachable from here.

    Entities go in before the links that point at them, because the ref
    validator refuses a link whose entity is not there yet. On the way out they
    go last for a different reason than the one written here before:
    ``permanently_delete`` fires no cascade, so an entity removed while a link
    still points at it leaves that link DANGLING rather than taking it along.
    That is why a preserved decision whose subject the evidence no longer
    supports is dropped here rather than left pointing at nothing.
    """
    erm = spec.get_resource_manager(GraphEntity)
    lrm = spec.get_resource_manager(GraphEntityLink)

    target_links = {
        link_id(link.entity_id, link.mention_id, link.basis, link.proposed_from): link
        for link in vocabulary.links
    }
    stored_entities = _stored(erm, _ENTITY_FIELDS)
    stored_links = _stored(lrm, _LINK_FIELDS)

    for eid, entity in vocabulary.entities.items():
        indexed = stored_entities.get(eid)
        if indexed is None:
            erm.create(entity, resource_id=eid)
        elif _entity_indexed(entity) != _normalised(indexed, _ENTITY_FIELDS):
            erm.update(eid, entity)
    for lid, link in target_links.items():
        stored = stored_links.get(lid)
        if stored is None:
            lrm.create(link, resource_id=lid)
        elif _state_of(stored) not in _PERSON_ANSWERED and sorted(
            stored.get("collection_ids") or []
        ) != sorted(link.collection_ids):
            # A link's collections are what the access scope reads, so leaving
            # them at whatever was true the day the row was created is not
            # staleness — it is a visibility rule frozen in the past, failing
            # closed and saying nothing. A proposal whose subject gains evidence
            # in another collection has become partly that collection's
            # question, and its reviewers would never be shown it.
            #
            # `evidence` is not reconciled and cannot be: it is deliberately not
            # indexed (nobody else reads it, and indexing every link to carry
            # display text is the cost #689 is about), so noticing a change would
            # mean decoding every row in the corpus. What this compares is
            # exactly what the index can answer.
            #
            # A PATCH, not a write of the whole row. The snapshot above was taken
            # before the entity loop, so a person answering while this pass runs
            # is judged against a row that still said `pending` — and a
            # whole-record write then puts the computed link there, sending
            # `state` back to `pending` and `evidence` back to the model's
            # sentence. That is not a re-queued question: the answer is the input
            # the merge is re-derived from, so losing it un-merges the identity
            # and asks the same pair again forever.
            #
            # The answer is not a sharper guard — that is a rule someone has to
            # keep — but a write that CANNOT express the fields it does not own,
            # aimed at the row as it is NOW rather than as the snapshot
            # remembered it.
            _repoint(lrm, lid, sorted(link.collection_ids))

    for rid in stored_links.keys() - target_links.keys():
        # A link the computation no longer produces goes — UNLESS it carries a
        # person's answer. `settled` and `rejected` are not derived from the
        # evidence and cannot be recomputed, and they are the input that keeps
        # the merge from being re-proposed; deleting them asks the same question
        # again next week with the answer filed against a row nobody looks up.
        state = _state_of(stored_links[rid])
        if state in _PERSON_ANSWERED and _subject_survives(stored_links[rid], vocabulary):
            continue
        if state in _PERSON_ANSWERED and not _subject_survives(stored_links[rid], vocabulary):
            # The identities it was about are gone, so there is no merge left to
            # re-derive and nothing for the answer to apply to. Kept, it would
            # dangle: `permanently_delete` fires no cascade.
            lrm.permanently_delete(rid)
            continue
        if state == "pending" and not vocabulary.proposed:
            # Nobody asked the model this time, so "no proposals" is not an
            # answer about these rows — it is silence. Deleting them would empty
            # a review queue every time the pass runs without a model, which is
            # what recording a decision does.
            continue
        lrm.permanently_delete(rid)
    for rid in stored_entities.keys() - vocabulary.entities.keys():
        erm.permanently_delete(rid)
    return len(vocabulary.entities), len(target_links)


_ENTITY_FIELDS = ("canonical_name", "norm_keys", "kind_id", "collection_ids", "merged_into")

#: What the link half reads. `proposed_from` is REQUESTED, not merely read:
#: it was indexed after `settled`/`rejected` shipped, so asking for it is what
#: makes a decision written on the released build arrive at all.
_LINK_FIELDS = ("entity_id", "state", "proposed_from", "collection_ids")

#: Link states a PERSON wrote. The computation never produces them, so they
#: would otherwise look exactly like rows it no longer produces.
_PERSON_ANSWERED = frozenset({"settled", "rejected"})


def _state_of(indexed: dict) -> str:
    """A stored link's state, with the pre-index default the field itself has."""
    return str(indexed.get("state") or "active")


def _repoint(lrm, lid: str, collection_ids: list[str]) -> None:
    """Bring one link's access footprint onto the computed one — if the row is
    still what the snapshot said it was.

    The snapshot this pass compares against is taken before the entity loop, so
    a person answering while the pass runs is judged against a row that still
    said ``pending``. Their answer is safe from the CONTENT of this write (a
    merge patch cannot express `state`), but not from the write itself: the
    computation's collections are the proposal's, and a decision a person wrote
    carries the union of both sides — so patching it would quietly NARROW who
    can see the record of their own decision.

    So the state is re-read here, on the handful of rows this pass actually
    intends to change rather than on every link in the corpus, and the write
    carries the revision that read saw. Someone who got in between wins; the
    next pass will bring the collections along if they still need it.
    """
    from specstar.types import PreconditionFailedError, ResourceIDNotFoundError

    try:
        current = lrm.get(lid)
    except ResourceIDNotFoundError:
        return  # deleted under us; nothing to repoint
    link = current.data
    if isinstance(link, GraphEntityLink) and link.state in _PERSON_ANSWERED:
        return
    try:
        lrm.patch(
            lid,
            MergePatch({"collection_ids": collection_ids}),
            expected_revision_id=current.info.revision_id,
        )
    except PreconditionFailedError:
        return  # answered or rewritten between the read and the write


def _subject_survives(indexed: dict, vocabulary: Vocabulary) -> bool:
    """Whether the identities a recorded answer was about still exist."""
    return all(
        str(indexed.get(field) or "") in vocabulary.entities
        for field in ("entity_id", "proposed_from")
    )


def _stored(rm, fields: tuple[str, ...]) -> dict[str, dict]:
    """Every stored id with the values this pass reads, a page at a time.

    Through ``indexed_rows`` rather than straight off ``indexed_data``, because
    this is the read that DELETES. A row written before one of these fields was
    indexed carries no cell for it, and an absent cell read as a value is an
    identity that looks like it no longer exists — so a merge a person approved
    on the released build was deleted for predating an index it could not have
    been written with. Everywhere else on this path the same miss only costs a
    blob read; here it cost the decision itself.

    The fallback is empty once the model's migrate route has run, which is what
    keeps the migrate a speed-up rather than something the deploy has to be
    ordered against.
    """
    return dict(indexed_rows(rm, fields))


def _entity_indexed(entity: GraphEntity) -> dict:
    return {
        "canonical_name": entity.canonical_name,
        "norm_keys": sorted(entity.norm_keys),
        "kind_id": entity.kind_id,
        "collection_ids": sorted(entity.collection_ids),
        "merged_into": entity.merged_into,
    }


def _normalised(indexed: dict, fields: tuple[str, ...]) -> dict:
    """The indexed cells as the computed side spells them.

    A row written before a field was indexed carries no cell for it — specstar
    extracts ``indexed_data`` at write time and never backfills — and an absent
    cell must read as "different", so the row is rewritten rather than left
    behind on an old shape.
    """
    out: dict = {}
    for name in fields:
        value = indexed.get(name)
        out[name] = (
            sorted(value) if isinstance(value, list) else (value if value is not None else "")
        )
    return out
