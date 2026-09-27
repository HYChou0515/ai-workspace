"""`partials`: each aggregated bar split by a marking's keys (#861 D3).

A marking lights an aggregated bar in part: the bar keeps its full length,
dimmed, and in front of it a lit bar whose value is the same aggregate over
the picked rows only. The browser draws that on every marking change without
asking again: this answers, per drawn bar, the decomposable partials of its
rows grouped by the marking's keys -- each a key tuple (as marking text,
`canon`) and its rows' ``n``, ``sum``, ``min`` and ``max`` -- and the browser
folds the lit ones (`partials.ts:litValue`). It is asked again only when the
marking's KEYS change, never its values.

    {"format": 1, "layers": [layer, ...]}      # one per layer, as `query`'s

    layer = None                               # not split: lit whole (D4)
          | {"whole": reason}                  # could be split, is not: lit whole
          | {"by": [key, ...], "op": op,
             "keys": [[text, ...], ...],       # the distinct key tuples, in `by` order
             "bars": [[[k, n, sum, min, max], ...], ...]}   # per drawn row

``n`` is what the op counts over: the field's non-missing values for
``count``; its non-missing numbers for ``sum`` / ``mean`` / ``min`` /
``max``; every row for ``rate`` (whose ``sum`` is the rows that passed). A
bar's value over the picked rows is then ``count`` Σn, ``sum`` Σsum,
``mean`` Σsum / Σn, ``rate`` Σsum / Σn, ``min`` / ``max`` of the picked
ones' (``min`` / ``max`` null where ``n`` is 0).

A layer is None when it is no aggregated bar, when its rows lack one of
the keys (a partial keyed on fewer would light rows that were not picked),
or when every bar is one key tuple already (lit whole is then exact). A
row missing a key's value can never be picked, and is in no partial.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from chart_view.query import LayerRows
from chart_view.wire import canon, unhashable_as_text

#: Past this many (bar, key tuple) entries a layer is lit whole: the answer
#: would be too big to fold on every marking change.
MAX_ENTRIES = 200_000

#: The ops whose value over some rows folds from per-tuple partials.
FOLDS = frozenset({"count", "sum", "mean", "min", "max", "rate"})


def _number(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def layer_partials(layer: LayerRows, by: Sequence[str]) -> dict[str, Any] | None:
    """`layer`'s bars split by the key columns `by` (see the module doc)."""
    src = layer.bar
    if src is None or not by or not set(by) <= set(src.frame.columns):
        return None
    if set(by) <= set(src.groups):
        return None
    if src.op not in FOLDS:
        return {"whole": f"a bar of a {src.op} lights whole: it cannot be split by the marking"}
    frame = src.frame
    # the drawn row each source row is in: `aggregate`'s own grouping
    if src.groups:
        keyed = frame.assign(**{g: unhashable_as_text(frame[g]) for g in src.groups})
        bar = keyed.groupby(src.groups, dropna=False, sort=True, observed=True).ngroup()
    else:
        bar = pd.Series(0, index=frame.index)
    texts = pd.DataFrame({k: frame[k].map(canon) for k in by}, index=frame.index)
    named = texts.notna().to_numpy().all(axis=1)
    raw = frame[src.value]
    numbers = pd.to_numeric(raw, errors="coerce")
    if src.op == "count":
        n, v = raw.notna().astype(float), pd.Series(np.nan, index=frame.index)
    elif src.op == "rate":
        n = pd.Series(1.0, index=frame.index)
        v = raw.map(lambda x: float(bool(x)) if not pd.isna(x) else 0.0)
    else:
        n, v = numbers.notna().astype(float), numbers.astype(float)
    parts = pd.DataFrame(
        {"bar": bar.to_numpy(), "n": n.to_numpy(), "v": v.to_numpy()}, index=frame.index
    )
    parts = pd.concat([parts, texts], axis=1)[named]
    tuples = parts[list(by)].drop_duplicates()
    grouped = parts.groupby(["bar", *by], sort=True, observed=True)
    if grouped.ngroups > MAX_ENTRIES:
        return {"whole": f"lit whole: split by the marking it would be over {MAX_ENTRIES:,} parts"}
    agg = grouped.agg(n=("n", "sum"), sum=("v", "sum"), min=("v", "min"), max=("v", "max"))
    keys = [list(t) for t in sorted(tuples.itertuples(index=False, name=None))]
    index = {tuple(k): i for i, k in enumerate(keys)}
    drawn = int(bar.max()) + 1 if len(bar) else 0
    bars: list[list[list[Any]]] = [[] for _ in range(drawn)]
    for (b, *key), row in zip(agg.index, agg.itertuples(index=False), strict=True):
        count = float(row.n)
        empty = count == 0
        bars[int(b)].append(
            [
                index[tuple(key)],
                count,
                0.0 if src.op == "count" else float(row.sum),
                None if empty or src.op in ("count", "rate") else _number(row.min),
                None if empty or src.op in ("count", "rate") else _number(row.max),
            ]
        )
    return {"by": list(by), "op": src.op, "keys": keys, "bars": bars}


def partials(layers: list[LayerRows], by: Sequence[str]) -> dict[str, Any]:
    """Every layer's partials (None for a layer not split)."""
    return {"format": 1, "layers": [layer_partials(ly, by) for ly in layers]}
