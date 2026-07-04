"""#303 — populate a SourceDoc's denormalized collection-permission mirror.

A SourceDoc carries a copy of its parent collection's access-control fields
(``collection_visibility`` / ``collection_read_meta`` / ``collection_created_by``)
so the ``source_doc`` access_scope can hide it at the storage layer without a
cross-resource join. This module is the single reader that turns a collection
into those mirror kwargs — used at doc-create (``kb.ingest``), doc-move
(``api.kb_routes``), and the fan-out that re-pushes them when the collection's
visibility / read_meta changes. See ``docs/plan-permissions.md`` (#303).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import msgspec
from specstar import QB, SpecStar

from ..perm import Actor, Permission, Verb, authorize
from ..resources.kb import Collection, SourceDoc


def collection_mirror_fields(spec: SpecStar, collection_id: str) -> dict[str, Any]:
    """The ``collection_*`` SourceDoc mirror kwargs for a doc in ``collection_id``,
    read from the collection's LIVE permission + owner. A collection with no
    ``Permission`` object ≡ public. Always sets all three fields EXPLICITLY so a
    re-mirror after a collection is loosened back to public resets a doc that was
    previously restricted. The collection is guaranteed to exist here (every
    caller mirrors a doc INTO an existing collection — the doc's ``Ref`` owns
    referential integrity), so a lookup miss is a real invariant break, not a case
    to paper over.
    """
    crm = spec.get_resource_manager(Collection)
    coll = crm.get(collection_id).data
    assert isinstance(coll, Collection)
    perm = coll.permission
    created_by = crm.get_meta(collection_id).created_by
    visibility = "public" if perm is None else perm.visibility
    read_meta = [] if perm is None else list(perm.read_meta)
    return {
        "collection_visibility": visibility,
        "collection_read_meta": read_meta,
        "collection_created_by": created_by,
    }


def denied_doc_ids(
    spec: SpecStar,
    actor: Actor,
    collection_ids: Iterable[str],
    verb: Verb,
    *,
    superusers: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """#308 — the doc-ids in ``collection_ids`` whose per-doc override BLOCKS
    ``actor`` from ``verb`` (``read_meta`` for the document LIST, ``read_content``
    for AI retrieval). The (usually empty) EXCLUSION set the list filter and the
    retriever remove from their candidates.

    It queries ONLY docs that carry an override (``permission.visibility`` is not
    null). An override is only ever written fresh at the current schema, so that
    field is always properly indexed on exactly these rows — which means a
    pre-migrate / un-overridden doc (whose ``permission.visibility`` may not be in
    the index yet) is NEVER in the candidate set, and thus never wrongly excluded
    (an inclusion predicate keyed on ``is_null()`` would drop such rows on the
    disk/postgres backend, where an un-extracted index doesn't match ``is_null()``).
    A collection nobody tightened per-doc yields an empty set. Each override is
    authorized against the mirrored collection owner (``collection_created_by``), so
    the owner / a superuser are never denied.

    A **metas-only** read: it uses ``search_resources`` (the indexed_data
    projection), NOT ``list_resources``, so the document LIST never pulls a full
    SourceDoc data blob (the multi-KB extracted ``text``) — keeping it inside the
    #395 list budget. The override's ``visibility`` + read-grant lists ride the
    index, so a minimal ``Permission`` reconstructed from them feeds ``authorize``
    (still the single decision point) without loading the record."""
    cids = list(collection_ids)
    if not cids:
        return frozenset()
    grant_field = f"permission.{verb}"
    drm = spec.get_resource_manager(SourceDoc)
    q = QB["collection_id"].in_(cids) & QB["permission.visibility"].is_not_null()
    denied: set[str] = set()
    for m in drm.search_resources(q.build()):
        indexed = getattr(m, "indexed_data", None)
        indexed = indexed if isinstance(indexed, dict) else {}
        vis = indexed.get("permission.visibility")
        if not isinstance(vis, str):  # pragma: no cover — is_not_null guarantees a value
            continue
        grants = indexed.get(grant_field)
        override = Permission(visibility=vis)
        # Only the queried verb's grant list rides the index, so set exactly it —
        # authorize reads `visibility` + `grants(verb)` and nothing else here.
        setattr(override, verb, [str(g) for g in grants] if isinstance(grants, list) else [])
        created_by = indexed.get("collection_created_by")
        if not authorize(
            actor,
            verb,
            override,
            created_by=created_by if isinstance(created_by, str) else "",
            superusers=superusers,
        ):
            denied.add(m.resource_id)
    return frozenset(denied)


def push_mirror_to_docs(
    spec: SpecStar,
    collection_id: str,
    *,
    visibility: str,
    read_meta: list[str],
    created_by: str,
) -> int:
    """Re-push the collection's read-visibility mirror onto every SourceDoc in it
    (the #303 fan-out). specstar has no bulk update, so this is a per-doc loop —
    run OFF the event loop by the caller. Runs as ``created_by`` (the collection
    owner) so each doc keeps its own ``created_by`` while ``updated_by`` records
    the fan-out. A doc already carrying the target mirror is skipped so a no-op
    change doesn't churn revisions. Returns the number of docs actually updated.
    """
    drm = spec.get_resource_manager(SourceDoc)
    target = list(read_meta)
    updated = 0
    with drm.using(created_by):
        for r in drm.list_resources((QB["collection_id"] == collection_id).build()):
            doc = r.data
            assert isinstance(doc, SourceDoc)
            if (
                doc.collection_visibility == visibility
                and doc.collection_read_meta == target
                and doc.collection_created_by == created_by
            ):
                continue
            drm.update(
                r.info.resource_id,  # ty: ignore[unresolved-attribute]
                msgspec.structs.replace(
                    doc,
                    collection_visibility=visibility,
                    collection_read_meta=target,
                    collection_created_by=created_by,
                ),
            )
            updated += 1
    return updated
