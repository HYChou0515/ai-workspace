"""The stack panel's query (plan-view-plugins-pr5-finish P5): a set of groups
of a built gallery stacked into ONE map -- at each cell, the picked column
over those groups' rows, summarised by the picked statistic -- and, with a
second set, A, B and A - B.

It is a query over the cache the gallery already built, not a new build: the
cache names the source, its version and the transform, and places each cell;
the rows come from the same source, read the same way. The oracle is pandas:
the same rows, ``groupby([x, y])[column].agg(stat)``."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from chart_view.cli import main
from chart_view.wire import canon

SPEC = """\
view: chart
source: data/w.csv
facet: {field: [lot, wafer]}
mark: grid
transform: [{filter: "keep"}]
encoding:
  x: {field: x, type: ordinal}
  y: {field: y, type: ordinal}
  color: {field: v, type: quantitative}
"""


def _frame() -> pd.DataFrame:
    """12 groups on a 3 x 4 lattice (some cells missing per group), a number,
    an integer and a text column, missing values, rows off the map (no x),
    and rows the spec's transform filters out (keep == False)."""
    rng = np.random.default_rng(3)
    rows: list[dict[str, Any]] = []
    for g in range(12):
        for x in range(3):
            for y in range(4):
                if rng.random() < 0.15:
                    continue  # this group has no row at this cell
                rows.append(
                    {
                        "lot": f"L{g % 2}",
                        "wafer": g,
                        "x": x,
                        "y": y,
                        "v": None if rng.random() < 0.1 else round(float(rng.normal(5, 2)), 3),
                        "n": int(rng.integers(0, 9)),
                        "tool": None if rng.random() < 0.1 else str(rng.choice(["A", "B", "C"])),
                        "keep": True,
                    }
                )
        rows.append({**rows[-1], "x": None, "v": 1e6, "tool": "Z"})  # off the map
        rows.append({**rows[-1], "x": 0, "y": 0, "keep": False, "v": -1e6})  # filtered out
    return pd.DataFrame(rows)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ws" / "data").mkdir(parents=True)
    _frame().to_csv(tmp_path / "ws" / "data" / "w.csv", index=False)
    monkeypatch.chdir(tmp_path / "ws")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _call(capsys: pytest.CaptureFixture[str], cmd: str, args: dict) -> tuple[int, Any, str]:
    code = main([cmd, json.dumps(args)])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if code == 0 else out.out), out.err


def _built(capsys: pytest.CaptureFixture[str]) -> tuple[str, dict]:
    code, built, err = _call(capsys, "facet_build", {"spec": SPEC})
    assert code == 0, err
    _, index, _ = _call(capsys, "facet_index", {"key": built["key"]})
    return built["key"], index


def _f64(wire: dict | None) -> list[float | None] | None:
    if wire is None:
        return None
    assert wire["kind"] == "f64"
    data = np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8")
    return [None if np.isnan(v) else float(v) for v in data]


def _oracle(index: dict, keys: list[list[str]] | None, column: str, stat: str) -> list:
    """pandas over the same rows: the source, the spec's transform, the groups
    asked (all when None), the rows with an x and a y; one value per cell of
    the cache's layout, None where no row is."""
    frame = pd.read_csv("data/w.csv")
    frame = frame[frame["keep"]]
    frame = frame.dropna(subset=["x", "y"])
    if keys is not None:
        wanted = {tuple(k) for k in keys}
        mine = [
            (canon(lot), canon(w)) in wanted
            for lot, w in zip(frame["lot"], frame["wafer"], strict=True)
        ]
        frame = frame[mine]
    agg = frame.groupby(["x", "y"])[column].agg("nunique" if stat == "distinct" else stat)
    by_cell = {(float(x), float(y)): v for (x, y), v in agg.items()}
    layout = index["layout"]
    out = []
    for x, y in zip(layout["x"], layout["y"], strict=True):
        v = by_cell.get((float(x), float(y)))
        out.append(None if v is None or pd.isna(v) else float(v))
    return out


def _approx(ours: list | None, want: list) -> None:
    assert ours is not None
    assert [v is None for v in ours] == [v is None for v in want]
    assert [v for v in ours if v is not None] == pytest.approx([v for v in want if v is not None])


CASES = [
    ("v", "mean"),
    ("v", "median"),
    ("v", "min"),
    ("v", "max"),
    ("v", "sum"),
    ("v", "count"),
    ("n", "sum"),
    ("tool", "count"),
    ("tool", "distinct"),
]


@pytest.mark.parametrize(("column", "stat"), CASES, ids=[f"{c}-{s}" for c, s in CASES])
def test_a_stack_of_every_group_is_pandas_over_the_same_rows(
    workspace: Path, capsys: pytest.CaptureFixture[str], column: str, stat: str
) -> None:
    key, index = _built(capsys)
    args = {"key": key, "build": index["build"], "a": None, "b": None}
    code, got, err = _call(capsys, "facet_stack", {**args, "column": column, "stat": stat})
    assert code == 0, err
    _approx(_f64(got["a"]), _oracle(index, None, column, stat))
    assert got["b"] is None and got["diff"] is None
    assert got["groups"] == {"a": 12, "b": None}


def test_a_stack_of_some_groups_takes_only_their_rows(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, index = _built(capsys)
    a = [["L0", "0"], ["L0", "2"], ["L1", "5"]]
    code, got, err = _call(
        capsys,
        "facet_stack",
        {"key": key, "build": index["build"], "a": a, "b": None, "column": "v", "stat": "max"},
    )
    assert code == 0, err
    _approx(_f64(got["a"]), _oracle(index, a, "v", "max"))
    assert got["groups"]["a"] == 3


def test_a_minus_b_is_a_cell_by_cell_difference_missing_where_either_is(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, index = _built(capsys)
    a = [["L0", str(w)] for w in (0, 2, 4)]
    b = [["L1", str(w)] for w in (1, 3)]
    code, got, err = _call(
        capsys,
        "facet_stack",
        {"key": key, "build": index["build"], "a": a, "b": b, "column": "v", "stat": "mean"},
    )
    assert code == 0, err
    want_a, want_b = _oracle(index, a, "v", "mean"), _oracle(index, b, "v", "mean")
    _approx(_f64(got["a"]), want_a)
    _approx(_f64(got["b"]), want_b)
    want_diff = [
        None if va is None or vb is None else va - vb for va, vb in zip(want_a, want_b, strict=True)
    ]
    _approx(_f64(got["diff"]), want_diff)
    assert got["groups"] == {"a": 3, "b": 2}


def test_a_key_the_cache_does_not_hold_is_named(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, index = _built(capsys)
    code, _, err = _call(
        capsys,
        "facet_stack",
        {
            "key": key,
            "build": index["build"],
            "a": [["L9", "99"]],
            "b": None,
            "column": "v",
            "stat": "mean",
        },
    )
    assert code == 2 and "L9" in err


@pytest.mark.parametrize(
    ("column", "stat", "named"),
    [("tool", "mean", "tool"), ("v", "distinct", "v"), ("nope", "count", "nope")],
)
def test_a_column_or_statistic_that_does_not_fit_exits_2_naming_it(
    workspace: Path, capsys: pytest.CaptureFixture[str], column: str, stat: str, named: str
) -> None:
    key, index = _built(capsys)
    code, _, err = _call(
        capsys,
        "facet_stack",
        {"key": key, "build": index["build"], "a": None, "b": None, "column": column, "stat": stat},
    )
    assert code == 2 and repr(named) in err


def test_the_index_offers_each_columns_stack_statistics(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, index = _built(capsys)
    stack = {c["name"]: c["stack"] for c in index["columns"]}
    assert stack["v"] == ["mean", "median", "min", "max", "sum", "count"]
    assert stack["tool"] == ["count", "distinct"]


def test_an_edited_source_asks_for_a_rebuild_not_a_stale_stack(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rows must be the ones the cache was built from: a source edited
    since is exit 3, which sends the gallery round a rebuild."""
    key, index = _built(capsys)
    source = workspace / "ws" / "data" / "w.csv"
    source.write_text(source.read_text() + "L0,0,9,9,1.0,1,A,True\n")
    code, _, err = _call(
        capsys,
        "facet_stack",
        {"key": key, "build": index["build"], "a": None, "b": None, "column": "v", "stat": "mean"},
    )
    assert code == 3 and err


def test_a_removed_source_asks_for_a_rebuild_too(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, index = _built(capsys)
    (workspace / "ws" / "data" / "w.csv").unlink()
    code, _, err = _call(
        capsys,
        "facet_stack",
        {"key": key, "build": index["build"], "a": None, "b": None, "column": "v", "stat": "mean"},
    )
    assert code == 3 and "gone" in err


def test_a_stack_asked_against_an_older_build_exits_4(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, index = _built(capsys)
    code, _, _ = _call(
        capsys,
        "facet_stack",
        {"key": key, "build": "0" * 32, "a": None, "b": None, "column": "v", "stat": "mean"},
    )
    assert code == 4


@pytest.mark.parametrize(
    "bad",
    [
        {"a": "L0"},
        {"a": [["L0", 1]]},
        {"column": 3},
        {"stat": None},
    ],
    ids=["a-text", "a-key-number", "column-number", "stat-null"],
)
def test_a_wrong_call_exits_2_before_the_cache_is_looked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: dict
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    args = {"key": "a" * 64, "build": "b" * 32, "a": None, "b": None, "column": "v", "stat": "mean"}
    code, _, err = _call(capsys, "facet_stack", {**args, **bad})
    # 2, not 3 ("no cache, build one"): there is no cache here at all
    assert code == 2, err


def test_a_stack_never_writes_the_cache(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key, index = _built(capsys)
    views = workspace / "home" / ".cache" / "views"
    before = {p.name: p.stat().st_size for p in views.iterdir()}
    args = {"key": key, "build": index["build"], "a": None, "b": None, "column": "n", "stat": "sum"}
    assert _call(capsys, "facet_stack", args)[0] == 0
    assert {p.name: p.stat().st_size for p in views.iterdir()} == before


def test_a_row_off_the_map_lands_in_no_cell_not_the_last_one(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every group has a row with no x (``_frame``'s "off the map"). It has no
    cell, so it is stacked into none -- in particular not into the layout's
    LAST cell, where an index of -1 would put it. That only shows for a set
    whose rows leave the last cell empty (a placed row there overwrites it),
    so each such group is stacked alone, and the fixture must hold one."""
    key, index = _built(capsys)
    last = (float(index["layout"]["x"][-1]), float(index["layout"]["y"][-1]))
    frame = pd.read_csv("data/w.csv")
    placed = frame[frame["keep"]].dropna(subset=["x", "y"])
    at_last = {
        (canon(lot), canon(w))
        for lot, w, x, y in zip(
            placed["lot"], placed["wafer"], placed["x"], placed["y"], strict=True
        )
        if (float(x), float(y)) == last
    }
    empty_there = [g["key"] for g in index["groups"] if tuple(g["key"]) not in at_last]
    assert empty_there  # the case this test is for exists in the fixture
    for k in empty_there:
        args = {
            "key": key,
            "build": index["build"],
            "a": [k],
            "b": None,
            "column": "v",
            "stat": "max",
        }
        code, got, err = _call(capsys, "facet_stack", args)
        assert code == 0, err
        stacked = _f64(got["a"])
        assert stacked is not None and stacked[-1] is None, k
        _approx(stacked, _oracle(index, [k], "v", "max"))
