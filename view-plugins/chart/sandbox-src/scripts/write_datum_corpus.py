"""Write `wire-corpus/datum-axes.json`: rule datums over layered charts — each
case's data, spec, the query's real answer (`query.build`), and whether
validate lets the datum through (`validate.check`, the oracle).

Which axis a datum lands on — a grid's channels when any layer is a grid,
otherwise the first layer's channel with a field — and what that axis holds
are decided once in the sandbox and once in the renderer.
`datum-axes.test.ts` feeds each stored answer to the renderer and holds it
to this file: a datum validate accepts gets a numeric position, one it
refuses none; `test_wire.py` checks the file is current
(verdicts and answers). Rerun after changing either:

    uv run python scripts/write_datum_corpus.py
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from chart_view.datums import datum_instant, instant_ms
from chart_view.query import build
from chart_view.spec import parse_spec
from chart_view.validate import check
from chart_view.wire import zone_of

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "datum-axes.json"
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

# The frame the CASES below read (the OWN cases carry their own): `t` holds
# dates, `v` numbers, `g` labels, `n` year-like integers used as labels, `p`
# positive numbers for a log axis, `k` integer labels with a gap the grid's
# lattice fills.
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


def grid(x: dict[str, Any], y: dict[str, Any], color: str = "v") -> dict[str, Any]:
    return {
        "mark": "grid",
        "encoding": {"x": x, "y": y, "color": {"field": color, "type": "quantitative"}},
    }


def f(name: str, kind: str) -> dict[str, str]:
    return {"field": name, "type": kind}


# Review round 6: cases with their own data, where the rows pandas holds and
# the wire the renderer reads differ — binned, coerced, dropped by the lattice.
OWN: list[tuple[str, list[dict[str, Any]], dict[str, list[Any]], dict[str, Any]]] = [
    (
        "a cell whose other coordinate is missing",
        [grid(f("k", "ordinal"), f("g", "nominal")), rule("x", 9)],
        {"k": [1, 2, 9], "g": ["a", "b", None], "v": [1.0, 2.0, 3.0]},
        {},
    ),
    (
        "a cell whose other coordinate is there",
        [grid(f("k", "ordinal"), f("g", "nominal")), rule("x", 2)],
        {"k": [1, 2, 9], "g": ["a", "b", None], "v": [1.0, 2.0, 3.0]},
        {},
    ),
    # The lattice fills an integer axis's gaps while its span is at most
    # FILL_LIMIT (4) times its values: 1..8 over two values fills, 1..9 not.
    (
        "a gap the lattice fills at its limit",
        [grid(f("k", "ordinal"), f("g", "nominal")), rule("x", "4")],
        {"k": [1, 8], "g": ["a", "b"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "a gap one past the lattice's limit",
        [grid(f("k", "ordinal"), f("g", "nominal")), rule("x", "4")],
        {"k": [1, 9], "g": ["a", "b"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "between two fractional cells",
        [grid(f("k", "quantitative"), f("g", "nominal")), rule("x", 1.0)],
        {"k": [0.5, 1.5], "g": ["a", "b"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "a label another layer reads as a number",
        [
            {"mark": "bar", "encoding": {"x": f("g", "nominal"), "y": V}},
            {"mark": "scatter", "encoding": {"x": f("g", "quantitative"), "y": V}},
            rule("x", "1"),
        ],
        {"g": ["01", "a"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "a label only another layer's dates send",
        [
            {
                "mark": "bar",
                "encoding": {"x": f("t", "nominal"), "y": V},
                "transform": [{"filter": "t == '2024-03-01'"}],
            },
            LINE_T,
            rule("x", "2024-03-02"),
        ],
        {"t": ["2024-03-01", "2024-03-02"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "a value binning moved",
        [
            {"mark": "bar", "encoding": {"x": f("w", "nominal"), "y": V}},
            {"mark": "scatter", "encoding": {"x": f("w", "quantitative"), "y": V}},
            rule("x", 5),
        ],
        {"w": list(range(30)), "v": [float(i) for i in range(30)]},
        {"bin_threshold": 5},
    ),
    (
        "a value only binning moved away",
        [
            {
                "mark": "bar",
                "encoding": {"x": f("w", "nominal"), "y": V},
                "transform": [{"filter": "w != 5"}],
            },
            {"mark": "scatter", "encoding": {"x": f("w", "quantitative"), "y": V}},
            rule("x", 5),
        ],
        {"w": list(range(30)), "v": [float(i) for i in range(30)]},
        {"bin_threshold": 5},
    ),
    # Review round 7: a JavaScript Set keeps each value's FIRST place; the
    # string sort ties 1 and "1", so that order decides which cells are next
    # to each other, and so whether 1.5 falls between two numbers.
    (
        "a number between cells, 1 first",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", 1.5)],
        {"k": [1, "1", 1, 2], "g": ["a", "b", "c", "d"], "v": [1.0, 2.0, 3.0, 4.0]},
        {},
    ),
    (
        "a number between cells, text 1 first",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", 1.5)],
        {"k": ["1", 1, "1", 2], "g": ["a", "b", "c", "d"], "v": [1.0, 2.0, 3.0, 4.0]},
        {},
    ),
    (
        "a date before 1970 on a time axis asking for log",
        [
            {
                "mark": "line",
                "encoding": {"x": {**T, "scale": {"type": "log"}}, "y": V},
            },
            rule("x", "1969-06-01"),
        ],
        {"t": ["2024-03-01", "2024-03-02"], "v": [1.0, 2.0]},
        {},
    ),
    # Review round 7 (conformance): pins for choices each side makes alone.
    (
        "a rule's y datum is the one drawn",
        [
            LINE_T,
            {"mark": "rule", "encoding": {"x": {"datum": "2024-03-01"}, "y": {"datum": "zzz"}}},
        ],
        DATA,
        {},
    ),
    (
        "a rule's x datum is not drawn when it has a y",
        [LINE_T, {"mark": "rule", "encoding": {"x": {"datum": "zzz"}, "y": {"datum": 1.5}}}],
        DATA,
        {},
    ),
    ("a whole float on integer labels", [BAR_N, rule("x", 2022.0)], DATA, {}),
    (
        "a bin's centre on the axis binning made",
        [
            {"mark": "bar", "encoding": {"x": f("w", "nominal"), "y": V}},
            {"mark": "scatter", "encoding": {"x": f("w", "quantitative"), "y": V}},
            rule("x", 0.056640625),
        ],
        {"w": list(range(30)), "v": [float(i) for i in range(30)]},
        {"bin_threshold": 5},
    ),
    (
        "numbers a text label sorts apart",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", 9.5)],
        {"k": [10, "5", 9], "g": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]},
        {},
    ),
    (
        "between number cells whose text had a stray value",
        [grid(f("k", "quantitative"), f("g", "nominal")), rule("x", 1.5)],
        {"k": ["1", "2", "x"], "g": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]},
        {},
    ),
    (
        "a number cell written with a leading zero",
        [grid(f("k", "quantitative"), f("g", "nominal")), rule("x", 1)],
        {"k": ["01", "02"], "g": ["a", "b"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "a number on a true/false grid",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", 1)],
        {"k": [True, False, None], "g": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]},
        {},
    ),
    (
        "true on a true/false grid",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", "true")],
        {"k": [True, False, None], "g": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]},
        {},
    ),
    (
        "a list's item on a grid of lists",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", "a")],
        {"k": [["a"], ["b"]], "g": ["a", "b"], "v": [1.0, 2.0]},
        {},
    ),
    (
        "a list's marking on a grid of lists",
        [grid(f("k", "nominal"), f("g", "nominal")), rule("x", "['a']")],
        {"k": [["a"], ["b"]], "g": ["a", "b"], "v": [1.0, 2.0]},
        {},
    ),
]


# #847/#848 PR 5 P26: a zoned time axis (its column carries a zone). A
# zone-less date is a wall time on the axis's clock; one the zone had twice or
# never is refused. `data` holds instants (UTC), `zones` each zoned column's
# zone; a placed datum on the time axis stores `at`, where it sits on that
# axis: its wall time there, as epoch ms read as UTC.
NY = {"t": ["2026-03-07T05:00:00Z", "2026-11-02T05:00:00Z"], "v": [1.0, 2.0]}
TAIPEI_DAYS = {
    "t": ["2026-02-28T16:00:00Z", "2026-03-01T16:00:00Z"],
    "g": ["a", "a"],
    "v": [1.0, 2.0],
}
ZONED: list[tuple[str, list[dict[str, Any]], dict[str, list[Any]], dict[str, str]]] = [
    (
        "a zone-less date on a zoned axis",
        [LINE_T, rule("x", "2026-03-07T12:00")],
        NY,
        {"t": "America/New_York"},
    ),
    (
        "a zone-less date the zone skipped",
        [LINE_T, rule("x", "2026-03-08T02:30")],
        NY,
        {"t": "America/New_York"},
    ),
    (
        "a zone-less date just past the skip",
        [LINE_T, rule("x", "2026-03-08T03:00")],
        NY,
        {"t": "America/New_York"},
    ),
    (
        "a zone-less date the zone had twice",
        [LINE_T, rule("x", "2026-11-01T01:30")],
        NY,
        {"t": "America/New_York"},
    ),
    (
        "a zone-less date just past the repeat",
        [LINE_T, rule("x", "2026-11-01T02:00")],
        NY,
        {"t": "America/New_York"},
    ),
    (
        "a date with its offset on a zoned axis",
        [LINE_T, rule("x", "2026-11-01T01:30-05:00")],
        NY,
        {"t": "America/New_York"},
    ),
    ("epoch ms on a zoned axis", [LINE_T, rule("x", 1772884800000)], NY, {"t": "America/New_York"}),
    (
        "a zone-less date on a zoned y axis",
        [LINE_YT, rule("y", "2026-03-07T12:00")],
        NY,
        {"t": "America/New_York"},
    ),
    (
        "a zone-less date on a fixed-offset axis",
        [LINE_T, rule("x", "2026-03-07T12:00")],
        NY,
        {"t": "+05:45"},
    ),
    (
        "a zone-less date at the calendar's start, ahead of UTC",
        [LINE_T, rule("x", "0001-01-01")],
        NY,
        {"t": "+05:00"},
    ),
    (
        "a zone-less day on a zoned temporal grid",
        [GRID_T, rule("x", "2026-03-02")],
        TAIPEI_DAYS,
        {"t": "Asia/Taipei"},
    ),
    (
        "that day read as UTC is no cell",
        [GRID_T, rule("x", "2026-03-02T00:00Z")],
        TAIPEI_DAYS,
        {"t": "Asia/Taipei"},
    ),
    (
        "no such day on a zoned axis",
        [LINE_T, rule("x", "2026-02-30")],
        NY,
        {"t": "America/New_York"},
    ),
    ("a zone-less date on a UTC axis", [LINE_T, rule("x", "2026-03-07T12:00")], NY, {"t": "UTC"}),
]


def frame_of(data: dict[str, list[Any]], zones: dict[str, str]) -> pd.DataFrame:
    """A case's frame: each zoned column's instants shown in its zone."""
    frame = pd.DataFrame(data)
    for column, zone in zones.items():
        frame[column] = pd.to_datetime(frame[column], utc=True).dt.tz_convert(zone_of(zone))
    return frame


def wall_at(datum: Any, zone: str) -> float | None:
    """Where the sandbox reads a datum on a time axis showing `zone`, as the
    renderer draws it there: the instant's wall time, as epoch ms read as UTC."""
    ms = datum_instant(datum, zone)
    if isinstance(ms, str):
        return None
    try:
        at = _EPOCH + dt.timedelta(milliseconds=ms)
    except OverflowError:  # off the calendar at UTC: the text is the wall time
        return instant_ms(datum)
    offset = at.astimezone(zone_of(zone)).utcoffset()
    assert offset is not None
    return ms + offset / dt.timedelta(milliseconds=1)


def case(
    name: str,
    layers: list[dict[str, Any]],
    data: dict[str, list[Any]],
    extra: dict[str, Any],
    zones: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One corpus case: its verdict from validate, its answer from query."""
    spec = {"view": "chart", "source": "a.csv", **extra, "layer": layers}
    frame = frame_of(data, zones or {})
    text = json.dumps(spec)
    placed = not check(text, lambda _s: frame).errors
    answer = build(parse_spec(text), frame)
    out = {"name": name, "data": data, "spec": spec, "answer": answer, "placed": placed}
    if zones:
        out["zones"] = zones
        rule_layer = layers[-1]["encoding"]
        axis = "y" if "y" in rule_layer else "x"
        if placed and layers[0]["mark"] != "grid":
            out["at"] = wall_at(rule_layer[axis]["datum"], zones["t"])
    return out


def cases() -> list[dict[str, Any]]:
    return (
        [case(n, layers, DATA, {}) for n, layers in CASES]
        + [case(*c) for c in OWN]
        + [case(n, layers, data, {}, zones) for n, layers, data, zones in ZONED]
    )


def main() -> None:
    doc = {"kind": "datum-axes", "cases": cases()}
    CORPUS.write_text(json.dumps(doc, indent=1) + "\n")
    print(len(doc["cases"]), sum(c["placed"] for c in doc["cases"]))


if __name__ == "__main__":
    main()
