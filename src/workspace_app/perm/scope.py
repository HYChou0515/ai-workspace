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


def collection_access_scope(
    superusers: frozenset[str] = frozenset(),
    groups_provider: GroupsProvider | None = None,
) -> AccessScope:
    """Build the `user -> predicate` access scope for collections. Mirrors
    `authorize(..., "read_meta", ...)`: a row is visible iff it is public,
    owned by the caller, or restricted-and-granted (to the user OR one of their
    groups, #307). No `permission` object ≡ public (legacy rows, no migration).
    Superusers see everything."""

    def scope(user: str) -> ConditionBuilder | _Unrestricted:
        if user in superusers:
            return UNRESTRICTED  # the single greppable "see everything" path
        groups = groups_provider(user) if groups_provider is not None else frozenset()
        granted = QB["permission.read_meta"].contains_any(subjects_of(user, groups))
        return (
            QB["permission.visibility"].is_null()  # absent Permission ≡ public
            | (QB["permission.visibility"] == "public")
            | (QB.created_by() == user)
            | ((QB["permission.visibility"] == "restricted") & granted)
        )

    return scope
