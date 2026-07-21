"""#262 — per-verb WRITE authorization on a Collection (the 403 layer that rides
on top of ``access_scope``'s 404 visibility layer).

``access_scope`` (perm.scope) already turns a row the caller can't ``read_meta``
into a uniform 404, so this checker only runs for rows the caller can already see.
Its job is the finer "may I do THIS action?" decision:

* ``update`` / ``modify`` / ``patch`` → ``write_meta``. A write that also changes
  the embedded ``permission`` additionally requires ``change_permission`` — so the
  generic ``PUT`` / ``PATCH /collection/{id}`` can't be used to rewire access
  control (the dedicated ``PUT …/permission`` endpoint is the only metadata path
  that carries ``change_permission``).
* ``delete`` / ``permanently_delete`` / ``switch`` / ``restore`` → owner or
  superuser only (a read/write member can't destroy the collection — the FE's
  delete button hits ``DELETE /collection/{id}/permanently``).

Reads (``get`` / ``search``) and ``create`` return ``allow`` (no opinion) — reads
are governed by ``access_scope``, and create has no existing row to authorize
against. This is why we DON'T use ``ActionBasedPermissionChecker``: it returns
``not_applicable`` for unmapped actions, and specstar's ``PermissionEventHandler``
treats anything other than ``allow`` as a denial — which would 403 every read and
create on the collection.

WIRING (specstar 0.11.11 caveat): ``add_model(permission_checker=…)`` does NOT
work — ``ResourceManager`` is built with ``self.permission_checker or
permission_checker`` and the spec-level default is a truthy ``AllowAll()``, so the
per-model checker is always shadowed (only ``access_scope`` is threaded straight
through). We therefore attach the checker via the per-model ``event_handlers`` slot
(``self.event_handlers`` defaults to ``None`` ⇒ falsy ⇒ the per-model list wins),
wrapping it in specstar's ``PermissionEventHandler`` so its
``required_resource_parts`` are aggregated and ``current_resource`` (META + DATA)
is loaded for the write check. See ``collection_permission_event_handler``.

Unlike the intended (shadowed) ``permission_checker`` — which specstar would fire
ONLY on request-originated auto-CRUD routes — an event handler fires on EVERY
``ResourceManager`` write, including programmatic ones. The only programmatic
non-create Collection write is the code-repo sync's git-metadata stamp, which acts
as the collection owner (see ``kb.code_repo``) so it passes ``write_meta``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from msgspec import UNSET
from specstar.permission import IPermissionChecker, PermissionResult, ResourcePart
from specstar.resource_manager.core import PermissionEventHandler
from specstar.types import ResourceAction

from .authorize import Actor, authorize
from .model import Permission
from .scope import GroupsProvider

# Write actions gated by ``write_meta`` (a permission rewrite escalates to
# ``change_permission``); they carry the would-be new data + the stored snapshot.
_WRITE_META_ACTIONS: frozenset[ResourceAction] = frozenset(
    {ResourceAction.update, ResourceAction.modify, ResourceAction.patch}
)
# Lifecycle actions only the owner (or a superuser) may take.
_OWNER_ACTIONS: frozenset[ResourceAction] = frozenset(
    {
        ResourceAction.delete,
        ResourceAction.permanently_delete,
        ResourceAction.switch,
        ResourceAction.restore,
    }
)

logger = logging.getLogger(__name__)


def _current(context: Any) -> Any | None:
    """The pre-write resource snapshot specstar loads (``META`` + ``DATA``), or
    ``None`` when absent (no row → nothing to authorize against → deny)."""
    cur = getattr(context, "current_resource", UNSET)
    if cur is UNSET or cur is None:
        return None
    return cur


def _stored_permission(snapshot: Any) -> Permission | None:
    perm = getattr(snapshot.data, "permission", None)
    return perm if isinstance(perm, Permission) else None


def _context_user(context: Any) -> str:
    user = getattr(context, "user", "")
    return user if isinstance(user, str) else ""


def _patch_touches_permission(patch: Any) -> bool:
    """True when a PATCH body names the ``permission`` field, across both patch
    flavors: RFC 7386 merge (``MergePatch``, a ``dict`` subclass → key membership)
    and RFC 6902 JSON-patch (``.patch`` = a list of ops with a ``/permission…``
    path)."""
    if isinstance(patch, dict):
        return "permission" in patch
    ops = getattr(patch, "patch", None)
    if isinstance(ops, list):
        return any(
            isinstance(op, dict) and str(op.get("path", "")).startswith("/permission") for op in ops
        )
    return False


class CollectionPermissionChecker(IPermissionChecker):
    """Allow-by-default checker enforcing the per-verb write/lifecycle ACL on a
    Collection. See the module docstring for the verb mapping + wiring rationale."""

    def __init__(
        self,
        superusers: frozenset[str] = frozenset(),
        groups_provider: GroupsProvider | None = None,
        *,
        absent_permission: Callable[[Any], Permission | None] | None = None,
    ) -> None:
        self._superusers = superusers
        # #307: resolve the acting user → their group ids so a `group:<id>` grant
        # authorizes the write. Injected at registration (needs a SpecStar); `None`
        # ⇒ pre-groups behaviour (the user's own subject only).
        self._groups_provider = groups_provider
        # How to interpret a row whose ``permission`` field is absent (``None``).
        # Default (``None``) ⇒ passthrough: ``authorize`` treats an absent
        # permission as ``public`` (Collection / WorkItem back-compat). A resource
        # whose absent-permission default is NOT public (a KbChat is owner-only)
        # injects a factory that synthesises the effective ``Permission`` from the
        # stored row (e.g. its legacy ``shared_with``), so the write ACL matches
        # its ``access_scope`` instead of silently falling back to world-writable.
        self._absent_permission = absent_permission

    def _actor(self, user: str) -> Actor:
        groups = self._groups_provider(user) if self._groups_provider is not None else frozenset()
        return Actor.human(user, groups=groups)

    def _effective(self, snap: Any) -> Permission | None:
        """The permission to authorize against: the stored one, or — when absent
        and a resource-specific default is configured — the synthesised effective
        permission for this row (see ``absent_permission``)."""
        stored = _stored_permission(snap)
        if stored is None and self._absent_permission is not None:
            return self._absent_permission(snap.data)
        return stored

    def check_permission(self, context: Any) -> PermissionResult:
        action = getattr(context, "action", None)
        if action in _WRITE_META_ACTIONS:
            return self._check_write(context)
        if action in _OWNER_ACTIONS:
            return self._check_owner(context)
        # reads / create / everything else — access_scope governs reads, and
        # create has no current row; voice no opinion (allow).
        return PermissionResult.allow

    def required_resource_parts(self, action: ResourceAction) -> frozenset[ResourcePart]:
        if action in _WRITE_META_ACTIONS:
            return frozenset({ResourcePart.META, ResourcePart.DATA})
        if action in _OWNER_ACTIONS:
            return frozenset({ResourcePart.META})
        return frozenset()

    def _rewrites_permission(self, context: Any, stored: Permission | None) -> bool:
        """Whether this write would change the access-control object — a full-body
        write (update/modify) whose ``permission`` differs, or a patch that names
        ``permission`` at all (we can't cheaply diff a patch, so any mention is
        treated as a rewire)."""
        if getattr(context, "action", None) == ResourceAction.patch:
            return _patch_touches_permission(getattr(context, "patch_data", UNSET))
        new = getattr(context, "data", UNSET)
        if new is UNSET or new is None:
            return False
        new_perm = getattr(new, "permission", UNSET)
        return new_perm is not UNSET and new_perm != stored

    def _check_write(self, context: Any) -> PermissionResult:
        snap = _current(context)
        if snap is None:
            return PermissionResult.deny
        created_by = snap.meta.created_by
        stored = self._effective(snap)
        actor = self._actor(_context_user(context))
        if self._rewrites_permission(context, stored) and not authorize(
            actor, "change_permission", stored, created_by=created_by, superusers=self._superusers
        ):
            logger.warning(
                "checker: change_permission denied for %s on collection owned by %s",
                actor.user_id,
                created_by,
            )
            return PermissionResult.deny
        ok = authorize(
            actor, "write_meta", stored, created_by=created_by, superusers=self._superusers
        )
        logger.debug(
            "checker: collection write_meta by %s (owner %s) -> ok=%s",
            actor.user_id,
            created_by,
            ok,
        )
        return PermissionResult.allow if ok else PermissionResult.deny

    def _check_owner(self, context: Any) -> PermissionResult:
        snap = _current(context)
        if snap is None:
            return PermissionResult.deny
        user = _context_user(context)
        if user == snap.meta.created_by or user in self._superusers:
            return PermissionResult.allow
        logger.warning(
            "checker: owner-only action denied for %s (collection owner %s)",
            user,
            snap.meta.created_by,
        )
        return PermissionResult.deny


def collection_permission_event_handler(
    superusers: frozenset[str] = frozenset(),
    groups_provider: GroupsProvider | None = None,
) -> PermissionEventHandler:
    """Wrap the Collection write ACL in specstar's ``PermissionEventHandler`` for
    the per-model ``event_handlers`` slot (the ``permission_checker`` slot is
    shadowed — see module docstring). `groups_provider` (#307) resolves the acting
    user's groups so a `group:<id>` write grant is honoured."""
    return PermissionEventHandler(CollectionPermissionChecker(superusers, groups_provider))


class SourceDocPermissionChecker(IPermissionChecker):
    """#308 — the anti-bypass for a SourceDoc's per-doc read override. Allow-by-
    default; the ONE write it gates is a change to the doc's own ``permission``
    field (the override): only the collection owner (the mirrored
    ``collection_created_by``) or a superuser may set/clear it — so the owner-only
    rule the dedicated ``PUT /kb/documents/{id}/permission`` enforces can't be
    bypassed through the auto-CRUD ``PUT /source-doc/{id}``. Every OTHER write
    (ingest create, the collection→doc mirror fan-out, re-index, status bumps) is
    allowed and this checker never denies it — they don't touch ``permission``, so
    ``_rewrites_permission`` is false and we return ``allow`` before any owner
    check. Unlike ``CollectionPermissionChecker`` this does NOT gate general
    ``write_meta`` (that's out of #308's scope and would break those programmatic
    doc writes)."""

    def __init__(self, superusers: frozenset[str] = frozenset()) -> None:
        self._superusers = superusers

    def check_permission(self, context: Any) -> PermissionResult:
        if getattr(context, "action", None) not in _WRITE_META_ACTIONS:
            return PermissionResult.allow  # create / read / lifecycle — not our concern
        snap = _current(context)
        if snap is None or not self._rewrites_permission(context, _stored_permission(snap)):
            return PermissionResult.allow  # no row, or the write leaves `permission` untouched
        user = _context_user(context)
        owner = getattr(snap.data, "collection_created_by", "")
        if user == owner or user in self._superusers:
            return PermissionResult.allow
        logger.warning(
            "checker: source-doc permission override change denied for %s (collection owner %s)",
            user,
            owner,
        )
        return PermissionResult.deny

    def required_resource_parts(self, action: ResourceAction) -> frozenset[ResourcePart]:
        if action in _WRITE_META_ACTIONS:
            return frozenset({ResourcePart.META, ResourcePart.DATA})
        return frozenset()

    def _rewrites_permission(self, context: Any, stored: Permission | None) -> bool:
        """Whether this write changes the doc's ``permission`` — a full-body write
        (update/modify) whose ``permission`` differs from the stored one, or a
        patch naming ``permission`` (reusing the Collection checker's helpers, which
        are resource-agnostic)."""
        if getattr(context, "action", None) == ResourceAction.patch:
            return _patch_touches_permission(getattr(context, "patch_data", UNSET))
        new = getattr(context, "data", UNSET)
        if new is UNSET or new is None:
            return False
        new_perm = getattr(new, "permission", UNSET)
        return new_perm is not UNSET and new_perm != stored


class GraphMirrorChecker(IPermissionChecker):
    """#534 — an extracted row may not claim a permission its collection does not
    grant.

    The read side is driven entirely by the mirror each evidence row carries, so
    whoever can write a row with a mirror of their choosing can publish a fact of
    their choosing to everyone. The auto-CRUD create route is open to any signed-in
    caller, which is the attack: POST a claim into someone's private collection,
    stamp the mirror "public", and everybody can read it.

    The check deliberately does not ask WHO is writing. The extraction job runs as
    an ordinary user and must keep working, and any rule phrased around identity
    would have to name it — a rule that has to enumerate its friends breaks the
    day someone adds a worker. It asks instead whether the mirror TELLS THE TRUTH
    about the collection it names, which the extractor satisfies for free because
    it copies the mirror from the document rather than composing one.

    An UNWRITTEN mirror passes: it is not a lie, and the read side already hides
    such a row from everyone. Refusing it would break the fail-closed default that
    lets a forgetful writer lose rows loudly instead of publishing them silently.
    """

    def __init__(self, resolve_collection: Callable[[str], Permission | None]) -> None:
        self._resolve = resolve_collection

    def check_permission(self, context: Any) -> PermissionResult:
        data = getattr(context, "data", None) or getattr(context, "new_data", None)
        claimed = getattr(data, "collection_visibility", None)
        if not isinstance(claimed, str) or not claimed:
            return PermissionResult.allow  # no mirror written — not a claim about anything
        collection_id = getattr(data, "collection_id", "")
        actual = self._resolve(collection_id) if isinstance(collection_id, str) else None
        truth = "public" if actual is None else actual.visibility
        if claimed == truth:
            return PermissionResult.allow
        logger.warning(
            "checker: graph row claims visibility %r for collection %s, which is %r",
            claimed,
            collection_id,
            truth,
        )
        return PermissionResult.deny


def graph_mirror_event_handler(
    resolve_collection: Callable[[str], Permission | None],
) -> PermissionEventHandler:
    """#534 — wrap the mirror-truthfulness check for the per-model
    ``event_handlers`` slot (the ``permission_checker`` slot is shadowed by
    specstar's spec-level default — see the module docstring)."""
    return PermissionEventHandler(GraphMirrorChecker(resolve_collection))


def source_doc_permission_event_handler(
    superusers: frozenset[str] = frozenset(),
) -> PermissionEventHandler:
    """#308 — wrap the SourceDoc override anti-bypass checker for the per-model
    ``event_handlers`` slot (the ``permission_checker`` slot is shadowed by
    specstar's spec-level default — see module docstring). Fires on EVERY SourceDoc
    write, but only ever denies a ``permission``-field change by a non-owner; the
    high-volume programmatic writes (ingest / index / mirror fan-out) never touch
    ``permission`` and pass straight through."""
    return PermissionEventHandler(SourceDocPermissionChecker(superusers))


def work_item_permission_event_handler(
    superusers: frozenset[str] = frozenset(),
) -> PermissionEventHandler:
    """#306 — an App WorkItem's write ACL is the SAME per-verb mapping as a
    collection (``update`` / ``modify`` / ``patch`` → ``write_meta``, incl. the
    lifecycle *close* which is an update; ``delete`` / ``switch`` / ``restore`` →
    owner-only) and reads the same embedded ``permission`` off the row, so it
    reuses the resource-agnostic ``CollectionPermissionChecker``. Attached via the
    per-model ``event_handlers`` slot for the same reason the collection one is —
    the ``permission_checker`` slot is shadowed by specstar's spec-level default
    (see module docstring). Fires on EVERY WorkItem write, incl. programmatic ones
    (item create → ``allow``; the lifecycle close runs as the acting user, who
    needs ``write_meta``)."""
    return PermissionEventHandler(CollectionPermissionChecker(superusers))
