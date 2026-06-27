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


def collection_access_scope(
    superusers: frozenset[str] = frozenset(),
) -> AccessScope:
    """Build the `user -> predicate` access scope for collections. Mirrors
    `authorize(..., "read_meta", ...)`: a row is visible iff it is public,
    owned by the caller, or restricted-and-granted. No `permission` object ≡
    public (legacy rows, no migration). Superusers see everything."""

    def scope(user: str) -> ConditionBuilder | _Unrestricted:
        if user in superusers:
            return UNRESTRICTED  # the single greppable "see everything" path
        granted = QB["permission.read_meta"].contains_any(subjects_of(user))
        return (
            QB["permission.visibility"].is_null()  # absent Permission ≡ public
            | (QB["permission.visibility"] == "public")
            | (QB.created_by() == user)
            | ((QB["permission.visibility"] == "restricted") & granted)
        )

    return scope
