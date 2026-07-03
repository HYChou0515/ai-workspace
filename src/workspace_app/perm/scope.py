"""#262 — the read/write visibility predicate fed to specstar's `access_scope`.

`access_scope` (specstar ≥ 0.11.11) ANDs this into every request-originated read
(every GET variant + list/search/count) and gates every write, at the storage
layer — so a resource the caller can't `read_meta` is a uniform 404 with no
per-route wiring. The finer per-verb write ACLs (edit_content / owner-only
delete) ride a `permission_checker` on top (403). See docs/plan-permissions.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from specstar import QB, UNRESTRICTED
from specstar.permission import AccessScope

from .model import ALL, Subject, group_subject, user_subject

if TYPE_CHECKING:
    from specstar.permission.access_scope import _Unrestricted
    from specstar.query import ConditionBuilder

# #307: resolve a user → the group ids they belong to. Injected at registration
# (it needs a `SpecStar` to query the `Group` resource) so `perm/` stays free of a
# resource import; `None` ⇒ the pre-groups behaviour (user + `all` only).
GroupsProvider = Callable[[str], frozenset[str]]


def subjects_of(user: str, groups: Iterable[str] = ()) -> list[Subject]:
    """The grant targets that resolve to `user`: their own `user:` subject, a
    `group:` subject per group they're in (#307), and the `all` wildcard."""
    return [user_subject(user), *(group_subject(g) for g in groups), ALL]


def _visibility_scope(
    *,
    visibility_field: str,
    read_meta_field: str,
    owner_field: str | None,
    superusers: frozenset[str],
    groups_provider: GroupsProvider | None = None,
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
    `groups_provider` (#307) resolves the reader's group ids so a grant to one of
    their groups also matches; `None` ⇒ the pre-groups behaviour (user + `all`).
    """

    def scope(user: str) -> ConditionBuilder | _Unrestricted:
        if user in superusers:
            return UNRESTRICTED  # the single greppable "see everything" path
        groups = groups_provider(user) if groups_provider is not None else frozenset()
        owner = QB.created_by() == user if owner_field is None else QB[owner_field] == user
        granted = QB[read_meta_field].contains_any(subjects_of(user, groups))
        return (
            QB[visibility_field].is_null()  # absent visibility ≡ public
            | (QB[visibility_field] == "public")
            | owner
            | ((QB[visibility_field] == "restricted") & granted)
        )

    return scope


def collection_access_scope(
    superusers: frozenset[str] = frozenset(),
    groups_provider: GroupsProvider | None = None,
) -> AccessScope:
    """Build the `user -> predicate` access scope for collections. Mirrors
    `authorize(..., "read_meta", ...)`: a row is visible iff it is public,
    owned by the caller, or restricted-and-granted (to the user OR one of their
    groups, #307). No `permission` object ≡ public (legacy rows, no migration).
    Superusers see everything."""
    return _visibility_scope(
        visibility_field="permission.visibility",
        read_meta_field="permission.read_meta",
        owner_field=None,  # the collection's own specstar `created_by`
        superusers=superusers,
        groups_provider=groups_provider,
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


def kbchat_access_scope(
    superusers: frozenset[str] = frozenset(),
) -> AccessScope:
    """#304 — KbChat read/list visibility. UNLIKE collections, a chat with no
    ``Permission`` is PRIVATE (owner-only), not public — a chat isn't open to
    everyone. Visible iff: owner, a superuser, ``public``, ``restricted`` +
    granted read_meta, OR (a pre-#304 row with no permission yet) still in the
    legacy ``shared_with`` — the fallback that keeps old shares readable until an
    operator migrates them (then ``shared_with`` is cleared and this clause goes
    inert)."""

    def scope(user: str) -> ConditionBuilder | _Unrestricted:
        if user in superusers:
            return UNRESTRICTED
        granted = QB["permission.read_meta"].contains_any(subjects_of(user))
        return (
            (QB.created_by() == user)  # the owner — absent-permission ≡ private
            | (QB["permission.visibility"] == "public")
            | ((QB["permission.visibility"] == "restricted") & granted)
            | (QB["permission.visibility"].is_null() & QB["shared_with"].contains(user))
        )

    return scope


def work_item_access_scope(
    superusers: frozenset[str] = frozenset(),
) -> AccessScope:
    """#306 — an App WorkItem carries the SAME embedded ``Permission`` as a
    collection (``permission.visibility`` / ``permission.read_meta`` + the real
    ``created_by`` owner), so its read/list visibility is the identical predicate.
    A thin delegate keeps that logic written once (plan-permissions.md: "written
    once, generically")."""
    return collection_access_scope(superusers)
