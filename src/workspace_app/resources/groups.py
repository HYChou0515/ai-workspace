"""#307 — a first-class logical `Group` (minimal viable). #608 adds a delegable
management model on top.

A `Group` is a flat bag of user ids — no nesting, no IdP sync yet (the `group:`
`Subject` namespace was reserved at #262 so adding this needs no data migration).
Granting `group:<id>` on a resource's `Permission` then covers every current
member: `groups_of` resolves a user → the groups they're in, which the caller
folds into the `Actor.groups` / `subjects_of` used by `authorize` and
`access_scope`.

Membership is the ONLY authority — there's no per-group `Permission` (and thus no
access_scope), so resolving a user's groups can't recurse back into a permission
check. WHO manages the group (#608):

  * ``owner`` — at most one. ``None`` ⇒ the record's ``created_by`` is the owner
    (the default, and every pre-#608 row — so no migration). The owner manages
    members AND maintainers, can transfer ownership, and can delete the group.
  * ``maintainers`` — a delegated list who may manage MEMBERS only (not each
    other, not deletion, not transfer — so delegation can't cascade).
  * a superuser bypasses all of the above (enforced in the routes).

Use `effective_owner(group, created_by)` — never read `group.owner` raw — so the
``None`` ⇒ creator fallback is applied in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from msgspec import Struct, field
from specstar import QB

if TYPE_CHECKING:
    from specstar import SpecStar


class Group(Struct):  # → resource "group"
    """A named set of users. `members` are the user ids in the group; `owner` /
    `maintainers` decide who may manage it (see module docstring + `effective_owner`)."""

    name: str
    description: str = ""
    members: list[str] = field(default_factory=list)
    # #608 — the single group owner. None ⇒ `created_by` (creator) is the owner.
    owner: str | None = None
    # #608 — delegated managers who may edit MEMBERS only (never each other).
    maintainers: list[str] = field(default_factory=list)


def effective_owner(group: Group, created_by: str) -> str:
    """The group's authority: the explicit `owner`, else the record's creator.
    The single place the `None` ⇒ `created_by` fallback lives (see #608)."""
    return group.owner or created_by


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
