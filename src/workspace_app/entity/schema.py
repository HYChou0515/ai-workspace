"""The declarative entity schema (#419 §A).

A schema is a list of fields, each tagged with a semantic `role` drawn from a
closed vocabulary. The role — not a Python type — is the single thing that
drives frontmatter parsing/validation, the quick-create widget, the generated
tool's arg type, and which view key a field may bind to. Scalar roles carry a
stored value; relational roles (ref / backref / rollup) are resolved at render
time from the other records (compute-on-read, §A4) — the view renderer does the
traversal, so no index is needed.
"""

from __future__ import annotations

from enum import StrEnum

import msgspec


class Role(StrEnum):
    """The closed role vocabulary (§A2). Expressiveness ceiling = this vocab."""

    # Scalar (stored) roles.
    TEXT = "text"
    STATUS = "status"
    ACTOR = "actor"
    DATE = "date"
    DATERANGE = "daterange"
    PROGRESS = "progress"
    RANK = "rank"
    # Relational roles — resolved at render time from other records.
    REF = "ref"
    BACKREF = "backref"
    ROLLUP = "rollup"


ROLLUP_AGGS = ("count", "sum", "avg", "min", "max")


class FieldSpec(msgspec.Struct, frozen=True):
    name: str
    role: Role
    required: bool = False
    values: list[str] | None = None
    """Closed vocabulary for a `status` role — a value outside it lints (§C7)."""
    to: str | None = None
    """`ref` → the target entity type (`milestone`). Traversal is to-one only."""
    from_: str | None = None
    """`backref` → the source `type.field` whose ref points back here (`issue.milestone`)."""
    over: str | None = None
    """`rollup` → the `backref` field on this type to aggregate over."""
    agg: str | None = None
    """`rollup` → one of `ROLLUP_AGGS`."""
    field: str | None = None
    """`rollup` → the field on the backref'd records to aggregate."""
    where: dict[str, str] | None = None
    """`rollup` → an optional single `{field: value}` equality filter (§A5)."""


class EntitySchema(msgspec.Struct, frozen=True):
    fields: list[FieldSpec]

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)
