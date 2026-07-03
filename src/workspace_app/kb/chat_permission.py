"""#304 — the KbChat permission bridge: legacy ``shared_with`` ⇄ ``Permission``.

A KbChat's absent-permission default is PRIVATE (owner-only), unlike a Collection
/ WorkItem whose absent ≡ public. This module is the single source of truth for
what a pre-#304 chat's legacy ``shared_with`` list is *equivalent* to as a
first-class ``Permission`` — used by BOTH the one-off migration (``resources``)
and the live route / write-ACL fallback so a migrated and an un-migrated chat
behave identically. When the migration has run (``shared_with`` cleared, a real
``permission`` written), the fallback goes inert.
"""

from __future__ import annotations

from typing import Any

from specstar.resource_manager.core import PermissionEventHandler

from ..perm.checker import CollectionPermissionChecker
from ..perm.model import Permission, user_subject


def permission_from_shared_with(shared_with: list[str]) -> Permission:
    """The ``Permission`` a pre-#304 KbChat's legacy ``shared_with`` is equivalent
    to. A shared user was a read-only viewer (the old ``_require_owner`` blocked
    them from sending), so each becomes a ``read_meta`` + ``read_chat`` grant under
    ``restricted`` — NOT ``converse``. An unshared chat (empty list) is ``private``
    (owner-only), the KbChat absent-permission default."""
    subjects = [user_subject(u) for u in shared_with]
    if subjects:
        return Permission(visibility="restricted", read_meta=subjects, read_chat=list(subjects))
    return Permission(visibility="private")


def effective_permission(permission: Permission | None, shared_with: list[str]) -> Permission:
    """The permission to authorize a KbChat action against: the stored one, or —
    for a pre-#304 row that has none yet — the ``Permission`` its legacy
    ``shared_with`` is equivalent to. This is what lets a hand-written route reuse
    the generic ``authorize`` (whose ``None`` ≡ public would otherwise wrongly open
    a chat to everyone)."""
    return permission if permission is not None else permission_from_shared_with(shared_with)


def _absent_kbchat_permission(data: Any) -> Permission:
    """``absent_permission`` hook for the write ACL: a KbChat row with no stored
    ``permission`` is authorized against the effective permission of its legacy
    ``shared_with`` (private when unshared), never the world-writable ``public``
    default a bare ``None`` would imply."""
    return permission_from_shared_with(list(getattr(data, "shared_with", []) or []))


def kbchat_permission_event_handler(
    superusers: frozenset[str] = frozenset(),
) -> PermissionEventHandler:
    """#304 — gate a KbChat's AUTO-CRUD writes (``PUT`` / ``PATCH`` / ``DELETE
    /kb-chat/{id}``) with the resource-agnostic checker, but interpreting an absent
    permission as PRIVATE (via ``absent_permission``) instead of public: update /
    patch → ``write_meta``, delete → owner. The hand-written chat routes do the
    FINER per-verb checks (send = ``converse``, share/setter = ``change_permission``)
    at the route and run their own internal ``update`` AS THE OWNER, so this handler
    only stops an in-scope non-writer from mutating the chat straight through the
    auto-CRUD route (the path ``access_scope`` — reads only — doesn't cover)."""
    return PermissionEventHandler(
        CollectionPermissionChecker(superusers, absent_permission=_absent_kbchat_permission)
    )
