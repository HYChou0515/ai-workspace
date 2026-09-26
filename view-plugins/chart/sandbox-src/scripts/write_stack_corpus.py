"""Write `wire-corpus/stack-sums.json`: stacked layers of raw rows -- each
case's data, spec, the query's real answer (`query.build`, which sums a stack
per slot and colour, #847/#848 PR 5 P40 row 18) and pandas' own stack tops.

`tops[k][j]` is where colour k's stack ends at slot j (colours and slots in
sorted order, a slot where a colour has no row adding 0): pandas' per slot
and colour sums, cumulated over the colours. `echarts.stacksandbox.test.ts`
draws each stored answer with real ECharts and holds its stack tops to
these; `test_stack_sum.py` checks the file is current (answers and tops).
Rerun after changing either:

    uv run python scripts/write_stack_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from chart_view.query import build
from chart_view.spec import parse_spec

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "stack-sums.json"

ITEM = {"field": "item", "type": "nominal"}
GROUP = {"field": "group", "type": "nominal"}
VALUE = {"field": "value", "type": "quantitative"}
BAR = {"type": "bar", "stack": True}
AREA = {"type": "area", "stack": True}

ROWS = {
    "item": list("ppqpqqqr"),
    "group": list("aaabbbab"),
    "value": [3, 4, 7, 5, 1, 2, 6, 8],
    "region": ["n", "n", "s", "n", "s", "s", "n", "s"],
}

# 1,000 rows in one item, a and b alternating, and one row in each other
_CROWD = 1000
CROWDED = {
    "item": ["p"] * _CROWD + ["q", "r"],
    "group": [("a" if i % 2 else "b") for i in range(_CROWD + 2)],
    "value": [1 + i % 7 for i in range(_CROWD + 2)],
}

NUMBER_X = {
    "at": [1, 1, 1, 2, 2, 3, 3],
    "group": ["a", "a", "b", "a", "b", "a", "b"],
    "value": [2, 5, None, None, None, 4, 1],
}

TIME_X = {
    "day": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02", "2024-01-03"],
    "group": ["a", "a", "a", "b", "b"],
    "value": [1.5, 2.5, 3, 4, 5],
}

# a has no row at q, and two at p and at s (echarts.catgaps.test.ts)
GAPS = {
    "item": list("prspspqrs"),
    "group": list("aaaaabbbb"),
    "value": [1, 3, 4, 5, 6, 1, 1, 1, 1],
}
GAPS_LOG = {**GAPS, "value": [2, 3, 4, 5, 6, 1, 1, 1, 1]}
# two rows of a at x=1, and a row of a with no x (echarts.stack.test.ts)
TWO_AT_ONE_X = {
    "at": [1, None, 1, 1, 2],
    "group": ["a", "a", "a", "b", "a"],
    "value": [1, 5, 2, 10, 3],
}
LOG = {"field": "value", "type": "quantitative", "scale": {"type": "log"}}
AT = {"field": "at", "type": "quantitative"}

CASES: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = [
    ("an upright bar of rows", BAR, {"x": ITEM, "y": VALUE, "color": GROUP}, ROWS),
    ("a horizontal bar of rows", BAR, {"x": VALUE, "y": ITEM, "color": GROUP}, ROWS),
    ("an area of rows on a category x", AREA, {"x": ITEM, "y": VALUE, "color": GROUP}, ROWS),
    ("a bar of rows with no colour", BAR, {"x": ITEM, "y": VALUE}, ROWS),
    (
        "a bar with a tooltip its rows share only in part",
        BAR,
        {"x": ITEM, "y": VALUE, "color": GROUP, "tooltip": {"field": "region", "type": "nominal"}},
        ROWS,
    ),
    ("a crowded item", BAR, {"x": ITEM, "y": VALUE, "color": GROUP}, CROWDED),
    (
        "an area on a number x, a slot of missing values",
        AREA,
        {"x": {"field": "at", "type": "quantitative"}, "y": VALUE, "color": GROUP},
        NUMBER_X,
    ),
    (
        "a bar on a time x",
        BAR,
        {"x": {"field": "day", "type": "temporal"}, "y": VALUE, "color": GROUP},
        TIME_X,
    ),
    ("an area with a gap at a category", AREA, {"x": ITEM, "y": VALUE, "color": GROUP}, GAPS),
    ("a bar with a gap at a category", BAR, {"x": ITEM, "y": VALUE, "color": GROUP}, GAPS),
    (
        "a horizontal bar with a gap at a category",
        BAR,
        {"x": VALUE, "y": ITEM, "color": GROUP},
        GAPS,
    ),
    (
        "an area with a gap at a category, on a log y",
        AREA,
        {"x": ITEM, "y": LOG, "color": GROUP},
        GAPS_LOG,
    ),
    (
        "an area with two rows of one group at one x, and a row with no x",
        AREA,
        {"x": AT, "y": VALUE, "color": GROUP},
        TWO_AT_ONE_X,
    ),
    # (#847/#848 PR 5 P41 row 28) an ordinal y is a slot too, as a nominal one is
    (
        "a horizontal bar on an ordinal y",
        BAR,
        {"x": VALUE, "y": {"field": "item", "type": "ordinal"}, "color": GROUP},
        ROWS,
    ),
]


def tops(frame: pd.DataFrame, encoding: dict[str, Any]) -> list[list[float]] | None:
    """pandas' stack tops: per colour (sorted), per slot (sorted, a missing
    one last). None on a log axis, where a slot with nothing beneath is
    empty rather than 0."""
    horizontal = encoding["y"]["type"] in ("nominal", "ordinal")
    value = encoding["x" if horizontal else "y"]
    if value.get("scale", {}).get("type") == "log":
        return None
    slot = encoding["y" if horizontal else "x"]["field"]
    colour = encoding.get("color", {}).get("field")
    df = frame.assign(**({} if colour else {"$one": "all"}))
    grouped = df.groupby([colour or "$one", slot], dropna=False)["value"].sum()
    sums = grouped.unstack(fill_value=0).sort_index().sort_index(axis=1, na_position="last")
    return [[float(v) for v in row] for row in sums.cumsum().to_numpy()]


def frame_of(data: dict[str, list[Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(data)
    if "day" in frame:
        frame["day"] = pd.to_datetime(frame["day"])
    return frame


def case(name: str, mark: dict[str, Any], encoding: dict[str, Any], data: dict[str, Any]):
    spec: dict[str, Any] = {"view": "chart", "source": "a.csv", "keys": ["item", "group", "region"]}
    spec |= {"mark": mark, "encoding": encoding}
    frame = frame_of(data)
    answer = build(parse_spec(json.dumps(spec)), frame)
    return {
        "name": name,
        "data": data,
        "spec": spec,
        "answer": answer,
        "tops": tops(frame, encoding),
    }


def cases() -> list[dict[str, Any]]:
    return [case(*c) for c in CASES]


def main() -> None:
    doc = {"kind": "stack-sums", "cases": cases()}
    CORPUS.write_text(json.dumps(doc, indent=1) + "\n")
    print(len(doc["cases"]))


if __name__ == "__main__":
    main()
