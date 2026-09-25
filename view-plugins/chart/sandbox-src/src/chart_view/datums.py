"""Where a rule's `datum` sits, decided the way the renderer decides it.

A datum never visits the data: the renderer places it on whatever axis the
chart drew, and a datum that axis cannot show used to draw nothing — or, as
`null`, break the whole chart. `datum_errors` refuses those in validate and in
query alike. The renderer's own choice is the oracle: wire-corpus/datum-axes.json
is written from this module and `datum-axes.test.ts` holds the renderer to it.

The axis is the grid's when a layer is a grid (its cells are the axis),
otherwise the first layer's channel with a field. On it a datum is:
- temporal: a number (epoch ms) or a date `instant_ms` reads;
- quantitative: a number (never text), above 0 on a log scale;
- nominal / ordinal: a value some layer's rows hold, compared as text;
- a grid's: besides the above, a cell — a label compared as text, a date's
  cell on a temporal grid, or a number between the first and last of
  all-numeric cells.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from chart_view.query import LayerRows
from chart_view.spec import spec_schema
from chart_view.wire import canon, epoch_ms

_INSTANT = re.compile(spec_schema()["$defs"]["instant"]["pattern"])

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


def datum_errors(layers: Sequence[LayerRows]) -> list[str]:
    """One line per `datum` the chart could not place, naming what would fit."""
    grid = next((ly for ly in layers if ly.mark == "grid"), None)
    errors = []
    for c in ("x", "y"):
        if grid is not None:
            axis = grid.encoding[c]
        else:
            axis = next(
                (ly.encoding[c] for ly in layers if "field" in ly.encoding.get(c, {})), None
            )
        for ly in layers:
            datum = ly.encoding.get(c, {}).get("datum")
            if datum is None:
                continue
            why = _unplaced(datum, axis, grid, layers)
            if why:
                errors.append(f"{c}: datum {datum!r} {why}")
    return errors


def _unplaced(
    datum: Any, axis: Mapping[str, Any] | None, grid: LayerRows | None, layers: Sequence[LayerRows]
) -> str | None:
    if axis is None:
        return "has no axis to sit on — no layer draws a field there"
    kind = axis.get("type")
    temporal = kind == "temporal"
    if temporal and isinstance(datum, str):
        datum = instant_ms(datum)
        if datum is None:
            return (
                "is not a date the chart can place — write it as 2024-03-01, "
                "2024-03-01T12:00, or with a zone as 2024-03-01T12:00:00+08:00"
            )
    if kind == "quantitative" and not isinstance(datum, int | float):
        return "is text on a number axis — write a number"
    if grid is not None:
        return _off_lattice(datum, grid.rows[axis["field"]], temporal)
    if temporal:
        return None
    if kind == "quantitative":
        if axis.get("scale", {}).get("type") == "log" and datum <= 0:
            return "is not above 0, and a log axis holds nothing else"
        return None
    field = axis["field"]
    marks = (canon(v) for ly in layers if field in ly.kinds for v in ly.rows[field])
    shown = {s for s in marks if s is not None}
    if canon(datum) in shown:
        return None
    return f"is not a value the axis shows ({', '.join(sorted(shown)[:5])})"


def _off_lattice(datum: Any, values: pd.Series, temporal: bool) -> str | None:
    """Why the renderer's grid axis finds no position for `datum`, or None."""
    cells = pd.Series(epoch_ms(values)) if temporal else values
    labels = [v for v in cells.dropna().unique() if not (isinstance(v, float) and math.isinf(v))]
    numbers = [float(v) for v in labels if isinstance(v, int | float | np.integer | np.floating)]
    all_numbers = len(numbers) == len(labels) and bool(labels)
    shown = {canon(v) for v in labels}
    if all_numbers and all(n.is_integer() for n in numbers):
        lo, hi = int(min(numbers)), int(max(numbers))
        if hi - lo + 1 <= _FILL_LIMIT * len(numbers):  # the lattice fills the gaps
            shown = {str(n) for n in range(lo, hi + 1)}
    if canon(datum) in shown:
        return None
    if all_numbers and isinstance(datum, int | float) and min(numbers) < datum < max(numbers):
        return None
    return "is not a cell of the grid, nor between two"
