"""`validate`: the check `show_file` runs before it shows a chart (plan Q9).

It refuses what would draw wrong or draw nothing — a key outside the schema, a
source it cannot read, a column the data lacks, a highlight that lights no row
or every row — with lines that name what to fix. It accepts with ONE summary
line, e.g. ``highlight matches 3/25 rows; fail_rate 0.02–0.41``, which
`show_file` appends to its reply so the author sees what the chart claims.

Counts are over each layer's rows as `query` computes them, before a big
scatter is binned. The source reader is passed in: a table file reads with
`sources.read_table`, `{entity: …}` through the platform's reader.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from chart_view.query import LayerRows, binned, layer_rows, mark_of, spec_layers
from chart_view.sources import SourceError
from chart_view.spec import SpecError, parse_spec, spec_errors, spec_schema
from chart_view.transforms import TransformError
from chart_view.wire import epoch_ms, js_number

ReadSource = Callable[[Any], pd.DataFrame]

# The channel a mark's claim is measured on.
_MEASURE = {"grid": "color", "heatmap": "color", "pie": "theta"}


@dataclass
class Result:
    summary: str | None = None
    errors: list[str] = field(default_factory=list)


def _span(values: pd.Series) -> str:
    def short(v: float) -> str:
        return js_number(float(f"{v:.3g}"))

    lo, hi = float(values.min()), float(values.max())
    return short(lo) if lo == hi else f"{short(lo)}–{short(hi)}"


def _measure(layer: LayerRows) -> str | None:
    field_ = layer.encoding.get(_MEASURE.get(layer.mark, "y"), {}).get("field")
    if field_ is None or field_ not in layer.rows.columns:
        return None
    numbers = pd.to_numeric(layer.rows[field_], errors="coerce")
    numbers = numbers[np.isfinite(numbers)]  # ±inf has no place on an axis either
    return None if numbers.empty else f"{field_} {_span(numbers)}"


def _highlight(spec: Mapping[str, Any], layers: list[LayerRows]) -> str:
    """The summary part for `highlight:`; raises ValueError when it is useless."""
    seen = [ly for ly in layers if ly.lit is not None]
    if not seen:
        named = list(spec["highlight"].get("values", {})) or [spec["highlight"]["where"]]
        raise ValueError(f"highlight: no layer has the columns it names ({', '.join(named)})")
    lit = sum(int(ly.lit.sum()) for ly in seen if ly.lit is not None)
    rows = sum(len(ly.rows) for ly in seen)
    if lit == 0:
        raise ValueError(f"highlight: matches 0 of {rows} rows — it lights nothing")
    if lit == rows:
        raise ValueError(f"highlight: matches all {rows} rows — nothing stands apart")
    return f"highlight matches {lit}/{rows} rows"


_INSTANT = re.compile(spec_schema()["$defs"]["instant"]["pattern"])


def instant_ms(text: str) -> float | None:
    """Epoch ms for a date `datum`, or None when the chart cannot place it.

    THE reading of a datum: validate refuses what this returns None for, and
    the renderer's `parseInstant` is held to it (wire-corpus/instants.json is
    written from it). A datum takes only the schema's `$defs.instant` forms,
    read the way the data is (`epoch_ms`); a date the calendar lacks
    ("2024-02-30") is None."""
    if not _INSTANT.fullmatch(text):
        return None
    ms = float(epoch_ms(pd.Series([text], dtype=object))[0])
    return None if np.isnan(ms) else ms


def datum_errors(spec: Mapping[str, Any]) -> list[str]:
    """A text datum the renderer could not place on its axis.

    Without this the rule drew nowhere and said nothing. The axis is the one
    the renderer draws: the first layer's channel with a field. On a temporal
    axis — a grid's too, whose cells are found by date — the text must be a
    date `instant_ms` reads; on a number axis it must be a number, not text
    (a grid finds a number's cell by its label, so text is fine there). Which
    datum is placed and which refused is held to the renderer by
    wire-corpus/datum-axes.json."""
    layers = spec_layers(spec)
    grid = any(mark_of(ly)[0] == "grid" for ly in layers)
    errors = []
    for c in ("x", "y"):
        axis = next(
            (ly["encoding"][c] for ly in layers if "field" in ly["encoding"].get(c, {})), None
        )
        kind = None if axis is None else axis.get("type")
        for ly in layers:
            datum = ly["encoding"].get(c, {}).get("datum")
            if not isinstance(datum, str):
                continue
            if kind == "temporal" and instant_ms(datum) is None:
                errors.append(
                    f"{c}: datum {datum!r} is not a date the chart can place — "
                    "write it as 2024-03-01, 2024-03-01T12:00, or with a zone as "
                    "2024-03-01T12:00:00+08:00"
                )
            elif kind == "quantitative" and not grid:
                errors.append(f"{c}: datum {datum!r} is text on a number axis — write a number")
    return errors


def check(text: str, read_source: ReadSource) -> Result:
    """Refusal lines, or the one-line summary, for a chart file's text."""
    try:
        spec = parse_spec(text)
    except SpecError as e:
        return Result(errors=[str(e)])
    errors = spec_errors(spec) or datum_errors(spec)
    if errors:
        return Result(errors=errors)
    try:
        layers = layer_rows(spec, read_source(spec["source"]))
    except (SourceError, TransformError) as e:
        return Result(errors=[str(e)])

    drawn = next((ly for ly in layers if len(ly.rows.columns)), layers[0])
    parts: list[str] = []
    if "highlight" in spec:
        try:
            parts.append(_highlight(spec, layers))
        except ValueError as e:
            return Result(errors=[str(e)])
    elif binned(spec, drawn):
        parts.append(f"{len(drawn.rows):,} points drawn as bins")
    else:
        parts.append(f"{len(drawn.rows)} rows")
    measure = _measure(drawn)
    if measure:
        parts.append(measure)
    return Result(summary="; ".join(parts))
