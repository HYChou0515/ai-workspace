"""The facet builder (plan-view-plugins-pr4 P3): a long-format frame, one row
per cell, becomes one facet cache.

This is the one place pandas values meet the cache format, so it is the one
place they are made plain (the format refuses anything else, by name):

- a group's key is ``canon`` of each facet column's value -- the string a
  linked view's marking compares (Q6), so a key written here always matches.
  A missing key (NaN, NA, None) is refused, never keyed "<NA>";
- a cell's x / y is asked of the chart itself: the wire kind ``query._kinds``
  gives a grid's x / y channels, then ``wire.encode_column`` of that kind,
  decoded. A thumbnail's ``lattice`` then gets exactly what the full view's
  gets. A row with no x or y is left out, as ``lattice`` leaves it out; a group
  all of whose rows are left out stays, as an empty thumbnail;
- a sort value is None when missing (NaN, NaT, NA), UTC epoch milliseconds
  when a date or a timestamp (exact in JSON.parse, and in time order across tz
  offsets), and the Python value of a numpy scalar. A ``datetime.time`` or a
  ``Timedelta`` is not converted, and the format refuses it by name.

Within ``chart_view.facet`` only this module imports pandas: the pager and the
cap never load it.
"""

from __future__ import annotations

import base64
import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from chart_view.facet import CategoryScale, ContinuousScale, Group, write_cache
from chart_view.query import _kinds
from chart_view.wire import canon, encode_column

_AXIS_TYPES = ("quantitative", "temporal", "ordinal", "nominal")


class BuildError(ValueError):
    """The frame cannot be one facet cache; the message names why."""


def _plain_sort(value: Any) -> Any:
    if value is None or (not isinstance(value, str | bytes) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp | dt.date | np.datetime64):  # dt.datetime is a dt.date
        # .value is UTC ns since the epoch for a zoned stamp, and reads a naive
        # one as UTC; either way ms is exact in JSON.parse and sorts in time order
        return pd.Timestamp(value).value / 1e6
    if isinstance(value, np.generic):
        return value.item()
    return value


def _axis(column: pd.Series, kind: str) -> list[Any]:
    """The axis values the chart's wire sends a browser for ``column`` (None
    where it sends a missing value): the same call, decoded."""
    wire = encode_column(column, kind)
    if kind != "cat":
        floats = np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8")
        return [None if np.isnan(v) else float(v) for v in floats]
    width = wire["width"]
    codes = np.frombuffer(base64.b64decode(wire["codes"]), dtype=f"<u{width}")
    missing = 2 ** (8 * width) - 1
    return [None if c == missing else wire["levels"][c] for c in codes]


def _texts(column: pd.Series) -> list[str | None]:
    """canon of each value, None where pandas says missing (NaN, NaT, NA)."""
    missing = column.isna().to_numpy()
    return [None if m else canon(v) for v, m in zip(column, missing, strict=True)]


def build_facet_cache(
    frame: pd.DataFrame,
    *,
    facet: Sequence[str],
    x: str,
    y: str,
    value: str,
    sort: Sequence[str] = (),
    path: Path,
    x_type: str = "ordinal",
    y_type: str = "ordinal",
    progress: Callable[[str], None] = lambda _line: None,
) -> None:
    missing_columns = [c for c in [*facet, x, y, value, *sort] if c not in frame.columns]
    if missing_columns:
        raise BuildError(f"the source has no column {', '.join(map(repr, missing_columns))}")
    progress(f"read {len(frame)} rows")

    for name, t in (("x_type", x_type), ("y_type", y_type)):
        if t not in _AXIS_TYPES:
            raise BuildError(f"{name} {t!r} is not one of {', '.join(_AXIS_TYPES)}")
    # the kind query sends each axis at, asked of query itself (a field named by
    # both channels takes the first channel's kind there, so it does here)
    kinds = _kinds(
        "grid", [("x", {"field": x, "type": x_type}), ("y", {"field": y, "type": y_type})]
    )
    xs, ys = _axis(frame[x], kinds[x]), _axis(frame[y], kinds[y])
    placed = [i for i in range(len(frame)) if xs[i] is not None and ys[i] is not None]
    if len(placed) < len(frame):
        left_out = len(frame) - len(placed)
        progress(f"left out {left_out} row{'s' if left_out > 1 else ''} with no x or y")
    if not placed:
        raise BuildError(f"no row has both a {x!r} and a {y!r} value")

    keys = {c: _texts(frame[c]) for c in facet}
    for c in facet:
        gaps = sum(k is None for k in keys[c])
        if gaps:
            raise BuildError(
                f"{gaps} rows have no {c!r} value (missing, or a number that is not finite)"
            )

    numeric = pd.api.types.is_numeric_dtype(frame[value]) and not pd.api.types.is_bool_dtype(
        frame[value]
    )
    if numeric:
        values = pd.to_numeric(frame[value], errors="coerce").astype(float)
        finite = values[np.isfinite(values)]
        lo = float(finite.min()) if len(finite) else 0.0
        hi = float(finite.max()) if len(finite) else 0.0
        scale: ContinuousScale | CategoryScale = ContinuousScale(lo, hi)
        # the format codes NaN (a missing cell) and +/-inf as missing, as the
        # chart's q8 does; the exact section keeps inf and reads NaN back as None
        cell_values: list[Any] = [float(v) for v in values]
    else:
        cell_values = _texts(frame[value])
        scale = CategoryScale(sorted({t for t in cell_values if t is not None}))

    cell_of: dict[tuple[Any, Any], int] = {}
    rows_of: dict[tuple[str, ...], list[int]] = {}
    for i in placed:
        cell_of.setdefault((xs[i], ys[i]), len(cell_of))
    # every group, placed or not: one whose rows all lack x or y stays, as an
    # empty thumbnail, rather than vanishing from under a marking that names it
    for i in range(len(frame)):
        key = tuple(str(keys[c][i]) for c in facet)  # no None left: refused above
        rows_of.setdefault(key, [])
        if xs[i] is not None and ys[i] is not None:
            rows_of[key].append(i)
    cells = len(cell_of)

    groups: list[Group] = []
    for k, rows in rows_of.items():
        record: list[Any] = [None] * cells
        seen: set[int] = set()
        for i in rows:
            cell = cell_of[(xs[i], ys[i])]
            if cell in seen:
                raise BuildError(
                    f"group {k!r} has more than one row at ({xs[i]!r}, {ys[i]!r}); aggregate first"
                )
            seen.add(cell)
            record[cell] = cell_values[i]
        sort_values = {}
        for c in sort:
            distinct = {repr(_plain_sort(frame[c].iat[i])) for i in rows}
            if len(distinct) > 1:
                raise BuildError(f"group {k!r} has more than one {c!r}; a sort value is per group")
            sort_values[c] = _plain_sort(frame[c].iat[rows[0]]) if rows else None
        groups.append(Group(key=k, sort=sort_values, values=record))
    progress(f"{len(groups)} groups over {cells} cells")

    write_cache(
        path,
        scale=scale,
        facet=list(facet),
        cells=cells,
        layout={"x": [c[0] for c in cell_of], "y": [c[1] for c in cell_of]},
        groups=groups,
    )
    progress(f"wrote {path.stat().st_size} bytes")
