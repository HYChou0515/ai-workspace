"""#262 — the read/write visibility predicate fed to specstar's `access_scope`.

`access_scope` (specstar ≥ 0.11.11) ANDs this into every request-originated read
(every GET variant + list/search/count) and gates every write, at the storage
layer — so a resource the caller can't `read_meta` is a uniform 404 with no
per-route wiring. The finer per-verb write ACLs (edit_content / owner-only
delete) ride a `permission_checker` on top (403). See docs/plan-permissions.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from specstar import QB, UNRESTRICTED
from specstar.permission import AccessScope

from .model import ALL, Subject, user_subject

if TYPE_CHECKING:
    from specstar.permission.access_scope import _Unrestricted
    from specstar.query import ConditionBuilder


def subjects_of(user: str) -> list[Subject]:
    """The grant targets that resolve to `user` (groups land here later)."""
    return [user_subject(user), ALL]


def _visibility_scope(
    *,
    visibility_field: str,
    read_meta_field: str,
    owner_field: str | None,
    superusers: frozenset[str],
) -> AccessScope:
    """The shared read/list predicate behind every resource's access_scope.

    A row is visible iff it is public, owned by the caller, or
    restricted-and-granted — the storage-layer mirror of
    `authorize(..., "read_meta", ...)`. `visibility_field`/`read_meta_field` name
    the indexed columns carrying the (possibly denormalized) visibility +
    read_meta grant list; `owner_field` is the indexed column the caller is
    matched against for the owner branch — `None` means use specstar's own
    `created_by` meta (the resource's real owner), a column name means a
    denormalized owner (e.g. a doc mirroring its collection's owner). An absent
    visibility ≡ public (legacy / un-backfilled rows). Superusers see everything.
    """

    def scope(user: str) -> ConditionBuilder | _Unrestricted:
        if user in superusers:
            return UNRESTRICTED  # the single greppable "see everything" path
        owner = QB.created_by() == user if owner_field is None else QB[owner_field] == user
        granted = QB[read_meta_field].contains_any(subjects_of(user))
        return (
            QB[visibility_field].is_null()  # absent visibility ≡ public
            | (QB[visibility_field] == "public")
            | owner
            | ((QB[visibility_field] == "restricted") & granted)
        )

    return scope


def collection_access_scope(
    superusers: frozenset[str] = frozenset(),
) -> AccessScope:
    """Build the `user -> predicate` access scope for collections. Mirrors
    `authorize(..., "read_meta", ...)`: a row is visible iff it is public,
    owned by the caller, or restricted-and-granted. No `permission` object ≡
    public (legacy rows, no migration). Superusers see everything."""
    return _visibility_scope(
        visibility_field="permission.visibility",
        read_meta_field="permission.read_meta",
        owner_field=None,  # the collection's own specstar `created_by`
        superusers=superusers,
    )


def source_doc_access_scope(
    superusers: frozenset[str] = frozenset(),
) -> AccessScope:
    """#303: a SourceDoc inherits its collection's read visibility. The doc
    carries a DENORMALIZED mirror of the collection's visibility / read_meta /
    created_by (`collection_*` fields, kept current by doc-create + a fan-out on
    collection permission change), so the SAME predicate that hides a collection
    hides its docs — at the storage layer, covering the auto-CRUD
    `GET /source-doc/{id}`. Matched against the mirrored `collection_created_by`
    (the collection owner), NOT the doc's own uploader."""
    return _visibility_scope(
        visibility_field="collection_visibility",
        read_meta_field="collection_read_meta",
        owner_field="collection_created_by",
        superusers=superusers,
    )
