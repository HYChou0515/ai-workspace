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

Two paths build the same cache. ``_by_rows`` reads the frame a row at a time
and is the definition; ``_by_arrays`` does the same work on whole columns, and
takes a frame only when every facet, sort and categorical value column is of a
dtype where pandas' equality is the definition's equality (``_labels``,
``_sort_ident``); x and y need no such gate, since both paths compare cells on
the same decoded ``encode_column`` wire. The row path cost 69 s on the plan's 200 groups x 50 000
cells, past the 60 s a sandbox command gets by default, so a gallery that size
never opened. The parity test runs both on the same frames.

Within ``chart_view.facet`` only this module imports pandas: the pager and the
cap never load it.
"""

from __future__ import annotations

import base64
import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from chart_view.facet import CategoryScale, ContinuousScale, Group, write_cache
from chart_view.query import _kinds
from chart_view.wire import canon, encode_column, zone_name

_AXIS_TYPES = ("quantitative", "temporal", "ordinal", "nominal")

Scale = ContinuousScale | CategoryScale
Plan = tuple[Scale, int, dict[str, list[Any]], list[Group]]


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
    stat: str | None = None,
    origin: Mapping[str, Any] | None = None,
    path: Path,
    x_type: str = "ordinal",
    y_type: str = "ordinal",
    continuous: bool | None = None,
    progress: Callable[[str], None] = lambda _line: None,
) -> None:
    missing_columns = [c for c in [*facet, x, y, value, *sort] if c not in frame.columns]
    if missing_columns:
        raise BuildError(f"the source has no column {', '.join(map(repr, missing_columns))}")
    progress(f"read {len(frame)} rows")
    if stat is not None:
        _check_stat(frame, sort, stat)

    for name, t in (("x_type", x_type), ("y_type", y_type)):
        if t not in _AXIS_TYPES:
            raise BuildError(f"{name} {t!r} is not one of {', '.join(_AXIS_TYPES)}")
    # the kind query sends each axis at, asked of query itself (a field named by
    # both channels takes the first channel's kind there, so it does here)
    kinds = _kinds(
        "grid", [("x", {"field": x, "type": x_type}), ("y", {"field": y, "type": y_type})]
    )
    # ``continuous`` is the chart's own decision when a spec is at hand (a grid
    # colour that is not quantitative is categories whatever its dtype, as
    # query sends it); without one, the column's dtype decides
    numeric = (
        pd.api.types.is_numeric_dtype(frame[value]) and not pd.api.types.is_bool_dtype(frame[value])
        if continuous is None
        else continuous
    )
    args: dict[str, Any] = {
        "facet": facet,
        "x": x,
        "y": y,
        "value": value,
        # a statistic is worked out over the groups below, not per row
        "sort": () if stat else sort,
        "kinds": kinds,
        "numeric": numeric,
        "progress": progress,
    }
    # each axis as one code per row, once: the whole-column path places cells
    # with it, and a tile's rows (for a statistic, and the column list) are
    # the rows it places
    axes = (_axis_codes(frame[x], kinds[x]), _axis_codes(frame[y], kinds[y]))
    # each facet column's labels, once: the whole-column path keys the groups
    # with them, and every row is placed in its group with them
    labels = {c: _labels(frame[c]) for c in facet}
    plan = _by_arrays(frame, axes=axes, labels=labels, **args) or _by_rows(frame, **args)
    scale, cells, layout, groups = plan
    progress(f"{len(groups)} groups over {cells} cells")

    pos = _row_positions(frame, facet, groups, labels=labels)
    placed = (axes[0][0] >= 0) & (axes[1][0] >= 0)
    if stat is not None:
        field = sort[0]
        values = _stat_values(frame[field], stat, pos, placed, len(groups))
        groups = [
            Group(key=g.key, sort={field: v}, values=g.values)
            for g, v in zip(groups, values, strict=True)
        ]
    columns = []
    tiles = _tiles(pos, placed)  # the same for every column
    # a group is one text of each facet column, and a text column's text is
    # its value (a number's is not: "0" keys 0.0 and -0.0), so a text facet
    # column holds one value per group without being read again
    text_facets = {
        c for c in facet if labels[c] is not None and frame[c].dtype.kind not in _NUMBERS
    }
    for c in frame.columns:
        kind = column_kind(frame[c])
        single = c in text_facets or _single(frame[c], tiles)
        columns.append(
            {
                "name": c,
                "kind": kind,
                "single": single,
                "stats": list(STATS[kind]),
                "stack": list(STACK_STATS[kind]),
            }
        )
    # a zoned facet column's keys are wall times there; the index names the
    # zone for the gallery's labels (#847/#848 P14)
    zones = {
        c: zone_name(frame[c].dtype.tz)
        for c in facet
        if isinstance(frame[c].dtype, pd.DatetimeTZDtype)
    }
    write_cache(
        path,
        scale=scale,
        facet=list(facet),
        cells=cells,
        layout=layout,
        groups=groups,
        zones=zones,
        columns=columns,
        origin=origin,
    )
    progress(f"wrote {path.stat().st_size} bytes")


def _left_out(total: int, placed: int, x: str, y: str, progress: Callable[[str], None]) -> None:
    if placed < total:
        left_out = total - placed
        progress(f"left out {left_out} row{'s' if left_out > 1 else ''} with no x or y")
    if not placed:
        raise BuildError(f"no row has both a {x!r} and a {y!r} value")


def _no_key(gaps: int, column: str) -> BuildError:
    return BuildError(
        f"{gaps} rows have no {column!r} value (missing, or a number that is not finite)"
    )


def _continuous(frame: pd.DataFrame, value: str) -> tuple[ContinuousScale, np.ndarray]:
    values = pd.to_numeric(frame[value], errors="coerce").astype(float).to_numpy()
    finite = values[np.isfinite(values)]
    lo = float(finite.min()) if len(finite) else 0.0
    hi = float(finite.max()) if len(finite) else 0.0
    # the format codes NaN (a missing cell) and +/-inf as missing, as the
    # chart's q8 does; the exact section keeps inf and reads NaN back as None
    return ContinuousScale(lo, hi, empty=not len(finite)), values


def _by_rows(
    frame: pd.DataFrame,
    *,
    facet: Sequence[str],
    x: str,
    y: str,
    value: str,
    sort: Sequence[str],
    kinds: dict[str, str],
    numeric: bool,
    progress: Callable[[str], None],
) -> Plan:
    """The cache's contents, a row at a time: the definition ``_by_arrays`` is
    held to."""
    xs, ys = _axis(frame[x], kinds[x]), _axis(frame[y], kinds[y])
    placed = [i for i in range(len(frame)) if xs[i] is not None and ys[i] is not None]
    _left_out(len(frame), len(placed), x, y, progress)

    keys = {c: _texts(frame[c]) for c in facet}
    for c in facet:
        gaps = sum(k is None for k in keys[c])
        if gaps:
            raise _no_key(gaps, c)

    scale: Scale
    if numeric:
        scale, floats = _continuous(frame, value)
        cell_values: list[Any] = [float(v) for v in floats]
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
                raise BuildError(
                    f"group {k!r} has more than one {c!r}; a sort value is per group;"
                    " sort by a statistic of it instead"
                )
            sort_values[c] = _plain_sort(frame[c].iat[rows[0]]) if rows else None
        groups.append(Group(key=k, sort=sort_values, values=record))
    layout = {"x": [c[0] for c in cell_of], "y": [c[1] for c in cell_of]}
    return scale, cells, layout, groups


# ─── whole columns ──────────────────────────────────────────────────────────


_NUMBERS = ("b", "i", "u", "f")  # dtype kinds: bool, int, unsigned, float


def _labels(column: pd.Series) -> tuple[np.ndarray, list[str]] | None:
    """``_texts(column)`` as one code per row into a list of distinct texts
    (-1 where ``_texts`` gives None), or None for a dtype where two values
    pandas holds equal could have different texts (an object column of mixed
    types: 1 and True hash alike), so only the row path can read it."""
    dtype = column.dtype
    if isinstance(dtype, pd.StringDtype):
        pass
    elif pd.api.types.is_object_dtype(dtype):
        if pd.api.types.infer_dtype(column, skipna=True) not in ("string", "empty"):
            return None
    elif isinstance(dtype, pd.CategoricalDtype) or getattr(dtype, "kind", None) not in _NUMBERS:
        return None
    codes, uniques = pd.factorize(column, use_na_sentinel=True)
    texts = [canon(u) for u in uniques]
    # distinct values can share a text (-0.0 and 0.0 are both "0"): one code per
    # text; the extra slot maps pandas' missing code (-1) to ours
    first: dict[str, int] = {}
    remap = [-1 if t is None else first.setdefault(t, len(first)) for t in texts]
    return np.asarray([*remap, -1], dtype=np.int64)[codes], list(first)


_NAN_BITS = np.int64(0x7FF8000000000000)  # no finite float has these bits


def _float_bits(values: np.ndarray) -> np.ndarray:
    bits = values.astype(np.float64).view(np.int64).copy()
    bits[np.isnan(values)] = _NAN_BITS
    return bits


def _sort_ident(column: pd.Series) -> np.ndarray | None:
    """One integer per row, equal exactly where ``repr(_plain_sort(value))`` is
    equal -- the row path's test for one sort value per group -- or None for a
    dtype this cannot vouch for. Floats compare by their bits, which is what
    repr tells apart (-0.0 is not 0.0); every missing value is one ident, as it
    is one None."""
    dtype = column.dtype
    if isinstance(dtype, pd.DatetimeTZDtype) or (isinstance(dtype, np.dtype) and dtype.kind == "M"):
        try:
            ns = column.dt.as_unit("ns").array.asi8
        except (pd.errors.OutOfBoundsDatetime, OverflowError):
            return None
        ms = ns / 1e6  # as _plain_sort divides Timestamp.value
        ms[column.isna().to_numpy()] = np.nan
        return _float_bits(ms)
    if pd.api.types.is_object_dtype(dtype):
        if pd.api.types.infer_dtype(column, skipna=True) not in ("string", "empty"):
            return None
        return pd.factorize(column, use_na_sentinel=True)[0].astype(np.int64)
    if not isinstance(dtype, np.dtype):
        return None
    values = column.to_numpy()
    if dtype.kind == "f":
        return _float_bits(values)
    if dtype.kind in ("b", "i", "u"):
        return values.astype(np.int64)  # a uint64 past 2**63 wraps, one to one
    return None


def _axis_codes(column: pd.Series, kind: str) -> tuple[np.ndarray, Callable[[int], Any]]:
    """``_axis(column, kind)`` as one code per row (-1: no value; equal codes
    where the values are equal as dict keys) and the value at a row."""
    wire = encode_column(column, kind)
    if kind != "cat":
        floats = np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8")
        # factorize puts -0.0 with 0.0, as a dict key does; NaN is its missing
        codes = pd.factorize(floats, use_na_sentinel=True)[0]
        return codes.astype(np.int64), lambda i: float(floats[i])
    width = wire["width"]
    raw = np.frombuffer(base64.b64decode(wire["codes"]), dtype=f"<u{width}").astype(np.int64)
    levels = wire["levels"]
    # a cell is its level as a dict key, as ``_axis`` gives it: a level with no
    # text (None: inf, say) is no x, and two codes whose levels are equal are one
    # place; the extra slot maps the wire's missing code to ours
    first: dict[Any, int] = {}
    place = [-1 if v is None else first.setdefault(v, len(first)) for v in levels]
    codes = np.asarray([*place, -1], dtype=np.int64)[
        np.where(raw == 2 ** (8 * width) - 1, len(levels), raw)
    ]
    return codes, lambda i: levels[raw[i]]


_Axis = tuple[np.ndarray, Callable[[int], Any]]


def _first_bad(pairs: pd.DataFrame) -> int | None:
    """The lowest group with more than one distinct ident, if any."""
    counts = pairs.drop_duplicates()["g"].value_counts()
    bad = counts.index[counts.to_numpy() > 1]
    return int(bad.min()) if len(bad) else None


def _by_arrays(
    frame: pd.DataFrame,
    *,
    axes: tuple[_Axis, _Axis] | None = None,
    labels: Mapping[str, tuple[np.ndarray, list[str]] | None] | None = None,
    facet: Sequence[str],
    x: str,
    y: str,
    value: str,
    sort: Sequence[str],
    kinds: dict[str, str],
    numeric: bool,
    progress: Callable[[str], None],
) -> Plan | None:
    """``_by_rows``' contents, errors and progress, on whole columns; None
    (before any progress line) for a frame only the row path can read."""
    # the caller's, when it has them (build_facet_cache reuses them)
    reads = labels if labels is not None else {c: _labels(frame[c]) for c in facet}
    if any(reads[c] is None for c in facet):
        return None
    labels = {c: read for c in facet if (read := reads[c]) is not None}
    idents: dict[str, np.ndarray] = {}
    for c in sort:
        ident = _sort_ident(frame[c])
        if ident is None:
            return None
        idents[c] = ident
    value_labels = None if numeric else _labels(frame[value])
    if not numeric and value_labels is None:
        return None

    # the caller's, when it has them (build_facet_cache reuses them)
    (xc, x_at), (yc, y_at) = axes or (
        _axis_codes(frame[x], kinds[x]),
        _axis_codes(frame[y], kinds[y]),
    )
    placed = np.flatnonzero((xc >= 0) & (yc >= 0))
    _left_out(len(frame), len(placed), x, y, progress)

    for c in facet:
        gaps = int((labels[c][0] < 0).sum())
        if gaps:
            raise _no_key(gaps, c)

    # one of the two is used, as ``numeric`` says; both are bound for ty
    scale: Scale
    floats = np.empty(0)
    value_codes, value_texts = np.empty(0, dtype=np.int64), list[str]()
    if value_labels is None:
        scale, floats = _continuous(frame, value)
    else:
        value_codes, value_texts = value_labels
        scale = CategoryScale(sorted(value_texts))

    # cells in the order their first placed row comes, as the row path's dict
    pair = xc[placed] * (int(yc.max()) + 1) + yc[placed]
    cid = pd.factorize(pair)[0].astype(np.int64)
    cells = int(cid.max()) + 1
    first_rows = placed[np.unique(cid, return_index=True)[1]]
    layout = {"x": [x_at(i) for i in first_rows], "y": [y_at(i) for i in first_rows]}

    # groups over every row, in the order their first row comes
    # every step factorizes in row order, so the ids are 0.. in first-row order
    gid = np.zeros(len(frame), dtype=np.int64)
    for c in facet:
        codes, texts = labels[c]
        gid = pd.factorize(gid * len(texts) + codes)[0].astype(np.int64)
    first_of_group = np.unique(gid, return_index=True)[1]
    keys = [tuple(labels[c][1][labels[c][0][i]] for c in facet) for i in first_of_group]

    # the row path raises for the first group, in order, with a repeated cell
    # or a sort column that varies -- the cell before the sort columns
    pg = gid[placed]
    dup = pd.Series(pg * cells + cid).duplicated().to_numpy()
    dup_group = int(pg[dup].min()) if dup.any() else None
    sort_bad = [(_first_bad(pd.DataFrame({"g": pg, "i": idents[c][placed]})), c) for c in sort]
    bad = [g for g in [dup_group, *(g for g, _ in sort_bad)] if g is not None]
    if bad:
        worst = min(bad)
        if worst == dup_group:
            i = int(placed[dup & (pg == worst)][0])
            raise BuildError(
                f"group {keys[worst]!r} has more than one row at ({x_at(i)!r}, {y_at(i)!r}); "
                "aggregate first"
            )
        c = next(c for g, c in sort_bad if g == worst)
        raise BuildError(
            f"group {keys[worst]!r} has more than one {c!r}; a sort value is per group;"
            " sort by a statistic of it instead"
        )

    order = np.argsort(pg, kind="stable")
    bounds = np.searchsorted(pg[order], np.arange(len(keys) + 1))
    groups: list[Group] = []
    for g, k in enumerate(keys):
        at = order[bounds[g] : bounds[g + 1]]
        rows, where = placed[at], cid[at]
        if value_labels is None:
            record = np.full(cells, np.nan)
            record[where] = floats[rows]
            values: list[Any] = record.tolist()  # NaN: the format's missing, as None is
        else:
            codes = np.full(cells, -1, dtype=np.int64)
            codes[where] = value_codes[rows]
            values = [value_texts[v] if v >= 0 else None for v in codes.tolist()]
        first = int(rows[0]) if len(rows) else None
        sort_values = {c: None if first is None else _plain_sort(frame[c].iat[first]) for c in sort}
        groups.append(Group(key=k, sort=sort_values, values=values))
    return scale, cells, layout, groups


# ─── sort by any column (plan-view-plugins-pr5-finish P4) ──────────────────

# The statistics a group can be sorted by, per column kind, in the order a
# gallery offers them (the first is its default). None gives a value a
# meaning: each is a plain summary of the group's values. The index answers
# them per column, so the gallery keeps no list of its own.
STATS: dict[str, tuple[str, ...]] = {
    "number": ("mean", "median", "min", "max", "count"),
    "text": ("distinct", "count"),
    "date": ("min", "max", "count"),
}


# What a stack panel summarises a column's values at one cell by (P5), in the
# order it offers them: a date's values stack only as a count.
STACK_STATS: dict[str, tuple[str, ...]] = {
    "number": ("mean", "median", "min", "max", "sum", "count"),
    "text": ("count", "distinct"),
    "date": ("count",),
}


def column_kind(column: pd.Series) -> str:
    """number, date or text: how a gallery sorts and summarises the column. A
    bool column is text (two labels, not a quantity), as the colour is."""
    dtype = column.dtype
    if isinstance(dtype, pd.DatetimeTZDtype) or (isinstance(dtype, np.dtype) and dtype.kind == "M"):
        return "date"
    if pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype):
        return "number"
    return "text"


def _check_stat(frame: pd.DataFrame, sort: Sequence[str], stat: str) -> None:
    if len(sort) != 1:
        raise BuildError(f"a statistic ({stat!r}) sorts by one column, not {list(sort)!r}")
    field = sort[0]
    kind = column_kind(frame[field])
    if stat not in STATS[kind]:
        raise BuildError(
            f"{stat!r} is not a statistic of {field!r}, a {kind} column:"
            f" pick one of {', '.join(STATS[kind])}"
        )


def _row_positions(
    frame: pd.DataFrame,
    facet: Sequence[str],
    groups: Sequence[Group],
    labels: Mapping[str, tuple[np.ndarray, list[str]] | None] | None = None,
) -> np.ndarray:
    """Each row's group, as its position in ``groups`` (the cache's order),
    matched by the key the builder wrote (canon of each facet column).
    ``labels``: each facet column's ``_labels``, when the caller has them."""
    gid = np.zeros(len(frame), dtype=np.int64)
    reads = []
    for c in facet:
        read = labels[c] if labels is not None else _labels(frame[c])
        if read is None:  # a dtype only the row path reads: its texts, a row at a time
            texts = _texts(frame[c])
            codes, uniques = pd.factorize(np.asarray(texts, dtype=object))
            read = codes.astype(np.int64), [str(u) for u in uniques]
        reads.append(read)
        gid = pd.factorize(gid * len(read[1]) + read[0])[0].astype(np.int64)
    first = np.unique(gid, return_index=True)[1]
    position = {g.key: i for i, g in enumerate(groups)}
    table = np.asarray(
        [position[tuple(texts[codes[i]] for codes, texts in reads)] for i in first],
        dtype=np.int64,
    )
    return table[gid]


def _stat_values(
    column: pd.Series, stat: str, pos: np.ndarray, placed: np.ndarray, n: int
) -> list[Any]:
    """Per group (cache order), ``stat`` over the values its tile draws; None
    for a group with none (it sorts last)."""
    agg = column[placed].groupby(pos[placed]).agg("nunique" if stat == "distinct" else stat)
    out: list[Any] = [None] * n
    for g, v in zip(agg.index.tolist(), agg.tolist(), strict=True):
        out[g] = _plain_sort(v)
    return out


def _tiles(pos: np.ndarray, placed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The placed rows in group order, and where each group's run of them
    starts: what ``_single`` reads every column over. One stable sort per
    build, not one per column (P33)."""
    rows = np.flatnonzero(placed)
    groups = pos[rows]
    order = np.argsort(groups, kind="stable")
    starts = np.flatnonzero(np.r_[True, np.diff(groups[order]) != 0])
    return rows[order], starts


def _single(column: pd.Series, tiles: tuple[np.ndarray, np.ndarray]) -> bool:
    """Whether every group's tile holds at most one value of ``column`` -- the
    builder's own test for a sort value (one ``repr(_plain_sort(v))`` per
    group), so a column offered as single never fails the build."""
    ident = _sort_ident(column)
    if ident is None:
        ident = pd.factorize(column.map(lambda v: repr(_plain_sort(v))))[0]
    # a group is single where its lowest ident is its highest: one pass over
    # the rows in group order (a pandas nunique per column cost ~1 s per 10M)
    rows, starts = tiles
    ident = ident[rows]
    lo, hi = np.minimum.reduceat(ident, starts), np.maximum.reduceat(ident, starts)
    return bool((lo == hi).all())
