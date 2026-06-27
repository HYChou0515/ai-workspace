"""Resolve column *roles* — the second half of "don't over-demand input".

Given a DataFrame, a chart's declared roles, and the caller's (optional) column
assignments, produce either:

* a :class:`Resolved` — a coerced DataFrame + a ``role → column(s)`` mapping
  ready for ``draw``, or
* an :class:`AskNeeded` — a structured "I need you to specify these roles;
  here are the available columns" signal (exit 0, guidance not crash) so the
  agent can re-call with explicit mappings.

Policy: explicit wins; omitted + exactly one suitable column → inferred; omitted
+ ambiguous (or no candidate for a required role) → ask. Coercion is liberal
(numeric-ish strings → numbers, date-ish → datetime); it never changes *which*
column plays a role.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from sci_plot.framework.roles import Role, RoleKind

Assignment = str | list[str] | None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    dtype: str


@dataclass(frozen=True)
class AskItem:
    role: str
    kind: str
    required: bool
    reason: str  # "ambiguous" | "missing"
    candidates: list[str]


@dataclass
class AskNeeded:
    """The resolver could not unambiguously fill every required role."""

    items: list[AskItem]
    available: list[ColumnInfo]


@dataclass
class Resolved:
    df: pd.DataFrame
    roles: dict[str, str | list[str]] = field(default_factory=dict)


def resolve(
    df: pd.DataFrame,
    roles: tuple[Role, ...],
    assignments: dict[str, Assignment],
) -> Resolved | AskNeeded:
    work = df.copy()
    mapping: dict[str, str | list[str]] = {}
    asks: list[AskItem] = []
    used: set[str] = set()

    for role in roles:
        given = assignments.get(role.name)
        if role.multi:
            _resolve_multi(work, role, given, mapping, asks, used)
        else:
            _resolve_single(work, role, given, mapping, asks, used)

    if asks:
        available = [ColumnInfo(str(c), str(df[c].dtype)) for c in df.columns]
        return AskNeeded(items=asks, available=available)
    return Resolved(df=work, roles=mapping)


def _resolve_single(
    work: pd.DataFrame,
    role: Role,
    given: Assignment,
    mapping: dict[str, str | list[str]],
    asks: list[AskItem],
    used: set[str],
) -> None:
    if given is not None:
        col = given if isinstance(given, str) else given[0]
        _require_column(work, col)
        _coerce(work, col, role.kind)
        mapping[role.name] = col
        used.add(col)
        return
    candidates = [c for c in _candidates(work, role.kind) if c not in used]
    if len(candidates) == 1:
        col = candidates[0]
        _coerce(work, col, role.kind)
        mapping[role.name] = col
        used.add(col)
    elif not candidates:
        if role.required:
            asks.append(_ask(role, "missing", [str(c) for c in work.columns]))
    else:
        if role.required:
            asks.append(_ask(role, "ambiguous", candidates))


def _resolve_multi(
    work: pd.DataFrame,
    role: Role,
    given: Assignment,
    mapping: dict[str, str | list[str]],
    asks: list[AskItem],
    used: set[str],
) -> None:
    if given is not None:
        cols = [given] if isinstance(given, str) else list(given)
        for col in cols:
            _require_column(work, col)
            _coerce(work, col, role.kind)
            used.add(col)
        mapping[role.name] = cols
        return
    # Order matters for a multi role — never guess it; ask if required.
    if role.required:
        asks.append(_ask(role, "missing", [str(c) for c in work.columns]))


def _ask(role: Role, reason: str, candidates: list[str]) -> AskItem:
    return AskItem(
        role=role.name,
        kind=role.kind.value,
        required=role.required,
        reason=reason,
        candidates=candidates,
    )


def _require_column(df: pd.DataFrame, col: str) -> None:
    if col not in df.columns:
        avail = ", ".join(str(c) for c in df.columns)
        raise ValueError(f"column {col!r} not found; available columns: {avail}")


def _candidates(df: pd.DataFrame, kind: RoleKind) -> list[str]:
    cols = [str(c) for c in df.columns]
    if kind in (RoleKind.NUMBER, RoleKind.INT):
        return [c for c in cols if _is_numeric_capable(df[c])]
    if kind == RoleKind.DATETIME:
        return [c for c in cols if _is_datetime_capable(df[c])]
    if kind == RoleKind.CATEGORY:
        return [c for c in cols if not _is_numeric_capable(df[c])]
    return cols  # ANY


def _is_numeric_capable(s: pd.Series) -> bool:
    if pd.api.types.is_numeric_dtype(s):
        return True
    coerced = pd.to_numeric(s, errors="coerce")
    return bool(coerced.notna().mean() >= 0.5)


def _is_datetime_capable(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if _is_numeric_capable(s):
        return False  # plain numbers are not a datetime candidate
    coerced = pd.to_datetime(s, errors="coerce")
    return bool(coerced.notna().mean() >= 0.5)


def _coerce(df: pd.DataFrame, col: str, kind: RoleKind) -> None:
    """Best-effort coerce ``df[col]`` in place to the role's kind."""
    if kind == RoleKind.NUMBER:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    elif kind == RoleKind.INT:
        df[col] = pd.to_numeric(df[col], errors="coerce").round().astype("Int64")
    elif kind == RoleKind.DATETIME:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    # CATEGORY / ANY: leave the column as-is (treated as discrete groups).
