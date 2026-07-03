"""Derive the quick-create form from an entity's skeleton (#419 §D).

The deterministic-UI form is exactly the skeleton's `{{arg.x}}` / `{{arg.x?}}`
placeholders rendered as widgets — a field with no `{{arg}}` never enters the
form. The widget for each arg comes from its schema role (role→widget), so the
form and the generated tool stay in lock-step with the schema.
"""

from __future__ import annotations

import re

import msgspec

from .catalog import EntityType
from .schema import Role

_ARG = re.compile(r"\{\{\s*arg\.([A-Za-z0-9_]+)(\?)?\s*\}\}")

_WIDGET: dict[Role, str] = {
    Role.TEXT: "text",
    Role.STATUS: "select",
    Role.ACTOR: "actor",
    Role.DATE: "date",
    Role.PROGRESS: "progress",
}


class FormField(msgspec.Struct, frozen=True):
    name: str
    widget: str
    required: bool
    values: list[str] | None = None


def form_spec(entity_type: EntityType) -> list[FormField]:
    seen: set[str] = set()
    out: list[FormField] = []
    for match in _ARG.finditer(entity_type.skeleton):
        name, optional = match.group(1), bool(match.group(2))
        if name in seen:
            continue
        seen.add(name)
        spec = entity_type.schema.field(name)
        widget = _WIDGET[spec.role] if spec else "text"
        values = spec.values if spec and spec.role is Role.STATUS else None
        out.append(FormField(name=name, widget=widget, required=not optional, values=values))
    return out
