"""Write `wire-corpus/bar-partials.json`: aggregated bars split by a
marking's keys (#861 D3) -- each case's data, spec, the marking's keys (`by`)
and picks, the `partials` answer, and the ORACLE: per layer, per drawn bar,
the sandbox's own `aggregate` (the op the bar is drawn with) over the bar's
picked rows only -- null for a bar with none picked, and for a layer that
is not split.

The browser folds the partials of the picked tuples on every marking change
(`partials.ts`); `partials.corpus.test.ts` holds that fold to the oracle,
and `test_partials.py` checks the file is current and folds it in Python
too. One oracle, so the two halves cannot drift apart by hand. Rerun after
changing either:

    uv run python scripts/write_partials_corpus.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from chart_view.partials import partials
from chart_view.query import layer_rows
from chart_view.spec import parse_spec
from chart_view.transforms import aggregate
from chart_view.wire import canon, unhashable_as_text

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "bar-partials.json"

GROUP = {"field": "group", "type": "nominal"}
ITEM = {"field": "item", "type": "nominal"}

ROWS = {
    "group": ["g1", "g1", "g1", "g1", "g2", "g2", "g2", "g3", "g3", "g3"],
    "item": ["1", "2", "2", "3", "1", "1", "2", "1", None, "2"],
    "value": [2.5, 4.0, 10.0, 1.25, 5.0, -7.5, 3.0, None, 6.0, 0.5],
    "ok": [1, 0, 1, None, 1, 1, 0, 0, 1, None],
    "region": ["n", "s", "n", "s", "n", "s", "s", "n", "n", "s"],
}
PICKS = [["g1", "2"], ["g2", "1"], ["g3", "1"], ["g9", "9"]]


def _bar(op: str) -> dict[str, Any]:
    return {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": op}}


CASES: list[tuple[str, Any, dict[str, Any], list[str], list[list[str]]]] = [
    (f"a {op} bar by group, keys group and item", "bar", _bar(op), ["group", "item"], PICKS)
    for op in ("count", "sum", "mean", "min", "max")
] + [
    (
        "a rate bar by group, keys group and item",
        "bar",
        {"x": GROUP, "y": {"field": "ok", "type": "quantitative", "aggregate": "rate"}},
        ["group", "item"],
        PICKS,
    ),
    (
        "a horizontal mean bar coloured by region",
        "bar",
        {
            "y": GROUP,
            "x": {"field": "value", "type": "quantitative", "aggregate": "mean"},
            "color": {"field": "region", "type": "nominal"},
        },
        ["group", "item"],
        PICKS,
    ),
    (
        "a stacked bar of rows, segments split by region",
        {"type": "bar", "stack": True},
        {"x": GROUP, "y": {"field": "value", "type": "quantitative"}, "color": ITEM},
        ["region"],
        [["s"]],
    ),
    (
        "a bar whose rows lack a key is not split",
        "bar",
        _bar("sum"),
        ["group", "unit"],
        [["g1", "a"]],
    ),
]


def frame_of(data: dict[str, list[Any]]) -> pd.DataFrame:
    return pd.DataFrame(data)


def _plain(x: Any) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def oracle(layer: Any, by: list[str], picks: list[list[str]]) -> list[float | None] | None:
    """Per drawn bar, `aggregate` of its op over its picked rows alone."""
    src = layer.bar
    if src is None:
        return None
    frame = src.frame
    if src.groups:
        keyed = frame.assign(**{g: unhashable_as_text(frame[g]) for g in src.groups})
        bars = keyed.groupby(src.groups, dropna=False, sort=True, observed=True).ngroup()
    else:
        bars = pd.Series(0, index=frame.index)
    if not set(by) <= set(frame.columns):
        return None
    wanted = {tuple(p) for p in picks}
    picked = frame.apply(lambda r: tuple(canon(r[k]) for k in by) in wanted, axis=1)
    out: list[float | None] = []
    for b in range(int(bars.max()) + 1 if len(bars) else 0):
        rows = frame[(bars == b) & picked]
        if rows.empty:
            out.append(None)
            continue
        [value] = aggregate(rows, [{"op": src.op, "field": src.value, "as": "$v"}], [])["$v"]
        out.append(_plain(value))
    return out


def case(name: str, mark: Any, encoding: dict[str, Any], by: list[str], picks: list[list[str]]):
    spec: dict[str, Any] = {"view": "chart", "source": "a.csv", "keys": by}
    spec |= {"mark": mark, "encoding": encoding}
    frame = frame_of(ROWS)
    layers = layer_rows(parse_spec(json.dumps(spec)), frame)
    answer = partials(layers, by)
    return {
        "name": name,
        "data": ROWS,
        "spec": spec,
        "by": by,
        "picks": picks,
        "partials": answer,
        # a layer the answer does not split has no oracle to hold it to
        "oracle": [
            oracle(ly, by, picks) if p is not None else None
            for ly, p in zip(layers, answer["layers"], strict=True)
        ],
    }


def cases() -> list[dict[str, Any]]:
    return [case(*c) for c in CASES]


def main() -> None:
    doc = {"kind": "bar-partials", "cases": cases()}
    CORPUS.write_text(json.dumps(doc, indent=1) + "\n")
    print(len(doc["cases"]))


if __name__ == "__main__":
    main()
