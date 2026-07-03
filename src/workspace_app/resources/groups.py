"""#307 — a first-class logical `Group` (minimal viable).

A `Group` is a flat, owner-managed bag of user ids — no nesting, no IdP sync yet
(the `group:` `Subject` namespace was reserved at #262 so adding this needs no
data migration). Granting `group:<id>` on a resource's `Permission` then covers
every current member: `groups_of` resolves a user → the groups they're in, which
the caller folds into the `Actor.groups` / `subjects_of` used by `authorize` and
`access_scope`.

Membership is the ONLY authority — a group has an owner (`created_by`) who adds /
removes members; there's no per-group `Permission` (and thus no access_scope), so
resolving a user's groups can't recurse back into a permission check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from msgspec import Struct, field
from specstar import QB

if TYPE_CHECKING:
    from specstar import SpecStar


class Group(Struct):  # → resource "group"
    """A named set of users. `created_by` (specstar meta) is the owner who manages
    membership; `members` are the user ids in the group."""

    name: str
    description: str = ""
    members: list[str] = field(default_factory=list)


def groups_of(spec: SpecStar, user: str) -> frozenset[str]:
    """The ids of every `Group` that lists `user` as a member — an indexed
    `members.contains(user)` query (not a scan). Folded into the caller's
    `Actor.groups` / `subjects_of` so a `group:<id>` grant resolves to its
    members. An empty result (the common no-groups case) is cheap and leaves
    authorization exactly as it was before groups existed."""
    rm = spec.get_resource_manager(Group)
    return frozenset(
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources(QB["members"].contains(user).build())
    )
