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
    DATETIMERANGE = "datetimerange"
    NUMBER = "number"
    PROGRESS = "progress"
    RANK = "rank"
    # Relational roles — resolved at render time from other records.
    REF = "ref"
    BACKREF = "backref"
    ROLLUP = "rollup"

    @classmethod
    def _missing_(cls, value: object) -> Role | None:
        """Accept `daterange`, the name `datetimerange` used to have.

        Not a transition window — a permanent spelling. `.entity/<type>/
        schema.yaml` is seeded into the item's workspace ONCE, at creation
        (`apps.seeding.seed_item` has a single caller and no re-seed path), and
        is the user's file from then on. Every schema written before the rename
        says `daterange` and always will, and so will anything a user or an
        agent copies from one.

        Refusing it would not raise: `catalog._load_type` catches the
        `ValueError` and degrades the field to `text` with a warning, so the
        symptom is a gantt bar that silently stops being drawn while the value
        sits intact in the file. Guarded in `tests/entity/test_catalog.py`
        through the real path, because asserting `Role("daterange")` alone
        cannot see that `except`.

        This is the ONLY place the old spelling exists. `.value` is the new
        name, so the widget table, the brief, the API payload and every view
        kind see one name and cannot branch on which was written."""
        if value == "daterange":
            return cls.DATETIMERANGE
        return None


ROLLUP_AGGS = ("count", "sum", "avg", "min", "max")


class FieldSpec(msgspec.Struct, frozen=True):
    name: str
    role: Role
    required: bool = False
    values: list[str] | None = None
    """Closed vocabulary for a `status` role — a value outside it lints (§C7)."""
    colors: dict[str, str] | None = None
    """`status`/select → a `{value: hue}` map pinning each value to a semantic
    colour for the renderer's chip (#GH-projects B); a hue name (`green`/`blue`/
    `amber`/`red`/…) or a palette slot. Absent → the FE auto-hashes a stable hue."""
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
