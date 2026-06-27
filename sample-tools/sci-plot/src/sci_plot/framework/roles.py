"""Column *roles* — the per-chart declaration of which columns a chart needs.

A role maps a semantic slot (``x``, ``y``, ``group``, ``die_x`` …) to a
DataFrame column. The framework resolves roles uniformly (explicit > inferred >
ask) and coerces the chosen column to the role's ``kind``; the renderer then
reads clean data by role name. Roles are the framework's vocabulary for "what
this chart needs" — everything else is the chart's ``Options``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RoleKind(str, Enum):
    """The dtype family a role's column is coerced to. ``ANY`` skips coercion."""

    NUMBER = "number"  # float-coerced (numeric-ish strings → numbers)
    INT = "int"  # integer-coerced (e.g. die row/col indices)
    CATEGORY = "category"  # left as-is, treated as discrete groups
    DATETIME = "datetime"  # parsed to datetime
    ANY = "any"  # no coercion


@dataclass(frozen=True)
class Role:
    """One column slot a chart declares.

    ``multi=True`` means the slot binds to an *ordered list* of columns (e.g.
    grouped_line's hierarchical x levels), so the request field is
    ``list[str] | None`` and resolution yields a ``list[str]``. Otherwise the
    slot binds to a single column (request field ``str | None``, resolution
    yields a ``str``).
    """

    name: str
    kind: RoleKind = RoleKind.ANY
    required: bool = True
    description: str = ""
    multi: bool = False
