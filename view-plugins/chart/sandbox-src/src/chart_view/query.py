"""`query`: what each layer of a (validated) chart spec draws, over its source.

The answer, `build(spec, frame)`::

    {"format": 1, "layers": [layer, ...]}          # one per `layer:` (or one)

    layer = {
      "mark": "scatter",                             # the mark type
      "rows": n,
      "columns": {name: wire column},                # chart_view.wire
      "highlight": bitset | None,                    # which rows are lit
      "lit": k | None,                               # how many
      "binned": {"points": N, "bins": n} | None,     # a scatter over bin_threshold
      "outliers": {"rows": m, "columns": {...}} | None,   # boxplot only
    }

A layer sends the fields its channels name (under those names; an encoding
`aggregate` replaces a field's values with the aggregate) plus the spec's
`keys:` it still has, so a selection can say which keys it covers. Summaries
a mark computes travel under `$`-names no data column can have: `$lo $q1 $mid
$q3 $hi` (boxplot), `$lo $mid $hi` (errorbar), `$count` (a binned scatter);
and `$key.<name>` repeats a key a channel sends as numbers / time / q8, as
the marking strings a selection names it by (`selection.ts:keyColumn`).

`highlight:` resolves on each layer's own rows. A layer whose rows cannot see
the columns it names is not lit (a rule has no rows at all); an expression that
does not evaluate is an error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from pandas.errors import UndefinedVariableError

from chart_view.rules import stack_parts, unlinked
from chart_view.transforms import (
    TransformError,
    aggregate,
    apply_transforms,
    dates_one_of,
    need_columns,
    row_mask,
)
from chart_view.wire import bitset, canon, encode_column, epoch_ms, unhashable_as_text, zone_name

FORMAT = 1
DEFAULT_BIN_THRESHOLD = 10_000
BINS_PER_AXIS = 256

_CHANNELS = ("x", "y", "x2", "y2", "color", "size", "theta", "text")
_CONTINUOUS = ("quantitative", "temporal")


def spec_layers(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if "layer" in spec:
        return list(spec["layer"])
    return [{"mark": spec["mark"], "encoding": spec["encoding"]}]


def mark_of(layer: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    mark = layer["mark"]
    return (mark, {}) if isinstance(mark, str) else (mark["type"], mark)


def _field_channels(encoding: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """(channel, definition) for every channel that names a field, tooltips too."""
    out = [(c, encoding[c]) for c in _CHANNELS if c in encoding and "field" in encoding[c]]
    tips = encoding.get("tooltip")
    # Every tooltip names a field: the schema requires one on each channel
    # (a tooltip list's items too) and refuses a datum there; the cli's
    # `query` and `validate` run `spec_errors` before `layer_rows`.
    for tip in tips if isinstance(tips, list) else [tips] if tips else []:
        out.append(("tooltip", tip))
    return out


def _kinds(mark: str, channels: list[tuple[str, Mapping[str, Any]]]) -> dict[str, str]:
    """Each sent field's wire kind; the first channel naming a field decides it."""
    kinds: dict[str, str] = {}
    for channel, d in channels:
        if d["field"] in kinds:
            continue
        if mark == "grid" and channel == "color" and d["type"] == "quantitative":
            kinds[d["field"]] = "q8"
        elif d["type"] == "quantitative" or d.get("aggregate"):
            kinds[d["field"]] = "f64"
        elif d["type"] == "temporal":
            kinds[d["field"]] = "time"
        else:
            kinds[d["field"]] = "cat"
    return kinds


def _measures(channels: list[tuple[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """An aggregate item per field a channel aggregates (the first channel naming it)."""
    items: dict[str, dict[str, Any]] = {}
    for _, d in channels:
        if d.get("aggregate") and d["field"] not in items:
            items[d["field"]] = {"op": d["aggregate"], "field": d["field"], "as": d["field"]}
    return list(items.values())


def _implicit_aggregate(
    df: pd.DataFrame, channels: list[tuple[str, Mapping[str, Any]]]
) -> tuple[pd.DataFrame, list[str]]:
    """Vega-Lite's encoding `aggregate`: group by every other field channel.
    Also the fields it aggregated (what the answer calls `measured`)."""
    items = _measures(channels)
    if not items:
        return df, []
    names = {i["field"] for i in items}
    groupby = list(dict.fromkeys(d["field"] for _, d in channels if d["field"] not in names))
    return aggregate(df, items, groupby), sorted(names)


def stacked(mark: str, props: Mapping[str, Any]) -> bool:
    """Whether a layer is a stack: a bar or an area with `stack: true` (the
    marks the renderer stacks)."""
    return mark in ("bar", "area") and props.get("stack") is True


def _stack_sum(
    df: pd.DataFrame, channels: list[tuple[str, Mapping[str, Any]]], encoding: Mapping[str, Any]
) -> tuple[pd.DataFrame, list[str]]:
    """A stack's rows, one per slot and colour (#847/#848 PR 5 P40 row 18).

    Exactly an `aggregate: sum` on the value channel (its own `aggregate` op
    if it has one), grouped by the slot -- y when y is a category (a
    horizontal bar), else x, as the renderer stacks -- and the colour, unless
    that is by value (`validate` refuses a stack coloured so: which rows form
    one segment would not be defined). A missing value adds nothing, and a
    slot of nothing but missing values sums to 0, as `aggregate: sum` does. A
    field another channel aggregates is aggregated the same way; any other
    field keeps the value the group's rows share, and is empty where they
    differ. Also the fields it summed or kept (what the answer calls
    `measured`): neither is a key, so a selection writes the slot and colour.
    """
    horizontal = encoding["y"]["type"] in ("nominal", "ordinal")
    value = encoding["x" if horizontal else "y"]
    groups = [encoding["y" if horizontal else "x"]["field"]]
    colour = encoding.get("color")
    if colour and "field" in colour and colour["type"] != "quantitative":
        groups.append(colour["field"])
    groups = list(dict.fromkeys(g for g in groups if g != value["field"]))
    items = [{"op": value.get("aggregate", "sum"), "field": value["field"], "as": value["field"]}]
    items += [i for i in _measures(channels) if i["field"] != value["field"]]
    summed = {i["field"] for i in items}
    kept = list(
        dict.fromkeys(d["field"] for _, d in channels if d["field"] not in {*groups, *summed})
    )
    out = aggregate(df, items, groups)
    if kept:
        out = out.assign(**_shared(df, kept, groups))
    return out, sorted({*summed, *kept})


_NULLABLE = {"b": "boolean", "i": "Int64", "u": "UInt64"}


def _shared(df: pd.DataFrame, fields: list[str], groups: list[str]) -> dict[str, pd.Series]:
    """Per group of `groups` that occurs (in `aggregate`'s order: the same, observed,
    grouping -- #847/#848 PR 5 P41 row 22), each field's value where
    every row of the group has that one value, else missing. A list or
    mapping is compared as its marking text; the value kept is the row's own."""
    frame = df.assign(**{k: unhashable_as_text(df[k]) for k in groups})
    codes = frame.groupby(groups, dropna=False, sort=True, observed=True).ngroup().to_numpy()
    first = pd.Series(np.arange(len(df))).groupby(codes).first().to_numpy()
    out: dict[str, pd.Series] = {}
    for f in fields:
        one = (unhashable_as_text(df[f]).groupby(codes).nunique(dropna=False) == 1).to_numpy()
        picked = df[f].iloc[first].reset_index(drop=True)
        if picked.dtype.kind in "iub":
            # a missing value would turn integers into floats (1 -> 1.0, a
            # label "1.0") and true / false into numbers: nullable, they stay
            # -- an unsigned one unsigned, which 2**63 and past need (P41 row 23)
            picked = picked.astype(_NULLABLE[picked.dtype.kind])
        out[f] = picked.where(one)
    return out


def _group_fields(channels: list[tuple[str, Mapping[str, Any]]], value: str) -> list[str]:
    fields = [d["field"] for c, d in channels if c in ("x", "color") and d["field"] != value]
    return list(dict.fromkeys(fields))


def _boxplot(df: pd.DataFrame, channels, encoding) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    value = encoding["y"]["field"]
    groups = _group_fields(channels, value)
    df = df.assign(**{g: unhashable_as_text(df[g]) for g in groups})
    rows, outliers = [], []
    for key, part in (
        df.groupby(groups, dropna=False, sort=True, observed=True) if groups else [((), df)]
    ):
        v = pd.to_numeric(part[value], errors="coerce").dropna()
        q1, mid, q3 = (float(v.quantile(q)) for q in (0.25, 0.5, 0.75))
        reach = 1.5 * (q3 - q1)
        inside = v[(v >= q1 - reach) & (v <= q3 + reach)]
        keys = dict(zip(groups, key if isinstance(key, tuple) else (key,), strict=True))
        rows.append(
            {**keys, "$lo": inside.min(), "$q1": q1, "$mid": mid, "$q3": q3, "$hi": inside.max()}
        )
        outliers += [{**keys, value: float(o)} for o in v[~v.index.isin(inside.index)]]
    columns = [*groups, "$lo", "$q1", "$mid", "$q3", "$hi"]  # an empty group set keeps them
    return pd.DataFrame.from_records(rows, columns=columns), (
        pd.DataFrame(outliers) if outliers else None
    )


def _errorbar(df: pd.DataFrame, channels, encoding, extent: str) -> pd.DataFrame:
    value = encoding["y"]["field"]
    groups = _group_fields(channels, value)
    df = df.assign(**{g: unhashable_as_text(df[g]) for g in groups})
    v = pd.to_numeric(df[value], errors="coerce")
    grouped = (
        v.groupby([df[g] for g in groups], dropna=False, sort=True, observed=True)
        if groups
        else None
    )

    def stat(fn: str, *args: Any) -> pd.Series:
        return (
            getattr(grouped, fn)(*args)
            if grouped is not None
            else pd.Series([getattr(v, fn)(*args)])
        )

    if extent == "iqr":
        lo, mid, hi = stat("quantile", 0.25), stat("quantile", 0.5), stat("quantile", 0.75)
    else:
        mid = stat("mean")
        spread = stat("std") if extent == "stdev" else stat("sem")
        lo, hi = mid - spread, mid + spread
    out = pd.DataFrame({"$lo": lo, "$mid": mid, "$hi": hi})
    return out.reset_index() if groups else out


def _bin(
    df: pd.DataFrame, encoding: Mapping[str, Any], lit: pd.Series | None
) -> tuple[pd.DataFrame, pd.Series | None]:
    """Collapse a scatter into at most BINS_PER_AXIS² cells per colour, each
    drawn at its centre with a `$count`; a cell is lit when any point in it is."""
    frame = df.copy()
    keys: list[str] = []
    for axis in ("x", "y"):
        field = encoding[axis]["field"]
        raw = frame[field]
        if encoding[axis]["type"] == "temporal":
            # The same reading the wire's `time` column uses.
            numbers = pd.Series(epoch_ms(raw), index=frame.index)
        else:
            numbers = pd.to_numeric(raw, errors="coerce")
        low, high = numbers.min(), numbers.max()
        width = (high - low) / BINS_PER_AXIS or 1.0
        cell = np.floor((numbers - low) / width).clip(upper=BINS_PER_AXIS - 1)
        # A temporal centre stays epoch ms, which the wire's `time` column
        # reads as such: a datetime would go through nanoseconds, which cannot
        # hold 9999-12-31.
        frame[field] = low + (cell + 0.5) * width
        keys.append(field)
    if "color" in encoding and "field" in encoding["color"]:
        keys.append(encoding["color"]["field"])
    keys = list(dict.fromkeys(keys))
    frame = frame.assign(**{k: unhashable_as_text(frame[k]) for k in keys})
    frame["$lit"] = lit if lit is not None else False
    grouped = frame.groupby(keys, dropna=False, sort=True, observed=True)
    sizes = grouped.size()
    assert isinstance(sizes, pd.Series)
    out = sizes.rename("$count").reset_index()
    if lit is None:
        return out, None
    any_lit = grouped["$lit"].any()
    assert isinstance(any_lit, pd.Series)
    return out, any_lit.reset_index(drop=True)


def _canon_values(s: pd.Series) -> pd.Series:
    return s.map(canon)


def _highlight(df: pd.DataFrame, highlight: Mapping[str, Any] | None) -> pd.Series | None:
    """Which of `df`'s rows the spec lights, or None when this layer can't tell."""
    if not highlight:
        return None
    if "where" in highlight:
        if df.empty:
            return None
        try:
            return row_mask(df, highlight["where"], what="highlight")
        except TransformError as e:
            if isinstance(e.__cause__, UndefinedVariableError):
                return None
            raise
    values: Mapping[str, Sequence[Any]] = highlight["values"]
    if not set(values) <= set(df.columns):
        return None
    mask = pd.Series(True, index=df.index)
    for column, wanted in values.items():
        # A date column reads its values as `oneOf` does; any other is marked by its text.
        hit = dates_one_of(df[column], column, wanted, f"highlight values on {column!r}:")
        if hit is None:
            hit = _canon_values(df[column]).isin({canon(v) for v in wanted})
        mask &= hit
    return mask


def _encode(df: pd.DataFrame, kinds: Mapping[str, str]) -> dict[str, Any]:
    return {name: encode_column(df[name], kind) for name, kind in kinds.items()}


@dataclass
class LayerRows:
    """One layer's rows before binning and encoding — what `validate` reads."""

    mark: str
    encoding: Mapping[str, Any]
    rows: pd.DataFrame
    kinds: dict[str, str]
    lit: pd.Series | None
    outliers: pd.DataFrame | None = None
    outlier_kinds: dict[str, str] = field(default_factory=dict)
    # the fields sent holding an aggregate (a sum, a mean...) or, in a stack,
    # the value its rows share: never a key (see the module doc)
    measured: list[str] = field(default_factory=list)


def _layer_rows(spec: Mapping[str, Any], base: pd.DataFrame, layer: Mapping[str, Any]) -> LayerRows:
    mark, props = mark_of(layer)
    encoding = layer["encoding"]
    channels = _field_channels(encoding)
    df = apply_transforms(base, layer.get("transform", []))
    need_columns(df, *(d["field"] for _, d in channels))
    kinds = _kinds(mark, channels)
    outliers, outlier_kinds = None, {}
    measured: list[str] = []

    if not channels:  # a rule drawn only from `datum`s
        df = df.iloc[0:0][[]]
    elif mark == "boxplot":
        df, outliers = _boxplot(df, channels, encoding)
        groups = {f: kinds[f] for f in _group_fields(channels, encoding["y"]["field"])}
        outlier_kinds = {**groups, encoding["y"]["field"]: "f64"}
        kinds = {**groups, **{k: "f64" for k in ("$lo", "$q1", "$mid", "$q3", "$hi")}}
    elif mark == "errorbar" and "y2" not in encoding:
        df = _errorbar(df, channels, encoding, props.get("extent", "stderr"))
        kinds = {f: kinds[f] for f in _group_fields(channels, encoding["y"]["field"])}
        kinds.update({k: "f64" for k in ("$lo", "$mid", "$hi")})
    elif stacked(mark, props):
        df, measured = _stack_sum(df, channels, encoding)
        value = encoding["x" if encoding["y"]["type"] in ("nominal", "ordinal") else "y"]
        # The value channel is quantitative (`validate`, P41 row 25), but a
        # slot naming the same field as a category names it first: the sum
        # still travels as a number.
        kinds[value["field"]] = "f64"
    else:
        df, measured = _implicit_aggregate(df, channels)

    for k in spec.get("keys", []):
        if k not in df.columns:
            continue
        if k not in kinds:
            kinds[k] = "cat"
        elif kinds[k] != "cat":
            # A key a channel sends as numbers / time / q8 ALSO goes as marking
            # strings, so a selection names the key the way `canon` writes it.
            df = df.assign(**{f"$key.{k}": df[k]})
            kinds[f"$key.{k}"] = "cat"
    lit = _highlight(df, spec.get("highlight"))
    parts = stack_parts(layer)
    if parts is not None and not unlinked(spec, parts).lights():
        # a stack is not lit by a field it does not link by, though its
        # tooltip keeps one where a segment's rows share it (P42 row 29)
        lit = None
    return LayerRows(mark, encoding, df, kinds, lit, outliers, outlier_kinds, measured)


def layer_rows(spec: Mapping[str, Any], frame: pd.DataFrame) -> list[LayerRows]:
    """Each layer's rows over `frame` (its source, already read)."""
    base = apply_transforms(frame, spec.get("transform", []))
    return [_layer_rows(spec, base, ly) for ly in spec_layers(spec)]


def binned(spec: Mapping[str, Any], layer: LayerRows) -> bool:
    """Whether `layer` is a scatter drawn as bins."""
    return (
        layer.mark == "scatter"
        and len(layer.rows) > spec.get("bin_threshold", DEFAULT_BIN_THRESHOLD)
        and all(layer.encoding[a]["type"] in _CONTINUOUS for a in ("x", "y"))
    )


def _answer(spec: Mapping[str, Any], layer: LayerRows) -> dict[str, Any]:
    df, kinds, lit = layer.rows, layer.kinds, layer.lit
    out: dict[str, Any] = {
        "mark": layer.mark,
        "binned": None,
        "outliers": None,
        "measured": layer.measured,
    }
    if layer.outliers is not None:
        columns = _encode(layer.outliers, layer.outlier_kinds)
        out["outliers"] = {"rows": len(layer.outliers), "columns": columns}
    # a zoned time column's zone (#847/#848 P14), read before binning redraws
    # its points as the epoch-ms centres of their cells
    zones = {f: df[f].dtype.tz for f in kinds if isinstance(df[f].dtype, pd.DatetimeTZDtype)}
    if binned(spec, layer):
        points = len(df)
        df, lit = _bin(df, layer.encoding, lit)
        kinds = {f: k for f, k in kinds.items() if f in df.columns}
        kinds["$count"] = "f64"
        out["binned"] = {"points": points, "bins": len(df)}
    out["rows"] = len(df)
    out["columns"] = _encode(df, kinds)
    for f, zone in zones.items():
        if kinds.get(f) == "time":
            out["columns"][f]["zone"] = zone_name(zone)
    out["highlight"] = bitset(lit) if lit is not None else None
    out["lit"] = int(lit.sum()) if lit is not None else None
    return out


def build(spec: Mapping[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """What `spec` draws over `frame` (its source, already read)."""
    return answer(spec, layer_rows(spec, frame))


def answer(spec: Mapping[str, Any], layers: list[LayerRows]) -> dict[str, Any]:
    """The query's reply for layers already computed."""
    return {"format": FORMAT, "layers": [_answer(spec, ly) for ly in layers]}
