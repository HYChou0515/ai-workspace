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

from specstar import QB, SpecStar

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

# Rows per request when walking a whole table. `list_resources(QB.all())` asks
# the store for everything in ONE statement, which specstar's postgres store
# builds with a row-constructor per key: 40k rows is a 937 KB statement the
# database answers with `stack depth limit exceeded` (#689).
PAGE = 500

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
    content-addressed on everything about it except ``state``. Over a corpus that
    is the difference between one decode per identity per week and none (#689).

    ``state`` is deliberately outside the address. It is the one field a PERSON
    writes — accepting or rejecting a merge proposal — so leaving an existing row
    alone is what keeps that decision from being recomputed away the same night.

    Links go before entities on the way in and after them on the way out, because
    a link's ``entity_id`` is a cascade Ref: an entity removed while a link still
    points at it would take that link with it.
    """
    erm = spec.get_resource_manager(GraphEntity)
    lrm = spec.get_resource_manager(GraphEntityLink)

    target_links = {
        link_id(
            link.entity_id, link.mention_id, link.basis, link.proposed_from, link.evidence
        ): link
        for link in vocabulary.links
    }
    stored_entities = _stored(erm)
    stored_links = _stored(lrm)

    for eid, entity in vocabulary.entities.items():
        indexed = stored_entities.get(eid)
        if indexed is None:
            erm.create(entity, resource_id=eid)
        elif _entity_indexed(entity) != _normalised(indexed, _ENTITY_FIELDS):
            erm.update(eid, entity)
    for lid, link in target_links.items():
        if lid not in stored_links:
            lrm.create(link, resource_id=lid)

    for rid in stored_links.keys() - target_links.keys():
        lrm.permanently_delete(rid)
    for rid in stored_entities.keys() - vocabulary.entities.keys():
        erm.permanently_delete(rid)
    return len(vocabulary.entities), len(target_links)


_ENTITY_FIELDS = ("canonical_name", "norm_keys", "kind_id", "collection_ids", "merged_into")


def _stored(rm) -> dict[str, dict]:
    """Every stored id with its indexed values, read a page at a time and without
    materialising a single row."""
    return {
        meta.resource_id: (meta.indexed_data or {})
        for meta in rm.iter_all(QB.all().build(), batch_size=PAGE)
    }


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
