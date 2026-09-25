"""Where a rule's `datum` sits, decided on what the renderer reads.

A datum never visits the data: the renderer places it on whatever axis the
chart drew, and a datum that axis cannot show used to draw nothing — or, as
`null`, break the whole chart. `datum_errors` refuses those in validate and in
query alike. It judges the query's own answer, decoded as the renderer
decodes it (`wire.decode_column`, held to wire-corpus/ with the renderer's
`decodeColumn`) — the rows after binning, each layer in its own kind — and
repeats the renderer's `axisFor` / `lattice` choices, which
wire-corpus/datum-axes.json (written from this module, with each case's real
answer) holds `datum-axes.test.ts` to.

The axis is the grid's when a layer is a grid (its cells are the axis),
otherwise the first layer's channel with a field. A rule draws its `y` datum
when it has one, else its `x` datum. On the axis a datum is:
- temporal: a number (epoch ms) or a date `instant_ms` reads;
- quantitative: a finite number (never text), above 0 on a log scale;
- nominal / ordinal: a value some layer sends for that field, compared as text;
- a grid's: a cell — its label compared as text (a date first, on a temporal
  grid) — or a number between two numeric cells.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from chart_view.instants import INSTANT as _INSTANT
from chart_view.query import mark_of, spec_layers
from chart_view.wire import canon, decode_column, decode_distinct, epoch_ms

# The renderer's lattice fills an integer axis's gaps unless the span is this
# many times wider than its values (web/src/raster.ts FILL_LIMIT).
_FILL_LIMIT = 4


def instant_ms(text: str) -> float | None:
    """Epoch ms for a date `datum`, or None when the chart cannot place it.

    THE reading of a date datum: the renderer's `parseInstant` is held to it
    (wire-corpus/instants.json is written from it). A datum takes only the
    schema's `$defs.instant` forms, read the way the data is (`epoch_ms`); a
    date the calendar lacks ("2024-02-30") is None."""
    if not _INSTANT.fullmatch(text):
        return None
    ms = float(epoch_ms(pd.Series([text], dtype=object))[0])
    return None if np.isnan(ms) else ms


def _number(v: Any) -> bool:
    """A JavaScript number: a bool is not one there."""
    return isinstance(v, int | float) and not isinstance(v, bool)


def drawn_datums(spec: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """(channel, datum) for each rule datum the renderer draws: a rule's `y`
    datum when it has one, else its `x` datum (the renderer ignores the other)."""
    out = []
    for s in spec_layers(spec):
        enc = s["encoding"]
        if mark_of(s)[0] == "rule":
            for c in ("y", "x"):
                if "datum" in enc.get(c, {}):
                    out.append((c, enc[c]["datum"]))
                    break
    return out


class _Wire:
    """The answer's columns, built and decoded only when a datum needs them:
    a datum on a time or number axis of a chart with no grid needs no data."""

    def __init__(self, build: Callable[[], Mapping[str, Any]]) -> None:
        self._build = build
        self._cols: list[Mapping[str, Any]] | None = None

    def _layers(self) -> list[Mapping[str, Any]]:
        if self._cols is None:
            self._cols = [layer["columns"] for layer in self._build()["layers"]]
        return self._cols

    def column(self, layer: int, field: str) -> list[Any]:
        return decode_column(self._layers()[layer][field])

    def distinct(self, field: str) -> set[str]:
        """The field's values over every layer that sends it, as marking text."""
        seen: dict[tuple[str, Any], Any] = {}
        for cols in self._layers():
            if field in cols:
                for v in decode_distinct(cols[field]):
                    seen[_key(v)] = v
        return {s for s in (canon(v) for v in seen.values()) if s is not None}


def datum_errors(spec: Mapping[str, Any], answer: Callable[[], Mapping[str, Any]]) -> list[str]:
    """One line per rule `datum` the chart could not place, naming why.
    `answer` builds the query's answer; it is called only when a datum needs it."""
    drawn = drawn_datums(spec)
    if not drawn:
        return []
    specs = spec_layers(spec)
    wire = _Wire(answer)
    grid = next((i for i, s in enumerate(specs) if mark_of(s)[0] == "grid"), None)
    errors = []
    for c, datum in drawn:
        why = _unplaced(datum, c, specs, wire, grid)
        if why:
            errors.append(f"{c}: datum {datum!r} {why}")
    return errors


def _unplaced(
    datum: Any,
    c: str,
    specs: list[Mapping[str, Any]],
    wire: _Wire,
    grid: int | None,
) -> str | None:
    if grid is not None:
        channel = specs[grid]["encoding"][c]
    else:
        named = (s["encoding"][c] for s in specs if "field" in s["encoding"].get(c, {}))
        channel = next(named, None)
    if channel is None:
        return "has no axis to sit on — no layer draws a field there"
    kind = channel.get("type")
    if kind == "temporal" and isinstance(datum, str):
        ms = instant_ms(datum)
        if ms is None:
            return (
                "is not a date the chart can place — write it as 2024-03-01, "
                "2024-03-01T12:00, or with a zone as 2024-03-01T12:00:00+08:00"
            )
        datum = ms
    if kind == "quantitative" and isinstance(datum, str):
        return "is text on a number axis — write a number"
    if _number(datum) and not math.isfinite(datum):
        return "is not a finite number"
    if grid is not None:
        encoding = specs[grid]["encoding"]
        x = wire.column(grid, encoding["x"]["field"])
        y = wire.column(grid, encoding["y"]["field"])
        return _off_lattice(datum, x, y, c)
    if kind in ("nominal", "ordinal"):
        shown = wire.distinct(channel["field"])
        if canon(datum) in shown:
            return None
        listed = sorted(s for s in shown if s is not None)[:5]
        return f"is not a value the axis shows ({', '.join(listed)})"
    # A temporal channel is a time axis whatever its scale says.
    log = kind == "quantitative" and channel.get("scale", {}).get("type") == "log"
    if log and datum <= 0:
        return "is not above 0, and a log axis holds nothing else"
    return None


def _key(v: Any) -> tuple[str, Any]:
    """Equality as a JavaScript Set sees it: 1 and 1.0 are one, "1" and true apart."""
    return ("n", float(v)) if _number(v) else ("b" if isinstance(v, bool) else "s", v)


def _lattice_labels(values: list[Any]) -> list[Any]:
    """web/src/raster.ts `axis()`: a lattice axis's cells, in order."""
    first: dict[tuple[str, Any], Any] = {}
    for v in values:  # a Set keeps each value's first place
        first.setdefault(_key(v), v)
    unique = list(first.values())
    if unique and all(_number(v) for v in unique):
        if all(float(v).is_integer() for v in unique):
            lo, hi = int(min(unique)), int(max(unique))
            if hi - lo + 1 <= _FILL_LIMIT * len(unique):
                return list(range(lo, hi + 1))
        return sorted(unique)
    return sorted(unique, key=lambda v: canon(v) or "")


def _off_lattice(datum: Any, x: list[Any], y: list[Any], c: str) -> str | None:
    """Why the renderer's grid axis finds no position for `datum`, or None."""
    keep = [i for i in range(len(x)) if x[i] is not None and y[i] is not None]
    labels = _lattice_labels([(x if c == "x" else y)[i] for i in keep])
    cell = {canon(v): i for i, v in enumerate(labels)}
    if canon(datum) in cell:
        return None
    if _number(datum):
        for a, b in zip(labels, labels[1:], strict=False):
            if _number(a) and _number(b) and (a - datum) * (b - datum) < 0:
                return None
    return "is not a cell of the grid, nor between two"
