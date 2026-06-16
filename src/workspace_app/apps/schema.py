"""Project a WorkItem model's field schema for the FE (#89 P7b).

The workspace shell renders + inline-edits an App's domain fields (severity,
status, …) driven by ``app.json``'s ``layout`` × this schema. Rather than
restate field types/options on the FE, we derive them here from the model — the
same source specstar's ``autocrud`` codegen reads (the OpenAPI ``enum``), but a
lean runtime subset folded into the manifest response instead of FE codegen.

``kind`` is the renderer key: an enum field → ``select`` (its values become the
dropdown ``options``); a list field (``topics``/``members``) → ``tags`` (a chip
input the FE adds/removes from); everything else → ``text``.
"""

from __future__ import annotations

from typing import Literal

import msgspec.inspect as mi
from msgspec import UNSET, Struct, UnsetType

from .base import WorkItemBase


class FieldSpec(Struct):
    name: str
    label: str
    kind: Literal["select", "text", "tags"]
    options: list[str] | UnsetType = UNSET


def _label(name: str) -> str:
    """Humanise a field name for display (``attached_preset`` → ``Attached
    Preset``). app.json ``labels`` can still override on the FE."""
    return " ".join(word.capitalize() for word in name.split("_"))


def _is_list(t: mi.Type) -> bool:
    """A list/set field — possibly wrapped in a ``T | UnsetType`` union (the
    base declares opt-in collections that way before a subclass concretises)."""
    if isinstance(t, (mi.ListType, mi.SetType)):
        return True
    return isinstance(t, mi.UnionType) and any(_is_list(m) for m in t.types)


def project_fields(model: type[WorkItemBase]) -> list[FieldSpec]:
    """Project ``model``'s fields into the FE's lean field schema."""
    info = mi.type_info(model)
    assert isinstance(info, mi.StructType)  # a WorkItemBase subclass always is
    out: list[FieldSpec] = []
    for field in info.fields:
        if isinstance(field.type, mi.EnumType):
            options = [member.value for member in field.type.cls]
            out.append(
                FieldSpec(name=field.name, label=_label(field.name), kind="select", options=options)
            )
        elif _is_list(field.type):
            out.append(FieldSpec(name=field.name, label=_label(field.name), kind="tags"))
        else:
            out.append(FieldSpec(name=field.name, label=_label(field.name), kind="text"))
    return out
