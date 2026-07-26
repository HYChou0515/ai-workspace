"""A compact, agent-facing summary of a workspace's record types.

The entity `create_entity` / `update_entity` tools take a free-form `args` dict —
the field vocabulary lives in the workspace `.entity/` schema, not the tool
signature (§ tools.py `_NONSTRICT_TOOLS`). So an agent asked to "add an issue"
has to guess the field names, the closed `status` vocabulary, and which field a
timeline reads — a small local model guesses wrong (an invalid status lints into
Project health; a missing date range hides the record from the gantt).

`entity_schema_brief` renders the live catalog into a short prompt section the
turn folds in up front, so the model sees the exact field names + allowed values
before it calls the tool. It is derived from the schema, never hardcoded per app,
so it stays correct as the workspace's `.entity/` evolves.
"""

from __future__ import annotations

from .catalog import EntityCatalog
from .schema import FieldSpec, Role

# Omitted from create/update guidance: backref / rollup are computed at render
# time from other records (read-only), and `rank` is manual board/table order
# (#GH-projects) — auto-assigned on create + set by dragging, never something the
# agent picks when filling a record's fields.
_DERIVED = frozenset({Role.BACKREF, Role.ROLLUP, Role.RANK})


def _role_detail(f: FieldSpec) -> str:
    """The parenthetical hint for one settable field — what a valid value is."""
    if f.role is Role.STATUS:
        return f"one of: {', '.join(f.values)}" if f.values else "a short status"
    if f.role is Role.ACTOR:
        return "a person — pass a user id from lookup_user"
    if f.role is Role.DATE:
        return "a date, YYYY-MM-DD"
    if f.role is Role.DATERANGE:
        return (
            'a date range "YYYY-MM-DD/YYYY-MM-DD" — set this so the record '
            "shows on the timeline / gantt"
        )
    if f.role is Role.PROGRESS:
        return "a percent, 0-100"
    if f.role is Role.RANK:
        return "an integer rank"
    if f.role is Role.REF:
        return f"a reference to a {f.to or 'record'} — pass its number, or use link_entity"
    return "text"


def _field_hint(f: FieldSpec) -> str:
    detail = _role_detail(f)
    return f"{f.name} ({detail}, required)" if f.required else f"{f.name} ({detail})"


def entity_schema_brief(catalog: EntityCatalog) -> str:
    """Render the workspace's record types + settable fields as a prompt section.

    Empty string when the workspace declares no entity types, so a non-entity app
    (or an item with no `.entity/`) gets nothing injected.
    """
    names = catalog.names()
    if not names:
        return ""
    lines = [
        "## This workspace's record types",
        "",
        "Create and change records with create_entity / update_entity using the "
        "exact field names and status values below; query_entity reads the current "
        "records.",
        "",
    ]
    for name in names:
        et = catalog.get(name)
        settable = [f for f in et.schema.fields if f.role not in _DERIVED]
        hints = ", ".join(_field_hint(f) for f in settable)
        lines.append(f"- **{name}** ({et.records_path}/N.md): {hints}")
    return "\n".join(lines)
