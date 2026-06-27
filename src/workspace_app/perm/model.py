"""The shared `Permission` value object and its vocabulary (#262).

One `Permission` is embedded on every protected resource (Collection / WorkItem
/ KbChat). `visibility` decides whether the per-verb grant lists are enforced;
the lists themselves always persist, so toggling public↔restricted↔private never
loses settings. See `docs/plan-permissions.md`.
"""

from __future__ import annotations

from typing import Literal

from msgspec import Struct, field

# A grant target: a specific user, a (logical) group, or everyone.
#   "user:<id>" | "group:<id>" | "all"
# The `group:` namespace is reserved now (no Group entity yet) so adding groups
# later needs no data migration.
Subject = str
ALL: Subject = "all"


def user_subject(user_id: str) -> Subject:
    return f"user:{user_id}"


def group_subject(group_id: str) -> Subject:
    return f"group:{group_id}"


Visibility = Literal["public", "restricted", "private"]

Verb = Literal[
    "read_meta",
    "write_meta",
    "read_content",
    "add_content",
    "edit_content",
    "read_chat",
    "converse",
    "execute",
    "use_terminal",
    "change_permission",
]

# Iterable form (declaration order is the canonical verb set).
VERBS: tuple[Verb, ...] = (
    "read_meta",
    "write_meta",
    "read_content",
    "add_content",
    "edit_content",
    "read_chat",
    "converse",
    "execute",
    "use_terminal",
    "change_permission",
)

# Verbs the AI can NEVER hold, whatever the preset ceiling or who drives it —
# changing access control and opening a human shell are not things an agent does.
AI_FORBIDDEN: frozenset[Verb] = frozenset({"change_permission", "use_terminal"})


class Permission(Struct):
    """Embedded on a resource. Absent ≡ `public` (back-compat). `owner` is NOT a
    field — it is the resource's `created_by` (specstar meta), implicit and
    non-removable."""

    visibility: Visibility = "public"
    read_meta: list[Subject] = field(default_factory=list)
    write_meta: list[Subject] = field(default_factory=list)
    read_content: list[Subject] = field(default_factory=list)
    add_content: list[Subject] = field(default_factory=list)
    edit_content: list[Subject] = field(default_factory=list)
    read_chat: list[Subject] = field(default_factory=list)
    converse: list[Subject] = field(default_factory=list)
    execute: list[Subject] = field(default_factory=list)
    use_terminal: list[Subject] = field(default_factory=list)
    change_permission: list[Subject] = field(default_factory=list)

    def grants(self, verb: Verb) -> list[Subject]:
        """The grant list for `verb`. (A small indirection so callers never index
        the struct by attribute string themselves.)"""
        return getattr(self, verb)
