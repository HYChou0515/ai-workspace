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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from chart_view.datums import datum_errors
from chart_view.facet import CacheUnusable
from chart_view.query import LayerRows, answer, binned, layer_rows, spec_layers
from chart_view.rules import stack_parts, sum_note, unlinked, where_names
from chart_view.sources import SourceError
from chart_view.spec import SpecError, parse_spec, spec_errors
from chart_view.transforms import TransformError
from chart_view.wire import js_number

ReadSource = Callable[[Any], pd.DataFrame]

# The channel a mark's claim is measured on.
_MEASURE = {"grid": "color", "heatmap": "color", "pie": "theta"}


@dataclass
class Result:
    summary: str | None = None
    errors: list[str] = field(default_factory=list)


def _span(values: pd.Series) -> str:
    return _range(float(values.min()), float(values.max()))


def _range(lo: float, hi: float) -> str:
    def short(v: float) -> str:
        return js_number(float(f"{v:.3g}"))

    return short(lo) if lo == hi else f"{short(lo)}–{short(hi)}"


def _measure(layer: LayerRows) -> str | None:
    field_ = layer.encoding.get(_MEASURE.get(layer.mark, "y"), {}).get("field")
    # A date's storage is no measure: read as numbers it came out as ns or s.
    if field_ is None or field_ not in layer.rows.columns or layer.kinds.get(field_) == "time":
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


def _summed(spec: Mapping[str, Any]) -> list[str]:
    """The summary part saying a highlight's `where:` tests a stack's value as
    each segment's sum (#847/#848 PR 5 P41 row 21) -- or its mean..., the
    op the value names (P42 row 33): a stack's rows are its segments, one per
    slot and colour."""
    where = spec["highlight"].get("where")
    read = where_names(where) if where else []
    said = [
        f"on a stack, {parts.value} is each segment's {parts.op}"
        for ly in spec_layers(spec)
        if (parts := stack_parts(ly)) and parts.value in read
    ]
    return list(dict.fromkeys(said))


def _beside_stacks(spec: Mapping[str, Any], layers: list[LayerRows]) -> tuple[list[str], list[str]]:
    """(the summary parts, the refusals) for a stack whose `keys:` /
    `highlight:` name a field it does not link by -- accepted beside a layer
    that may (#847/#848 P42 row 29 [user, 2026-09-26]: the stack rule limits
    the stack layer only). The summary says what the stack links by; a key
    no layer's rows hold row by row (not binned, not an aggregate or a
    stack's kept value) is refused, as `_highlight` refuses a highlight no
    layer can see: nothing would ever write it."""
    notes: list[str] = []
    errors: list[str] = []
    for layer in spec_layers(spec):
        parts = stack_parts(layer)
        if parts is None:
            continue
        other = unlinked(spec, parts)
        if not other.keys and other.lights():
            continue
        note = sum_note(parts)
        notes.append(note)
        errors += [
            f"keys: no layer can write '{k}' — {note}, and no other layer has '{k}' row by row"
            for k in other.keys
            if not any(
                k in ly.rows.columns
                and k not in ly.measured
                and not binned(spec, ly)
                and ly.rows[k].notna().any()
                for ly in layers
            )
        ]
    return list(dict.fromkeys(notes)), list(dict.fromkeys(errors))


def _empty_keys(spec: Mapping[str, Any], layers: list[LayerRows]) -> list[str]:
    """A key no row holds a value of is no key (#847/#848 PR 5 P44 row 35): a
    brush over its rows would name no key, and a marking by it lights nothing.
    Judged where a layer has the column; a key no layer has is left to the
    other checks."""
    return [
        f"keys: '{k}' is empty on every row — a key with no value links nothing"
        for k in spec.get("keys", [])
        if any(k in ly.rows.columns for ly in layers)
        and not any(ly.rows[k].notna().any() for ly in layers if k in ly.rows.columns)
    ]


BuildFacet = Callable[[str], Mapping[str, Any]]


def _build_facet(text: str) -> Mapping[str, Any]:
    # pandas-heavy, and only a facet spec needs it
    from chart_view.facet.build_command import build

    return build(text, echo=False)


def _facet(spec: Mapping[str, Any], text: str, build: BuildFacet) -> Result:
    """A gallery is checked by building it (#848 P20): the build's own checks
    -- a table-file source, the facet / sort / x / y / colour columns, one row
    per cell, one sort value per group, a statistic that fits -- so the gate
    and the gallery cannot disagree, and the gallery then opens on the cache
    this built."""
    try:
        built = build(text)
    except (ValueError, CacheUnusable) as e:
        return Result(errors=str(e).splitlines())
    parts = [f"{built['groups']} groups over {built['cells']} cells"]
    colour = spec["encoding"]["color"]["field"]
    scale = built["scale"]
    if scale["kind"] == "category" and scale["labels"]:
        n = len(scale["labels"])
        parts.append(f"{colour}: {n} categor{'y' if n == 1 else 'ies'}")
    elif scale["kind"] == "category" or scale.get("empty"):
        # a column with no value has no categories and no range (P44 row 39):
        # it said "<colour> 0" -- the range a column of 0s has
        parts.append(f"{colour} has no value")
    else:
        parts.append(f"{colour} {_range(scale['lo'], scale['hi'])}")
    return Result(summary="; ".join(parts))


def check(text: str, read_source: ReadSource, build_facet: BuildFacet = _build_facet) -> Result:
    """Refusal lines, or the one-line summary, for a chart file's text."""
    try:
        spec = parse_spec(text)
    except SpecError as e:
        return Result(errors=[str(e)])
    errors = spec_errors(spec)
    if errors:
        return Result(errors=errors)
    if "facet" in spec:
        return _facet(spec, text, build_facet)
    try:
        layers = layer_rows(spec, read_source(spec["source"]))
    except (SourceError, TransformError) as e:
        return Result(errors=[str(e)])
    # Judged on the answer the chart receives, not on the rows pandas holds.
    errors = datum_errors(spec, lambda: answer(spec, layers))
    if errors:
        return Result(errors=errors)

    notes, errors = _beside_stacks(spec, layers)
    errors = errors or _empty_keys(spec, layers)
    if errors:
        return Result(errors=errors)

    drawn = next((ly for ly in layers if len(ly.rows.columns)), layers[0])
    parts: list[str] = []
    if "highlight" in spec:
        try:
            parts.append(_highlight(spec, layers))
        except ValueError as e:
            return Result(errors=[str(e)])
        parts += _summed(spec)
    elif binned(spec, drawn):
        parts.append(f"{len(drawn.rows):,} points drawn as bins")
    else:
        parts.append(f"{len(drawn.rows)} rows")
    parts += notes
    measure = _measure(drawn)
    if measure:
        parts.append(measure)
    return Result(summary="; ".join(parts))
