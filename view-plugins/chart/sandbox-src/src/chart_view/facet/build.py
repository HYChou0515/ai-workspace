"""The facet builder (plan-view-plugins-pr4 P3): a long-format frame, one row
per cell, becomes one facet cache.

This is the one place pandas values meet the cache format, so it is the one
place they are made plain (the format refuses anything else, by name):

- a group's key is ``canon`` of each facet column's value -- the string a
  linked view's marking compares (Q6), so a key written here always matches;
- a cell's x / y is what the chart's wire sends a browser for the same value
  (``wire._json_scalar``), so a thumbnail's ``lattice`` places cells where the
  full view does;
- a sort value is None when missing (NaN, NaT, NA), a number of UTC epoch
  milliseconds when a date or time (exact in JSON.parse, and in time order
  across tz offsets), and the Python value of a numpy scalar.

Pandas is imported here and only here: the pager (``chart_view.facet.pager``)
never loads it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from chart_view.facet import CategoryScale, ContinuousScale, Group, write_cache
from chart_view.wire import _json_scalar, canon


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


def build_facet_cache(
    frame: pd.DataFrame,
    *,
    facet: Sequence[str],
    x: str,
    y: str,
    value: str,
    sort: Sequence[str] = (),
    path: Path,
    progress: Callable[[str], None] = lambda _line: None,
) -> None:
    missing = [c for c in [*facet, x, y, value, *sort] if c not in frame.columns]
    if missing:
        raise BuildError(f"the source has no column {', '.join(map(repr, missing))}")
    progress(f"read {len(frame)} rows")

    keys = pd.DataFrame({c: frame[c].map(canon) for c in facet})
    for c in facet:
        if keys[c].isna().any():
            raise BuildError(f"{int(keys[c].isna().sum())} rows have no {c!r} value")
    xs = frame[x].map(_json_scalar)
    ys = frame[y].map(_json_scalar)
    cell_of: dict[tuple[Any, Any], int] = {}
    for cx, cy in zip(xs, ys, strict=True):
        cell_of.setdefault((cx, cy), len(cell_of))
    cells = len(cell_of)

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
        texts = frame[value].map(canon)
        scale = CategoryScale(sorted({t for t in texts if t is not None}))
        cell_values = list(texts)

    groups: list[Group] = []
    order = list(dict.fromkeys(zip(*(keys[c] for c in facet), strict=True)))
    rows_of: dict[tuple[str, ...], list[int]] = {k: [] for k in order}
    for i, k in enumerate(zip(*(keys[c] for c in facet), strict=True)):
        rows_of[k].append(i)
    for k in order:
        record: list[Any] = [None] * cells
        seen: set[int] = set()
        for i in rows_of[k]:
            cell = cell_of[(xs.iat[i], ys.iat[i])]
            if cell in seen:
                raise BuildError(
                    f"group {k!r} has more than one row at ({xs.iat[i]!r}, {ys.iat[i]!r});"
                    " aggregate first"
                )
            seen.add(cell)
            record[cell] = cell_values[i]
        sort_values = {}
        for c in sort:
            distinct = {repr(_plain_sort(frame[c].iat[i])) for i in rows_of[k]}
            if len(distinct) > 1:
                raise BuildError(f"group {k!r} has more than one {c!r}; a sort value is per group")
            sort_values[c] = _plain_sort(frame[c].iat[rows_of[k][0]])
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
