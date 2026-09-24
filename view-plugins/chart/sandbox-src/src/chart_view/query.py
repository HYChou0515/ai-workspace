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
$q3 $hi` (boxplot), `$lo $mid $hi` (errorbar), `$count` (a binned scatter).

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

from chart_view.transforms import (
    TransformError,
    aggregate,
    apply_transforms,
    need_columns,
    row_mask,
)
from chart_view.wire import bitset, canon, encode_column

FORMAT = 1
DEFAULT_BIN_THRESHOLD = 10_000
BINS_PER_AXIS = 256

_CHANNELS = ("x", "y", "x2", "y2", "color", "size", "theta", "text")
_CONTINUOUS = ("quantitative", "temporal")


def _layers(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if "layer" in spec:
        return list(spec["layer"])
    return [{"mark": spec["mark"], "encoding": spec["encoding"]}]


def _mark(layer: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    mark = layer["mark"]
    return (mark, {}) if isinstance(mark, str) else (mark["type"], mark)


def _field_channels(encoding: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """(channel, definition) for every channel that names a field, tooltips too."""
    out = [(c, encoding[c]) for c in _CHANNELS if c in encoding and "field" in encoding[c]]
    tips = encoding.get("tooltip")
    for tip in tips if isinstance(tips, list) else [tips] if tips else []:
        if "field" in tip:
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


def _implicit_aggregate(
    df: pd.DataFrame, channels: list[tuple[str, Mapping[str, Any]]]
) -> pd.DataFrame:
    """Vega-Lite's encoding `aggregate`: group by every other field channel."""
    measured = [d for _, d in channels if d.get("aggregate")]
    if not measured:
        return df
    items = [{"op": d["aggregate"], "field": d["field"], "as": d["field"]} for d in measured]
    names = {d["field"] for d in measured}
    groupby = list(dict.fromkeys(d["field"] for _, d in channels if d["field"] not in names))
    return aggregate(df, items, groupby)


def _group_fields(channels: list[tuple[str, Mapping[str, Any]]], value: str) -> list[str]:
    fields = [d["field"] for c, d in channels if c in ("x", "color") and d["field"] != value]
    return list(dict.fromkeys(fields))


def _boxplot(df: pd.DataFrame, channels, encoding) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    value = encoding["y"]["field"]
    groups = _group_fields(channels, value)
    rows, outliers = [], []
    for key, part in df.groupby(groups, dropna=False, sort=True) if groups else [((), df)]:
        v = pd.to_numeric(part[value], errors="coerce").dropna()
        q1, mid, q3 = (float(v.quantile(q)) for q in (0.25, 0.5, 0.75))
        reach = 1.5 * (q3 - q1)
        inside = v[(v >= q1 - reach) & (v <= q3 + reach)]
        keys = dict(zip(groups, key if isinstance(key, tuple) else (key,), strict=True))
        rows.append(
            {**keys, "$lo": inside.min(), "$q1": q1, "$mid": mid, "$q3": q3, "$hi": inside.max()}
        )
        outliers += [{**keys, value: float(o)} for o in v[~v.index.isin(inside.index)]]
    return pd.DataFrame(rows), (pd.DataFrame(outliers) if outliers else None)


def _errorbar(df: pd.DataFrame, channels, encoding, extent: str) -> pd.DataFrame:
    value = encoding["y"]["field"]
    groups = _group_fields(channels, value)
    v = pd.to_numeric(df[value], errors="coerce")
    grouped = v.groupby([df[g] for g in groups], dropna=False, sort=True) if groups else None

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
            stamps = pd.to_datetime(raw, errors="coerce", utc=True, format="mixed")
            numbers = pd.Series(pd.DatetimeIndex(stamps).asi8 / 1e6, index=frame.index)
            numbers = numbers.where(stamps.notna())
        else:
            numbers = pd.to_numeric(raw, errors="coerce")
        low, high = numbers.min(), numbers.max()
        width = (high - low) / BINS_PER_AXIS or 1.0
        cell = np.floor((numbers - low) / width).clip(upper=BINS_PER_AXIS - 1)
        frame[field] = low + (cell + 0.5) * width
        if encoding[axis]["type"] == "temporal":
            frame[field] = pd.to_datetime(frame[field], unit="ms", utc=True)
        keys.append(field)
    if "color" in encoding and "field" in encoding["color"]:
        keys.append(encoding["color"]["field"])
    keys = list(dict.fromkeys(keys))
    frame["$lit"] = lit if lit is not None else False
    grouped = frame.groupby(keys, dropna=False, sort=True)
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
        mask &= _canon_values(df[column]).isin({canon(v) for v in wanted})
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


def _layer_rows(spec: Mapping[str, Any], base: pd.DataFrame, layer: Mapping[str, Any]) -> LayerRows:
    mark, props = _mark(layer)
    encoding = layer["encoding"]
    channels = _field_channels(encoding)
    df = apply_transforms(base, layer.get("transform", []))
    need_columns(df, *(d["field"] for _, d in channels))
    kinds = _kinds(mark, channels)
    outliers, outlier_kinds = None, {}

    if not channels:  # a rule / text drawn only from `datum`s
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
    else:
        df = _implicit_aggregate(df, channels)

    keys = [k for k in spec.get("keys", []) if k in df.columns and k not in kinds]
    kinds.update({k: "cat" for k in keys})
    lit = _highlight(df, spec.get("highlight"))
    return LayerRows(mark, encoding, df, kinds, lit, outliers, outlier_kinds)


def layer_rows(spec: Mapping[str, Any], frame: pd.DataFrame) -> list[LayerRows]:
    """Each layer's rows over `frame` (its source, already read)."""
    base = apply_transforms(frame, spec.get("transform", []))
    return [_layer_rows(spec, base, ly) for ly in _layers(spec)]


def binned(spec: Mapping[str, Any], layer: LayerRows) -> bool:
    """Whether `layer` is a scatter drawn as bins."""
    return (
        layer.mark == "scatter"
        and len(layer.rows) > spec.get("bin_threshold", DEFAULT_BIN_THRESHOLD)
        and all(layer.encoding[a]["type"] in _CONTINUOUS for a in ("x", "y"))
    )


def _answer(spec: Mapping[str, Any], layer: LayerRows) -> dict[str, Any]:
    df, kinds, lit = layer.rows, layer.kinds, layer.lit
    out: dict[str, Any] = {"mark": layer.mark, "binned": None, "outliers": None}
    if layer.outliers is not None:
        columns = _encode(layer.outliers, layer.outlier_kinds)
        out["outliers"] = {"rows": len(layer.outliers), "columns": columns}
    if binned(spec, layer):
        points = len(df)
        df, lit = _bin(df, layer.encoding, lit)
        kinds = {f: k for f, k in kinds.items() if f in df.columns}
        kinds["$count"] = "f64"
        out["binned"] = {"points": points, "bins": len(df)}
    out["rows"] = len(df)
    out["columns"] = _encode(df, kinds)
    out["highlight"] = bitset(lit) if lit is not None else None
    out["lit"] = int(lit.sum()) if lit is not None else None
    return out


def build(spec: Mapping[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """What `spec` draws over `frame` (its source, already read)."""
    return {"format": FORMAT, "layers": [_answer(spec, ly) for ly in layer_rows(spec, frame)]}
