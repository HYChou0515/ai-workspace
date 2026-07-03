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

from typing import Any

import msgspec
from specstar import QB, SpecStar

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
