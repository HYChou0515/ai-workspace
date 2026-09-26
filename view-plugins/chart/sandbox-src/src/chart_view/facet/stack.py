"""``facet_stack`` (plan-view-plugins-pr5-finish P5): a set of a gallery's
groups stacked into one map, and with a second set, A, B and A - B.

At each cell of the gallery's lattice, the picked column over the chosen
groups' rows, summarised by the picked statistic. It is a query over the cache
the gallery already built, never a build: the cache places every cell and
names the source it was built from (path, size, mtime) and the transform; the
rows are read from that source the way the build read them, and a source
edited since is ``CacheUnusable`` -- exit 3, which sends the gallery round a
rebuild, rather than a stack of rows the tiles do not show.

The cache holds only the colour column, so any other column has to come from
the source; that is the cost of "any column" (a read of the source per stack).

Imported only when ``facet_stack`` runs: it loads pandas, which the pager's
other commands must not.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from chart_view.facet import CacheIndex, CacheUnusable, Group
from chart_view.facet.build import STACK_STATS, _axis_codes, _row_positions, column_kind
from chart_view.facet.pager import _current, _used
from chart_view.query import _kinds
from chart_view.sources import read_source
from chart_view.transforms import apply_transforms

Keys = Sequence[Sequence[str]] | None


def _frame(workspace: Path, index: CacheIndex) -> pd.DataFrame:
    origin = index.origin
    try:
        st = (workspace / origin["source"]).stat()
    except OSError:
        raise CacheUnusable(
            f"source {origin['source']!r} is gone since the gallery was built"
        ) from None
    if (st.st_size, st.st_mtime_ns) != (origin["size"], origin["mtime_ns"]):
        raise CacheUnusable(f"source {origin['source']!r} changed since the gallery was built")
    return apply_transforms(read_source(workspace, origin["source"]), origin["transform"])


def _cells(frame: pd.DataFrame, index: CacheIndex) -> np.ndarray:
    """Each row's cell of the cache's lattice (-1: no x or y), placed by the
    same axis codes the build placed them with."""
    x, y = index.origin["x"], index.origin["y"]
    kinds = _kinds("grid", [("x", x), ("y", y)])
    (xc, x_at), (yc, y_at) = (
        _axis_codes(frame[x["field"]], kinds[x["field"]]),
        _axis_codes(frame[y["field"]], kinds[y["field"]]),
    )
    rows = np.flatnonzero((xc >= 0) & (yc >= 0))
    layout = zip(index.layout["x"], index.layout["y"], strict=True)
    where = {(lx, ly): i for i, (lx, ly) in enumerate(layout)}
    pair = xc[rows] * (int(yc.max()) + 1) + yc[rows]
    _, first, inverse = np.unique(pair, return_index=True, return_inverse=True)
    table = np.asarray(
        [where[(x_at(int(rows[f])), y_at(int(rows[f])))] for f in first], dtype=np.int64
    )
    cells = np.full(len(frame), -1, dtype=np.int64)
    cells[rows] = table[inverse]
    return cells


def _positions(index: CacheIndex, keys: Keys) -> np.ndarray:
    if keys is None:
        return np.arange(len(index.groups))
    position = {g.key: i for i, g in enumerate(index.groups)}
    out = []
    for k in keys:
        if tuple(k) not in position:
            raise ValueError(f"group {list(k)!r} is not one of this gallery's groups")
        out.append(position[tuple(k)])
    return np.asarray(out, dtype=np.int64)


def _f64(values: np.ndarray) -> dict[str, Any]:
    data = values.astype("<f8").tobytes()  # little-endian on any host, as the wire is
    return {"kind": "f64", "data": base64.b64encode(data).decode("ascii")}


def stack_payload(
    views: Path,
    workspace: Path,
    digest: str,
    build: str,
    a: Keys,
    b: Keys,
    column: str,
    stat: str,
) -> dict[str, Any]:
    path, index = _current(views, digest, build)
    frame = _frame(workspace, index)
    if column not in frame.columns:
        raise ValueError(f"no column {column!r} (columns: {', '.join(map(str, frame.columns))})")
    kind = column_kind(frame[column])
    if stat not in STACK_STATS[kind]:
        raise ValueError(
            f"{stat!r} does not stack {column!r}, a {kind} column:"
            f" pick one of {', '.join(STACK_STATS[kind])}"
        )
    chosen_a, chosen_b = _positions(index, a), None if b is None else _positions(index, b)
    groups = [Group(key=g.key, sort={}, values=[]) for g in index.groups]
    pos = _row_positions(frame, index.facet, groups)
    cells = _cells(frame, index)
    values = frame[column]

    def stacked(chosen: np.ndarray) -> np.ndarray:
        mask = (cells >= 0) & np.isin(pos, chosen)
        agg = values[mask].groupby(cells[mask]).agg("nunique" if stat == "distinct" else stat)
        out = np.full(index.cells, np.nan)
        out[agg.index.to_numpy(dtype=np.int64)] = agg.to_numpy(dtype=float)
        return out

    stack_a = stacked(chosen_a)
    stack_b = None if chosen_b is None else stacked(chosen_b)
    _used(path)
    return {
        "a": _f64(stack_a),
        "b": None if stack_b is None else _f64(stack_b),
        "diff": None if stack_b is None else _f64(stack_a - stack_b),
        "groups": {"a": len(chosen_a), "b": None if chosen_b is None else len(chosen_b)},
    }
