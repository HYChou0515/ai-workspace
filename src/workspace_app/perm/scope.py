"""#262 — the read/write visibility predicate fed to specstar's `access_scope`.

`access_scope` (specstar ≥ 0.11.11) ANDs this into every request-originated read
(every GET variant + list/search/count) and gates every write, at the storage
layer — so a resource the caller can't `read_meta` is a uniform 404 with no
per-route wiring. The finer per-verb write ACLs (edit_content / owner-only
delete) ride a `permission_checker` on top (403). See docs/plan-permissions.md.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
            logger.debug("scope: superuser %s -> unrestricted (%s)", user, visibility_field)
            return UNRESTRICTED  # the single greppable "see everything" path
        groups = groups_provider(user) if groups_provider is not None else frozenset()
        owner = QB.created_by() == user if owner_field is None else QB[owner_field] == user
        granted = QB[read_meta_field].contains_any(subjects_of(user, groups))
        return (
            # An ABSENT visibility index cell ≡ public (legacy / un-backfilled
            # rows). This MUST be `isna()` (absent-OR-json-null), NOT `is_null()`
            # (present-AND-json-null): on postgres/sqlite a field added to
            # `indexed_fields` after a row was written has NO cell for that row,
            # and `is_null()` does not match an absent cell — so a pre-#303/#308
            # SourceDoc (or pre-#262 collection) never run through the migrate
            # backfill would be HIDDEN from every non-owner even though its
            # collection is public (the "visible in list + viewer, 404 on open"
            # bug, #494). The in-memory test backend treats absent as null so
            # `is_null()` passed there — which is exactly why this regressed
            # unseen; real backends do not. `isna()` is a no-op for a row that
            # DOES carry the cell (a fresh doc mirrors "public"), so this only
            # ever admits legacy rows the mirror never reached.
            QB[visibility_field].isna()
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


def _and_scopes(a: AccessScope, b: AccessScope) -> AccessScope:
    """Two access scopes ANDed: a row is visible iff BOTH admit it. A half that
    imposes no restriction (``UNRESTRICTED`` for a superuser, or ``None`` — the
    specstar "no predicate" reading; our own halves never return it) drops out and
    the other half decides; otherwise the two predicates combine with ``&``."""

    def scope(user: str) -> ConditionBuilder | None | _Unrestricted:
        pa = a(user)
        pb = b(user)
        if pa is UNRESTRICTED or pa is None:
            return pb
        if pb is UNRESTRICTED or pb is None:
            return pa
        # Both are real predicates here (the UNRESTRICTED/None branches returned);
        # ty can't narrow the `_Unrestricted` singleton out of an `is` check.
        return pa & pb  # ty: ignore[unsupported-operator]

    return scope


def source_doc_override_scope(
    superusers: frozenset[str] = frozenset(),
    groups_provider: GroupsProvider | None = None,
) -> AccessScope:
    """#308: a doc's OWN per-doc read override, on TOP of the inherited collection
    mirror. The SAME ``_visibility_scope`` over the doc's own ``permission.*``
    fields, with the OWNER branch matched against the mirrored
    ``collection_created_by`` (the collection owner — the override's authority, who
    always sees the doc; the doc's uploader is NOT special, per #308/D4). A doc
    with no override (``permission is None`` → absent/null ``permission.visibility``)
    passes via the ``isna()`` clause, so this predicate only ever HIDES an
    explicitly-overridden doc — the intersect that can tighten, never loosen."""
    return _visibility_scope(
        visibility_field="permission.visibility",
        read_meta_field="permission.read_meta",
        owner_field="collection_created_by",
        superusers=superusers,
        groups_provider=groups_provider,
    )


def source_doc_access_scope(
    superusers: frozenset[str] = frozenset(),
    groups_provider: GroupsProvider | None = None,
) -> AccessScope:
    """#303 + #308: a SourceDoc is visible iff its COLLECTION admits it (the #303
    denormalized ``collection_*`` mirror — the SAME predicate that hides a
    collection hides its docs, at the storage layer covering the auto-CRUD
    ``GET /source-doc/{id}``) AND its OWN per-doc override admits it (#308). The two
    are ANDed so an override can only TIGHTEN: a doc the caller can't reach through
    the collection stays hidden, and one the collection admits can be further
    hidden by its own override. Both halves match the caller against the mirrored
    ``collection_created_by`` (the collection owner), NOT the doc's uploader.
    ``groups_provider`` (#307) resolves a ``group:<id>`` grant on EITHER half —
    closing the #303 gap where the doc scope ignored groups (#308/D7)."""
    collection = _visibility_scope(
        visibility_field="collection_visibility",
        read_meta_field="collection_read_meta",
        owner_field="collection_created_by",
        superusers=superusers,
        groups_provider=groups_provider,
    )
    return _and_scopes(collection, source_doc_override_scope(superusers, groups_provider))


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
            logger.debug("scope: kbchat superuser %s -> unrestricted", user)
            return UNRESTRICTED
        granted = QB["permission.read_meta"].contains_any(subjects_of(user))
        return (
            (QB.created_by() == user)  # the owner — absent-permission ≡ private
            | (QB["permission.visibility"] == "public")
            | ((QB["permission.visibility"] == "restricted") & granted)
            # `isna()` (absent-OR-null), NOT `is_null()`: a pre-#304 chat whose
            # `permission.visibility` cell was never indexed has it ABSENT, and
            # `is_null()` misses an absent cell on postgres/sqlite — which would
            # make its legacy `shared_with` grants unreadable until migrated (the
            # #494 footgun class). `isna()` keeps the legacy share readable.
            | (QB["permission.visibility"].isna() & QB["shared_with"].contains(user))
        )

    return scope


def conversation_access_scope(
    superusers: frozenset[str] = frozenset(),
    groups_provider: GroupsProvider | None = None,
) -> AccessScope:
    """#306 PR3 — a Conversation (chat thread) is visible iff its OWNING ITEM admits
    the caller to ``read_chat``. Conversation is served by its own auto-CRUD (the
    item scope never covers it), so it gates on the SAME ``_visibility_scope`` over
    the denormalized ``item_*`` mirror the chat carries — visibility + the item's
    ``read_chat`` grant list + the mirrored item owner. A pre-#306 conversation
    (absent mirror cell) passes via ``isna()`` ≡ public, so legacy threads stay
    readable until the next stamp. The mirror is pushed at chat-create and re-pushed
    on item permission / member changes."""
    return _visibility_scope(
        visibility_field="item_visibility",
        read_meta_field="item_read_chat",
        owner_field="item_created_by",
        superusers=superusers,
        groups_provider=groups_provider,
    )


def work_item_access_scope(
    superusers: frozenset[str] = frozenset(),
) -> AccessScope:
    """#306 — an App WorkItem carries the SAME embedded ``Permission`` as a
    collection (``permission.visibility`` / ``permission.read_meta`` + the real
    ``created_by`` owner), so its read/list visibility is the identical predicate.
    A thin delegate keeps that logic written once (plan-permissions.md: "written
    once, generically")."""
    return collection_access_scope(superusers)
