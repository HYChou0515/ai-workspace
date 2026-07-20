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

from specstar import QB, MergePatch, SpecStar

from ..perm import Actor, Permission, Verb, authorize
from ..resources.graph import GraphClaim
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


def doc_mirror_fields(spec: SpecStar, source_doc_id: str) -> dict[str, Any]:
    """#534 slice 2 — the read-permission mirror kwargs for a row DERIVED from a
    doc (a ``GraphClaim``), read from that doc's LIVE effective permission.

    Both layers travel together: the doc's own copy of its collection's mirror
    (already maintained by #303, so this never re-reads the collection) plus the
    doc's own tightening (#308). A doc with no override yields the explicit verdict
    ``"public"`` — never ``""``, which the claim scope reads as "no mirror was ever
    written" and hides. Like ``collection_mirror_fields``, every field is set
    EXPLICITLY so re-stamping a loosened doc resets a previously restricted claim.

    The doc is guaranteed to exist (a claim is only ever extracted FROM one), so a
    lookup miss is a real invariant break, not a case to paper over.
    """
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(source_doc_id).data
    assert isinstance(doc, SourceDoc)
    override = doc.permission
    return {
        "collection_visibility": doc.collection_visibility,
        "collection_read_meta": list(doc.collection_read_meta),
        "collection_created_by": doc.collection_created_by,
        "doc_visibility": "public" if override is None else override.visibility,
        "doc_read_content": [] if override is None else list(override.read_content),
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


def _fan_out(
    spec: SpecStar,
    model: type,
    query: Any,
    fields: dict[str, Any],
    *,
    as_user: str,
    subject: str,
) -> int:
    """Push one set of mirror fields onto every row a query selects — ONE
    ``patch_many`` (specstar #434) — and return how many rows actually moved.

    The fields are named rather than diffed first: a merge patch that changes
    nothing creates no revision, so specstar's own no-op detection replaces the
    pre-#434 "read the row, compare, skip" loop, which had to load every row's full
    data blob just to decide it had nothing to do.

    Two outcomes are NOT errors on their own and one is:

    * A row whose revision moved between selection and write is a CONFLICT and is
      left alone. Expected here — indexing writes to the same rows — and the patch
      is idempotent, so re-running picks it up. Hence one retry.
    * A row that could not be written is a FAILURE. ``patch_many`` collects those
      instead of raising, which would quietly turn a partial fan-out into a
      reported success — and a row left on the OLD, looser mirror while the caller
      believes the tightening landed is a read leak. So anything still unwritten
      after the retry raises.

    Runs as ``as_user`` (the collection owner) so each row keeps its own
    ``created_by`` while ``updated_by`` records the fan-out. Blocking — the caller
    runs it off the event loop.
    """
    rm = spec.get_resource_manager(model)
    patch = MergePatch(dict(fields))
    result = rm.patch_many(query, patch, user=as_user)
    moved = result.patched
    if result.conflicts:
        retry = rm.patch_many(query, patch, user=as_user)
        moved += retry.patched
        result = retry
    if result.conflicts or result.failures:
        raise RuntimeError(
            f"{subject}: the permission mirror did not reach every row — "
            f"conflicts={result.conflicts} failures={result.failures}"
        )
    return moved


def push_mirror_to_docs(
    spec: SpecStar,
    collection_id: str,
    *,
    visibility: str,
    read_meta: list[str],
    created_by: str,
) -> int:
    """Re-push the collection's read-visibility mirror onto every SourceDoc in it
    (the #303 fan-out) — ONE ``patch_many`` (specstar #434), run OFF the event loop
    by the caller. Runs as ``created_by`` (the collection owner) so each doc keeps
    its own ``created_by`` while ``updated_by`` records the fan-out. Returns the
    number of docs actually moved.

    The mirror fields are named explicitly rather than diffed first: a merge patch
    that changes nothing creates no revision, so specstar's own no-op detection
    replaces the pre-#434 "read the doc, compare, skip" loop — which had to load
    every doc's full data blob (multi-KB of extracted text) just to decide it had
    nothing to do.

    See ``_fan_out`` for the conflict / failure rule every mirror push shares."""
    return _fan_out(
        spec,
        SourceDoc,
        (QB["collection_id"] == collection_id).build(),
        {
            "collection_visibility": visibility,
            "collection_read_meta": list(read_meta),
            "collection_created_by": created_by,
        },
        as_user=created_by,
        subject=f"collection {collection_id} → its docs",
    )


def push_mirror_to_claims(
    spec: SpecStar,
    collection_id: str,
    *,
    visibility: str,
    read_meta: list[str],
    created_by: str,
) -> int:
    """#534 slice 2 — the same collection push, one level further down: every
    ``GraphClaim`` extracted out of this collection carries its own copy of the
    collection verdict, so a collection tightened AFTER extraction leaves every
    claim on the old, looser mirror until this runs.

    Keyed on the collection, not on each doc, so a collection with thousands of
    claims is still one query. The doc half of a claim's mirror is untouched here —
    a collection change never alters a deck's own override."""
    return _fan_out(
        spec,
        GraphClaim,
        (QB["collection_id"] == collection_id).build(),
        {
            "collection_visibility": visibility,
            "collection_read_meta": list(read_meta),
            "collection_created_by": created_by,
        },
        as_user=created_by,
        subject=f"collection {collection_id} → its claims",
    )


def reset_doc_override_on_claims(
    spec: SpecStar,
    collection_id: str,
    *,
    created_by: str,
) -> int:
    """#534 slice 2 — set every claim in a collection to "the deck adds no
    restriction", the BROAD half of the reconcile's broad-then-narrow pass.

    Only the reconcile may call this. It deliberately clobbers the doc half for the
    whole collection, which is safe there because the narrow pass immediately
    re-applies each deck that really does carry an override — and is exactly why
    the collection permission endpoint must NOT use it: that path has no narrow
    pass, so it would quietly drop every per-deck tightening in the collection.
    """
    return _fan_out(
        spec,
        GraphClaim,
        (QB["collection_id"] == collection_id).build(),
        {"doc_visibility": "public", "doc_read_content": []},
        as_user=created_by,
        subject=f"collection {collection_id} → its claims (doc-override reset)",
    )


def push_doc_override_to_claims(
    spec: SpecStar,
    source_doc_id: str,
    *,
    visibility: str,
    read_content: list[str],
    created_by: str,
) -> int:
    """#534 slice 2 — push ONE deck's own tightening (#308) onto the claims
    extracted from it. Keyed on the deck, so tightening one deck never touches its
    neighbours' metrics.

    ``visibility="public"`` is how an override is CLEARED: the mirror always states
    the verdict outright, because a blank reads as "never written" and hides the
    row. Callers pass the deck's effective verdict, not a flag."""
    return _fan_out(
        spec,
        GraphClaim,
        (QB["source_doc_id"] == source_doc_id).build(),
        {"doc_visibility": visibility, "doc_read_content": list(read_content)},
        as_user=created_by,
        subject=f"doc {source_doc_id} → its claims",
    )
