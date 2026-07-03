"""The declarative entity schema (#419 §A).

A schema is a list of fields, each tagged with a semantic `role` drawn from a
closed vocabulary. The role — not a Python type — is the single thing that
drives frontmatter parsing/validation, the quick-create widget, the generated
tool's arg type, and which view key a field may bind to. P1 ships the scalar
subset of roles; relational roles (ref / backref / rollup) land in P2.
"""

from __future__ import annotations

from enum import StrEnum

import msgspec


class Role(StrEnum):
    """The closed role vocabulary. P1 scalar subset only."""

    TEXT = "text"
    STATUS = "status"
    ACTOR = "actor"
    DATE = "date"
    PROGRESS = "progress"


class FieldSpec(msgspec.Struct, frozen=True):
    name: str
    role: Role
    required: bool = False
    values: list[str] | None = None
    """Closed vocabulary for a `status` role — a value outside it lints (§C7)."""


class EntitySchema(msgspec.Struct, frozen=True):
    fields: list[FieldSpec]

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)
