"""Sort the gallery by any column (plan-view-plugins-pr5-finish P4).

The build lists every column with its kind and whether it holds one value per
group (the menu), and writes each group's sort key for the column the gallery
sorts by: its value when the column holds one value per group, else the
statistic the person picked. The oracle is pandas: ``groupby(...).agg(stat)``
then ``sort_values`` over the same groups."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from chart_view.cli import main
from chart_view.facet import read_index
from chart_view.facet.build import BuildError, build_facet_cache
from chart_view.spec import spec_errors
from chart_view.wire import canon

FACET = ["lot", "wafer"]


def _frame() -> pd.DataFrame:
    """45 groups on a 3 x 3 lattice, with numeric, text and date columns that
    hold one value per group or several, missing values here and there, and a
    few rows with no x (off the map: not in the tile, so not in its sort)."""
    rng = np.random.default_rng(7)
    rows: list[dict[str, Any]] = []
    for g in range(45):
        lot, wafer = f"L{g % 3}", g // 3 + 1
        label = f"w{rng.integers(0, 12):02d}"  # ties across groups
        score = None if g % 11 == 4 else float(rng.integers(0, 6))
        when = pd.Timestamp("2026-01-01") + dt.timedelta(days=int(rng.integers(0, 30)))
        for x in range(3):
            for y in range(3):
                rows.append(
                    {
                        "lot": lot,
                        "wafer": wafer,
                        "x": x,
                        "y": y,
                        "v": None if rng.random() < 0.1 else float(rng.integers(0, 50)),
                        "n": int(rng.integers(-5, 5)),
                        "tool": None if rng.random() < 0.1 else str(rng.choice(["A", "B", "C"])),
                        "label": label,
                        "score": score,
                        "when": when,
                        "stamp": when + dt.timedelta(hours=int(rng.integers(0, 90))),
                    }
                )
        # a row off the map: its v would move the group's mean if it counted
        rows.append({**rows[-1], "x": None, "v": 10_000.0, "tool": "Z"})
    return pd.DataFrame(rows)


def _build(tmp_path: Path, frame: pd.DataFrame, field: str | None, stat: str | None) -> Path:
    path = tmp_path / f"{field}-{stat}.vcache"
    build_facet_cache(
        frame,
        facet=FACET,
        x="x",
        y="y",
        value="v",
        sort=[field] if field else [],
        stat=stat,
        path=path,
    )
    return path


def _gallery_order(values: list[Any], descending: bool) -> list[int]:
    """The gallery's rule (sortedPositions): stable, missing last either way."""
    present = [i for i, v in enumerate(values) if v is not None]
    missing = [i for i, v in enumerate(values) if v is None]
    present.sort(key=lambda i: values[i], reverse=descending)  # sort(reverse) is stable
    return present + missing


def _oracle(frame: pd.DataFrame, keys: list[tuple[str, ...]], field: str, stat: str) -> list[Any]:
    placed = frame.dropna(subset=["x", "y"])
    agg = placed.groupby(FACET, sort=False)[field].agg("nunique" if stat == "distinct" else stat)
    agg.index = pd.Index([tuple(canon(v) for v in k) for k in agg.index], tupleize_cols=False)
    return agg.reindex(pd.Index(keys, tupleize_cols=False)).tolist()


CASES = [
    ("v", "count"),
    ("v", "min"),
    ("v", "max"),
    ("v", "mean"),
    ("v", "median"),
    ("n", "mean"),
    ("n", "median"),
    ("tool", "count"),
    ("tool", "distinct"),
    ("stamp", "min"),
    ("stamp", "max"),
    ("stamp", "count"),
]


@pytest.mark.parametrize(("field", "stat"), CASES, ids=[f"{f}-{s}" for f, s in CASES])
@pytest.mark.parametrize("descending", [False, True], ids=["ascending", "descending"])
def test_a_statistic_sorts_the_groups_as_pandas_does(
    tmp_path: Path, field: str, stat: str, descending: bool
) -> None:
    frame = _frame()
    index = read_index(_build(tmp_path, frame, field, stat))
    keys = [g.key for g in index.groups]
    ours = [g.sort[field] for g in index.groups]
    oracle = _oracle(frame, keys, field, stat)
    expected = (
        pd.Series(oracle, dtype=object)
        .map(lambda v: None if v is None or (not isinstance(v, str) and pd.isna(v)) else v)
        .sort_values(ascending=not descending, kind="stable", na_position="last")
        .index.tolist()
    )
    # pandas sorts NaN last; a stable sort keeps ties in the groups' order
    assert _gallery_order(ours, descending) == expected
    # and the values themselves: a date is its UTC epoch milliseconds
    for got, want in zip(ours, oracle, strict=True):
        if want is None or (not isinstance(want, str) and pd.isna(want)):
            assert got is None
        elif isinstance(want, pd.Timestamp):
            assert got == want.value / 1e6
        else:
            assert got == pytest.approx(float(want))


@pytest.mark.parametrize("field", ["label", "score", "when", "lot"])
def test_a_column_with_one_value_per_group_sorts_by_that_value(tmp_path: Path, field: str) -> None:
    frame = _frame()
    index = read_index(_build(tmp_path, frame, field, None))
    ours = [g.sort[field] for g in index.groups]
    first = frame.dropna(subset=["x", "y"]).groupby(FACET, sort=False)[field].first()
    for got, want in zip(ours, first.tolist(), strict=True):
        if isinstance(want, pd.Timestamp):
            assert got == want.value / 1e6
        elif isinstance(want, float) and np.isnan(want):
            assert got is None
        else:
            assert got == want


def test_the_index_lists_every_column_with_its_kind_and_whether_it_is_one_per_group(
    tmp_path: Path,
) -> None:
    path = _build(tmp_path, _frame(), None, None)
    columns = read_index(path).columns
    assert columns == [
        {
            "name": "lot",
            "kind": "text",
            "single": True,
            "stats": ["distinct", "count"],
            "stack": ["count", "distinct"],
        },
        {
            "name": "wafer",
            "kind": "number",
            "single": True,
            "stats": ["mean", "median", "min", "max", "count"],
            "stack": ["mean", "median", "min", "max", "sum", "count"],
        },
        {
            "name": "x",
            "kind": "number",
            "single": False,
            "stats": ["mean", "median", "min", "max", "count"],
            "stack": ["mean", "median", "min", "max", "sum", "count"],
        },
        {
            "name": "y",
            "kind": "number",
            "single": False,
            "stats": ["mean", "median", "min", "max", "count"],
            "stack": ["mean", "median", "min", "max", "sum", "count"],
        },
        {
            "name": "v",
            "kind": "number",
            "single": False,
            "stats": ["mean", "median", "min", "max", "count"],
            "stack": ["mean", "median", "min", "max", "sum", "count"],
        },
        {
            "name": "n",
            "kind": "number",
            "single": False,
            "stats": ["mean", "median", "min", "max", "count"],
            "stack": ["mean", "median", "min", "max", "sum", "count"],
        },
        {
            "name": "tool",
            "kind": "text",
            "single": False,
            "stats": ["distinct", "count"],
            "stack": ["count", "distinct"],
        },
        {
            "name": "label",
            "kind": "text",
            "single": True,
            "stats": ["distinct", "count"],
            "stack": ["count", "distinct"],
        },
        {
            "name": "score",
            "kind": "number",
            "single": True,
            "stats": ["mean", "median", "min", "max", "count"],
            "stack": ["mean", "median", "min", "max", "sum", "count"],
        },
        {
            "name": "when",
            "kind": "date",
            "single": True,
            "stats": ["min", "max", "count"],
            "stack": ["count"],
        },
        {
            "name": "stamp",
            "kind": "date",
            "single": False,
            "stats": ["min", "max", "count"],
            "stack": ["count"],
        },
    ]


@pytest.mark.parametrize(
    ("field", "stat", "why"),
    [
        ("tool", "mean", "tool"),
        ("tool", "median", "tool"),
        ("stamp", "mean", "stamp"),
        ("v", "distinct", "v"),
    ],
)
def test_a_statistic_the_column_kind_has_no_meaning_for_is_refused_by_name(
    tmp_path: Path, field: str, stat: str, why: str
) -> None:
    with pytest.raises(BuildError, match=f"{stat}.*{why}|{why}.*{stat}"):
        _build(tmp_path, _frame(), field, stat)


def test_a_statistic_sorts_by_one_column(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="one column"):
        build_facet_cache(
            _frame(),
            facet=FACET,
            x="x",
            y="y",
            value="v",
            sort=["v", "n"],
            stat="mean",
            path=tmp_path / "c.vcache",
        )


def test_a_facet_column_only_the_row_path_reads_still_finds_each_rows_group(
    tmp_path: Path,
) -> None:
    """An object column of mixed types (1 and True hash alike, but are
    different texts) is keyed a row at a time: the statistic must still land
    on the group each row is keyed to."""
    frame = _frame()
    frame["lot"] = frame["lot"].map({"L0": 1, "L1": "a", "L2": True}).astype(object)
    index = read_index(_build(tmp_path, frame, "v", "max"))
    keys = [g.key for g in index.groups]
    assert {k[0] for k in keys} == {"1", "a", "true"} and len(keys) == 45
    placed = frame.dropna(subset=["x", "y"])
    by = [placed["lot"].map(canon), placed["wafer"].map(canon)]
    want = placed.groupby(by, sort=False)["v"].max()
    assert [g.sort["v"] for g in index.groups] == [want[k] for k in keys]


def test_a_mixed_type_column_is_read_a_row_at_a_time_for_one_value_per_group(
    tmp_path: Path,
) -> None:
    frame = _frame()
    frame["mix"] = pd.Series([1 if i % 2 else "a" for i in range(len(frame))], dtype=object)
    frame["same"] = pd.Series([1 if i % 2 else "1" for i in range(len(frame))], dtype=object)
    columns = {
        c["name"]: c["single"] for c in read_index(_build(tmp_path, frame, None, None)).columns
    }
    # 1 and "a" differ; so do 1 and "1" (repr tells them apart, as the build does)
    assert (columns["mix"], columns["same"]) == (False, False)
    frame["one"] = pd.Series([True] * len(frame), dtype=object)
    columns = {
        c["name"]: c["single"] for c in read_index(_build(tmp_path, frame, None, None)).columns
    }
    assert columns["one"] is True


def test_rows_are_matched_to_groups_by_key_not_by_order() -> None:
    """The statistic lands on the group each row is KEYED to, whatever order
    the groups are listed in."""
    from chart_view.facet import Group
    from chart_view.facet.build import _row_positions

    frame = pd.DataFrame({"g": ["a", "b", "a", "c"]})
    groups = [Group(key=(k,), sort={}, values=[]) for k in ("c", "a", "b")]
    assert _row_positions(frame, ["g"], groups).tolist() == [1, 2, 1, 0]


def test_a_column_with_several_values_and_no_statistic_asks_for_one(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="statistic"):
        _build(tmp_path, _frame(), "v", None)


def test_the_spec_takes_a_statistic_and_nothing_else() -> None:
    base = {
        "view": "chart",
        "source": "data/w.csv",
        "mark": "grid",
        "encoding": {
            "x": {"field": "x", "type": "ordinal"},
            "y": {"field": "y", "type": "ordinal"},
            "color": {"field": "v", "type": "quantitative"},
        },
    }
    for stat in ["count", "distinct", "min", "max", "mean", "median"]:
        spec = {**base, "facet": {"field": "lot", "sort": {"field": "v", "stat": stat}}}
        assert spec_errors(spec) == [], stat
    bad = {**base, "facet": {"field": "lot", "sort": {"field": "v", "stat": "worst"}}}
    assert spec_errors(bad)


# ─── through the command ────────────────────────────────────────────────────

SPEC = """\
view: chart
source: data/w.csv
facet: {{field: [lot, wafer], sort: {sort}}}
mark: grid
encoding:
  x: {{field: x, type: ordinal}}
  y: {{field: y, type: ordinal}}
  color: {{field: v, type: quantitative}}
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ws" / "data").mkdir(parents=True)
    _frame().drop(columns=["when", "stamp"]).to_csv(tmp_path / "ws" / "data" / "w.csv", index=False)
    monkeypatch.chdir(tmp_path / "ws")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _call(capsys: pytest.CaptureFixture[str], cmd: str, args: dict) -> tuple[int, Any, str]:
    code = main([cmd, json.dumps(args)])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if code == 0 else out.out), out.err


def test_the_statistic_is_part_of_the_cache_key_and_the_order_is_not(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keys = {}
    for sort in [
        "{field: v, stat: mean}",
        "{field: v, stat: mean, order: descending}",
        "{field: v, stat: median}",
        "{field: tool, stat: count}",
    ]:
        code, built, err = _call(capsys, "facet_build", {"spec": SPEC.format(sort=sort)})
        assert code == 0, err
        keys[sort] = built["key"]
    assert keys["{field: v, stat: mean}"] == keys["{field: v, stat: mean, order: descending}"]
    assert len(set(keys.values())) == 3


def test_the_index_answers_the_columns_for_the_menu(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, built, _ = _call(capsys, "facet_build", {"spec": SPEC.format(sort="{field: v, stat: max}")})
    code, index, _ = _call(capsys, "facet_index", {"key": built["key"]})
    assert code == 0
    assert {c["name"]: c["kind"] for c in index["columns"]}["tool"] == "text"
    assert index["groups"][0]["sort"].keys() == {"v"}


def test_a_statistic_that_does_not_fit_exits_2_naming_it(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _call(
        capsys, "facet_build", {"spec": SPEC.format(sort="{field: tool, stat: mean}")}
    )
    assert code == 2 and "mean" in err and "tool" in err


# ─── a sort chosen in the gallery, beside the view file (#847/#848 P9) ───────


def _file(workspace: Path, sort: str) -> str:
    (workspace / "ws" / "views").mkdir(exist_ok=True)
    (workspace / "ws" / "views" / "g.ai.yaml").write_text(SPEC.format(sort=sort))
    return "views/g.ai.yaml"


def test_a_sort_beside_the_file_builds_as_that_sort_in_the_spec_would(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gallery names the view by its file; a sort the person picks rides
    beside it, and builds exactly the cache the spec with that sort would."""
    path = _file(workspace, "{field: score, order: descending}")
    over = {"field": "v", "stat": "max"}
    code, built, err = _call(capsys, "facet_build", {"path": path, "rev": "r", "sort": over})
    assert code == 0, err
    in_spec = SPEC.format(sort="{field: v, stat: max, order: descending}")
    assert _call(capsys, "facet_build", {"spec": in_spec})[1]["key"] == built["key"]
    _, index, _ = _call(capsys, "facet_index", {"key": built["key"]})
    assert index["groups"][0]["sort"].keys() == {"v"}


def test_a_null_sort_beside_the_file_is_the_written_order(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(workspace, "{field: score}")
    code, built, _ = _call(capsys, "facet_build", {"path": path, "sort": None})
    assert code == 0
    _, index, _ = _call(capsys, "facet_index", {"key": built["key"]})
    assert all(g["sort"] == {} for g in index["groups"])


@pytest.mark.parametrize(
    "sort",
    ["v", {"stat": "max"}, {"field": 3}, {"field": "v", "stat": 1}, {"field": "v", "x": "y"}],
    ids=["text", "no-field", "field-number", "stat-number", "extra-key"],
)
def test_a_sort_that_is_not_a_choice_exits_2(
    workspace: Path, capsys: pytest.CaptureFixture[str], sort: Any
) -> None:
    path = _file(workspace, "{field: score}")
    code, _, err = _call(capsys, "facet_build", {"path": path, "sort": sort})
    assert code == 2 and "sort" in err


def test_a_sort_beside_a_spec_with_no_facet_says_it_needs_one(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = SPEC.format(sort="{field: score}").replace(
        "facet: {field: [lot, wafer], sort: {field: score}}\n", ""
    )
    code, _, err = _call(capsys, "facet_build", {"spec": plain, "sort": {"field": "v"}})
    assert code == 2 and "needs a spec with facet" in err


def test_a_statistic_the_schema_does_not_know_is_refused_as_the_spec_would_be(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(workspace, "{field: score}")
    args = {"path": path, "sort": {"field": "v", "stat": "worst"}}
    code, _, err = _call(capsys, "facet_build", args)
    assert code == 2 and "stat" in err
