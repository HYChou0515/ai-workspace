"""Write `wire-corpus/datum-axes.json`: rule datums over layered charts, and
whether validate lets each one through (`validate.check`, the oracle).

Which axis a datum lands on — the first layer's channel with a field, a
grid's cells, a category's labels — is decided once in the sandbox and once
in the renderer. `datum-axes.test.ts` holds the renderer to this file: a datum
validate accepts gets a numeric position, one it refuses none;
`test_wire.py` checks the file is current. Rerun after changing either:

    uv run python scripts/write_datum_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from chart_view.validate import check

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "datum-axes.json"

# One frame every case reads: `t` holds dates, `v` numbers, `g` labels, `n`
# year-like integers used as labels, `p` positive numbers for a log axis, `k`
# integer labels with a gap the grid's lattice fills.
DATA = {
    "t": ["2024-03-01", "2024-03-02"],
    "v": [1.0, 2.0],
    "g": ["a", "b"],
    "n": [2022, 2023],
    "p": [1.0, 10.0],
    "k": [1, 3],
}

T = {"field": "t", "type": "temporal"}
V = {"field": "v", "type": "quantitative"}
G = {"field": "g", "type": "nominal"}
GO = {"field": "g", "type": "ordinal"}
NO = {"field": "n", "type": "ordinal"}
PLOG = {"field": "p", "type": "quantitative", "scale": {"type": "log"}}


def rule(channel: str, datum: Any) -> dict[str, Any]:
    return {"mark": "rule", "encoding": {channel: {"datum": datum}}}


LINE_T = {"mark": "line", "encoding": {"x": T, "y": V}}
LINE_YT = {"mark": "line", "encoding": {"x": V, "y": T}}
SCATTER_V = {"mark": "scatter", "encoding": {"x": V, "y": V}}
BAR_G = {"mark": "bar", "encoding": {"x": G, "y": V}}
GRID_T = {"mark": "grid", "encoding": {"x": T, "y": GO, "color": V}}
GRID_G = {"mark": "grid", "encoding": {"x": GO, "y": V, "color": V}}
GRID_V = {"mark": "grid", "encoding": {"x": V, "y": G, "color": V}}
BAR_N = {"mark": "bar", "encoding": {"x": NO, "y": V}}
SCATTER_LOG = {"mark": "scatter", "encoding": {"x": PLOG, "y": V}}
GRID_K = {
    "mark": "grid",
    "encoding": {"x": {"field": "k", "type": "ordinal"}, "y": G, "color": V},
}

CASES: list[tuple[str, list[dict[str, Any]]]] = [
    ("date on a time axis", [LINE_T, rule("x", "2024-03-01T12:00")]),
    ("zoned date on a time axis", [LINE_T, rule("x", "2024/03/01T12:00+08:00")]),
    ("ms on a time axis", [LINE_T, rule("x", 1709294400000)]),
    ("loose date on a time axis", [LINE_T, rule("x", "2024-3-1")]),
    ("digits as text on a time axis", [LINE_T, rule("x", "2024")]),
    ("no such day on a time axis", [LINE_T, rule("x", "2024-02-30")]),
    ("the rule layer first", [rule("x", "2024-3-1"), LINE_T]),
    ("the rule layer first, a good date", [rule("x", "2024-03-02"), LINE_T]),
    ("a time y axis", [LINE_YT, rule("y", "2024-03-02")]),
    ("a loose date on a time y axis", [LINE_YT, rule("y", "2024/3/2")]),
    ("the first layer with a field decides", [SCATTER_V, LINE_T, rule("x", "2024-03-01")]),
    ("a number on a number axis", [SCATTER_V, rule("x", 1.5)]),
    ("text on a number axis", [SCATTER_V, rule("x", "1.5")]),
    ("a label on a category axis", [BAR_G, rule("x", "b")]),
    ("a date on a temporal grid", [GRID_T, rule("x", "2024-03-02")]),
    ("a loose date on a temporal grid", [GRID_T, rule("x", "2024-3-2")]),
    ("a label on an ordinal grid", [GRID_G, rule("x", "b")]),
    # Review round 5: values an axis does not show.
    ("a label the category axis lacks", [BAR_G, rule("x", "zzz")]),
    ("a number on a text category axis", [BAR_G, rule("x", 1)]),
    ("a number label on a number-label axis", [BAR_N, rule("x", 2022)]),
    ("its text on a number-label axis", [BAR_N, rule("x", "2022")]),
    ("a label written otherwise", [BAR_N, rule("x", "2022.0")]),
    ("a number the labels lack", [BAR_N, rule("x", 1999)]),
    ("a cell's label as text on a number grid", [GRID_V, rule("x", "1")]),
    ("between two number cells", [GRID_V, rule("x", 1.5)]),
    ("text that is no cell of a number grid", [GRID_V, rule("x", "abc")]),
    ("a cell written otherwise", [GRID_V, rule("x", "1.0")]),
    ("a number past the cells", [GRID_V, rule("x", 99)]),
    ("a date past a temporal grid's cells", [GRID_T, rule("x", "2030-01-01")]),
    ("a label a grid's row axis lacks", [GRID_V, rule("y", "zzz")]),
    ("a label on a grid's row axis", [GRID_V, rule("y", "b")]),
    ("a date whose field comes later", [rule("x", "2024-3-1"), BAR_G, LINE_T]),
    ("a grid second: its cells decide", [BAR_G, GRID_T, rule("x", "2024-03-02")]),
    ("a label of a grid that comes second", [LINE_T, GRID_G, rule("x", "a")]),
    ("a positive number on a log axis", [SCATTER_LOG, rule("x", 5)]),
    ("zero on a log axis", [SCATTER_LOG, rule("x", 0)]),
    ("a negative number on a log axis", [SCATTER_LOG, rule("x", -1)]),
    ("a cell the lattice fills", [GRID_K, rule("x", "2")]),
    ("a label past a filled lattice", [GRID_K, rule("x", "5")]),
    ("text on a number grid", [GRID_V, rule("x", "1.5")]),
    ("text with no axis", [rule("x", "abc")]),
    ("a date with no axis", [rule("x", "2024-03-01")]),
]


def main() -> None:
    frame = pd.DataFrame(DATA)
    cases = []
    for name, layers in CASES:
        spec = {"view": "chart", "source": "a.csv", "layer": layers}
        errors = check(json.dumps(spec), lambda _s: frame).errors
        cases.append({"name": name, "layer": layers, "placed": not errors})
    doc = {"kind": "datum-axes", "data": DATA, "cases": cases}
    CORPUS.write_text(json.dumps(doc, indent=2) + "\n")
    print(len(cases), sum(c["placed"] for c in cases))


if __name__ == "__main__":
    main()
