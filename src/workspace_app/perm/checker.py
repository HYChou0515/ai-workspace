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
            return PermissionResult.deny
        ok = authorize(
            actor, "write_meta", stored, created_by=created_by, superusers=self._superusers
        )
        return PermissionResult.allow if ok else PermissionResult.deny

    def _check_owner(self, context: Any) -> PermissionResult:
        snap = _current(context)
        if snap is None:
            return PermissionResult.deny
        user = _context_user(context)
        if user == snap.meta.created_by or user in self._superusers:
            return PermissionResult.allow
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
