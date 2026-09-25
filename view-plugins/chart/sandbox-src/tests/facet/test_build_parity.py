"""The builder's two paths (plan-view-plugins-pr4 P3). ``_by_rows`` reads the
frame a row at a time and is the definition; ``_by_arrays`` does the same on
whole columns, because the row path took 69 s on the plan's 200 groups x
50 000 cells -- past the 60 s a sandbox command gets -- so that gallery never
opened. The row path is the oracle here: on every frame the column path takes,
both must write the same bytes (bar the random build id), say the same
progress, and refuse with the same message."""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from chart_view.facet import MAGIC, read_index, write_cache
from chart_view.facet.build import BuildError, _by_arrays, _by_rows, build_facet_cache
from chart_view.query import _kinds

_ID = len(MAGIC) + 32  # MAGIC, then the build id: random per write


def _args(frame: pd.DataFrame, **kw: Any) -> dict[str, Any]:
    x, y, value = kw.get("x", "x"), kw.get("y", "y"), kw.get("value", "v")
    x_type, y_type = kw.get("x_type", "ordinal"), kw.get("y_type", "ordinal")
    numeric = kw.get(
        "numeric",
        pd.api.types.is_numeric_dtype(frame[value])
        and not pd.api.types.is_bool_dtype(frame[value]),
    )
    return {
        "facet": kw.get("facet", ["g"]),
        "x": x,
        "y": y,
        "value": value,
        "sort": kw.get("sort", ()),
        "kinds": _kinds(
            "grid", [("x", {"field": x, "type": x_type}), ("y", {"field": y, "type": y_type})]
        ),
        "numeric": numeric,
    }


def _run(
    path_fn: Any, frame: pd.DataFrame, args: dict[str, Any], out: Path
) -> tuple[Any, list[str]]:
    lines: list[str] = []
    try:
        plan = path_fn(frame, **args, progress=lines.append)
    except BuildError as e:
        return ("refused", str(e)), lines
    if plan is None:
        return None, lines
    scale, cells, layout, groups = plan
    write_cache(
        out, scale=scale, facet=list(args["facet"]), cells=cells, layout=layout, groups=groups
    )
    raw = out.read_bytes()
    return raw[: len(MAGIC)] + raw[_ID:], lines


def _same(tmp_path: Path, frame: pd.DataFrame, **kw: Any) -> Any:
    args = _args(frame, **kw)
    rows, rows_lines = _run(_by_rows, frame, args, tmp_path / "rows.vcache")
    arrays, arrays_lines = _run(_by_arrays, frame, args, tmp_path / "arrays.vcache")
    assert arrays is not None, "the column path declined a frame it should take"
    assert arrays == rows
    assert arrays_lines == rows_lines
    return rows


def _wafers(rng: np.random.Generator) -> pd.DataFrame:
    """The plan's shape, shuffled: rows of a group are not contiguous, a few
    have no x, one group has none placed, values carry NaN and inf."""
    n, side = 7, 5
    rows = [
        {"g": f"w{g}", "x": x, "y": y, "v": float(rng.normal()), "rate": g / 10}
        for g in range(n)
        for x in range(side)
        for y in range(side)
    ]
    frame = pd.DataFrame(rows).sample(frac=1.0, random_state=3).reset_index(drop=True)
    frame.loc[[2, 9], "v"] = [math.nan, math.inf]
    frame["x"] = frame["x"].astype(float)
    frame.loc[[4, 11], "x"] = math.nan
    frame.loc[frame["g"] == "w6", "x"] = math.nan  # a group with no placed row
    return frame


def test_the_plans_shape_writes_the_same_cache_both_ways(tmp_path: Path) -> None:
    frame = _wafers(np.random.default_rng(1))
    _same(tmp_path, frame, sort=["rate"])
    _same(tmp_path, frame, sort=["rate"], x_type="quantitative")


@pytest.mark.parametrize(
    ("columns", "kw"),
    [
        # two facet columns; a float key where -0.0 and 0.0 are both "0"
        (
            {
                "lot": ["a", "a", "b", "b"],
                "w": [-0.0, 0.0, 1.5, 1.5],
                "x": [0, 1, 0, 1],
                "y": [0, 0, 0, 0],
                "v": [1.0, 2.0, 3.0, 4.0],
            },
            {"facet": ["lot", "w"]},
        ),
        # a float32 value, an int64 facet
        (
            {
                "g": [3, 3, 4],
                "x": [0, 1, 0],
                "y": [0, 0, 0],
                "v": np.array([1.25, 2.5, 3.0], dtype=np.float32),
            },
            {},
        ),
        # a text value with a missing one: categories
        ({"g": ["a", "a", "b"], "x": [0, 1, 0], "y": [0, 0, 0], "v": ["ok", None, "bad"]}, {}),
        # a true/false value: categories named as JavaScript names them
        ({"g": ["a", "a"], "x": [0, 1], "y": [0, 0], "v": [True, False]}, {}),
        # pandas' own string dtype, missing values in it
        (
            {
                "g": pd.array(["a", "b", "a"], dtype="string"),
                "x": [0, 0, 1],
                "y": [0, 0, 0],
                "v": pd.array(["p", pd.NA, "q"], dtype="string"),
            },
            {},
        ),
        # a quantitative x where -0.0 and 0.0 are one place, the first seen named
        (
            {"g": ["a", "b", "b"], "x": [-0.0, 0.0, 2.0], "y": [0, 0, 0], "v": [1.0, 2.0, 3.0]},
            {"x_type": "quantitative"},
        ),
        # text axes
        (
            {"g": ["a", "a"], "x": ["c2", "c1"], "y": ["r", "r"], "v": [1.0, 2.0]},
            {"x_type": "nominal"},
        ),
        # a temporal axis
        (
            {
                "g": ["a", "a"],
                "x": pd.to_datetime(["2026-01-02", "2026-01-01"]),
                "y": [0, 0],
                "v": [1.0, 2.0],
            },
            {"x_type": "temporal"},
        ),
        # a colour the spec calls categories though its dtype is numeric
        ({"g": ["a", "a"], "x": [0, 1], "y": [0, 0], "v": [1, 2]}, {"numeric": False}),
        # a nullable-integer facet, and a uint8 one
        ({"g": pd.array([1, 2], dtype="Int64"), "x": [0, 0], "y": [0, 0], "v": [1.0, 2.0]}, {}),
        ({"g": np.array([1, 2], dtype=np.uint8), "x": [0, 0], "y": [0, 0], "v": [1.0, 2.0]}, {}),
        # a row with an x and no y is left out too
        (
            {"g": ["a", "a", "a"], "x": [0, 1, 2], "y": [0.0, math.nan, 0.0], "v": [1.0, 2.0, 3.0]},
            {"y_type": "quantitative"},
        ),
        # two keys whose first-seen order is not their sorted order: (b, x) then
        # (a, y) then (b, y) -- groups keep the order their first row comes in
        (
            {
                "lot": ["b", "a", "b"],
                "w": ["x", "y", "y"],
                "x": [0, 0, 1],
                "y": [0, 0, 0],
                "v": [1.0, 2.0, 3.0],
            },
            {"facet": ["lot", "w"]},
        ),
    ],
    ids=[
        "two-keys-negative-zero",
        "float32-value-int-key",
        "text-value",
        "bool-value",
        "string-dtype",
        "quantitative-negative-zero-x",
        "text-axes",
        "temporal-axis",
        "numeric-as-categories",
        "Int64-key",
        "uint8-key",
        "no-y",
        "keys-first-seen-not-sorted",
    ],
)
def test_frames_of_every_dtype_the_column_path_takes_write_the_same_cache(
    tmp_path: Path, columns: dict[str, Any], kw: dict[str, Any]
) -> None:
    _same(tmp_path, pd.DataFrame(columns), **kw)


def _sorted(**sort_columns: Any) -> pd.DataFrame:
    n = len(next(iter(sort_columns.values())))
    return pd.DataFrame(
        {
            "g": ["a"] * (n - n // 2) + ["b"] * (n // 2),
            "x": list(range(n)),
            "y": [0] * n,
            "v": [1.0] * n,
        }
        | sort_columns
    )


@pytest.mark.parametrize(
    "column",
    [
        pd.to_datetime(
            ["2026-09-25 01:00", "2026-09-25 01:00", "2026-09-26 00:00", "2026-09-26 00:00"]
        ),
        pd.to_datetime(["2026-09-25 03:00+02:00"] * 2 + ["2026-09-26 00:00+02:00"] * 2),
        pd.to_datetime(["2026-09-25", "2026-09-25", None, None]),
        np.array(["2026-09-25", "2026-09-25", "2026-09-26", "2026-09-26"], dtype="datetime64[s]"),
        np.array([7, 7, -1, -1], dtype=np.int64),
        np.array([2**53, 2**53, 1, 1], dtype=np.uint64),
        np.array([7, 7, 9, 9], dtype=np.uint16),
        np.array([True, True, False, False]),
        [0.5, 0.5, math.nan, math.nan],
        [-0.0, -0.0, 0.0, 0.0],
        ["x", "x", "y", "y"],
        ["x", "x", None, math.nan],
        [None, None, None, None],
        # nanoseconds apart, one float of milliseconds: one sort value, not two
        pd.to_datetime(
            [
                "2026-09-25 00:00:00.000000001",
                "2026-09-25 00:00:00.000000000",
                "2026-09-26 00:00:00.000000000",
                "2026-09-26 00:00:00.000000000",
            ]
        ),
    ],
    ids=[
        "naive-time",
        "zoned-time",
        "time-with-NaT",
        "seconds-time",
        "int64",
        "uint64",
        "uint16",
        "bool",
        "float-with-nan",
        "negative-zero",
        "text",
        "text-with-missing",
        "all-missing",
        "nanoseconds-one-millisecond",
    ],
)
def test_sort_values_are_the_same_both_ways(tmp_path: Path, column: Any) -> None:
    _same(tmp_path, _sorted(s=column), sort=["s"])


@pytest.mark.parametrize(
    ("frame", "kw"),
    [
        # a sort value that varies, told apart by repr: -0.0 is not 0.0
        (_sorted(s=[-0.0, 0.0, 1.0, 1.0]), {"sort": ["s"]}),
        # a millisecond apart: two
        (
            _sorted(
                s=pd.to_datetime(
                    [
                        "2026-09-25 00:00:00.001",
                        "2026-09-25 00:00:00.000",
                        "2026-09-26 00:00:00.000",
                        "2026-09-26 00:00:00.000",
                    ]
                )
            ),
            {"sort": ["s"]},
        ),
        # b repeats a cell, a's sort varies: a comes first, so a's is the refusal
        (
            pd.DataFrame(
                {
                    "g": ["a", "a", "b", "b"],
                    "x": [0, 1, 0, 0],
                    "y": [0] * 4,
                    "v": [1.0] * 4,
                    "s": [1, 2, 3, 3],
                }
            ),
            {"sort": ["s"]},
        ),
        # one group repeats a cell AND varies its sort: the cell is named
        (
            pd.DataFrame({"g": ["a", "a"], "x": [0, 0], "y": [0, 0], "v": [1.0, 2.0], "s": [1, 2]}),
            {"sort": ["s"]},
        ),
        # two sort columns vary in one group: the first is named
        (_sorted(s=[1, 2, 3, 3], t=[1, 2, 3, 3]), {"sort": ["t", "s"]}),
        # a repeated cell in a later group, rows interleaved
        (
            pd.DataFrame(
                {"g": ["a", "b", "a", "b"], "x": [0, 1, 1, 1], "y": [0] * 4, "v": [1.0] * 4}
            ),
            {},
        ),
        # -0.0 then 0.0 on a quantitative x: one cell, and the refusal names the
        # row that repeated it (0.0), not the one it repeated (-0.0)
        (
            pd.DataFrame({"g": ["a", "a"], "x": [-0.0, 0.0], "y": [0, 0], "v": [1.0, 2.0]}),
            {"x_type": "quantitative"},
        ),
        # a key that is missing, and one that is not finite
        (pd.DataFrame({"g": ["a", None, None], "x": [0, 1, 2], "y": [0] * 3, "v": [1.0] * 3}), {}),
        (pd.DataFrame({"g": [1.0, math.inf], "x": [0, 1], "y": [0, 0], "v": [1.0, 2.0]}), {}),
        # no row can be placed
        (
            pd.DataFrame({"g": ["a"], "x": [math.nan], "y": [0], "v": [1.0]}),
            {"x_type": "quantitative"},
        ),
    ],
    ids=[
        "negative-zero-sort",
        "a-millisecond-apart",
        "earlier-group-refused-first",
        "cell-before-sort",
        "first-sort-column",
        "repeat-in-later-group",
        "repeat-names-the-repeating-row",
        "missing-key",
        "infinite-key",
        "nothing-placed",
    ],
)
def test_a_refusal_is_the_same_both_ways(
    tmp_path: Path, frame: pd.DataFrame, kw: dict[str, Any]
) -> None:
    refused, _message = _same(tmp_path, frame, **kw)
    assert refused == "refused"


@pytest.mark.parametrize(
    ("frame", "kw"),
    [
        # 1 and True hash alike in an object column; their texts are "1" and "true"
        (
            pd.DataFrame(
                {"g": [1, True], "x": [0, 1], "y": [0, 0], "v": [1.0, 2.0]}, dtype=object
            ).astype({"x": int, "y": int, "v": float}),
            {},
        ),
        (
            pd.DataFrame(
                {"g": pd.Categorical(["a", "b"]), "x": [0, 1], "y": [0, 0], "v": [1.0, 2.0]}
            ),
            {},
        ),
        (
            pd.DataFrame(
                {
                    "g": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                    "x": [0, 1],
                    "y": [0, 0],
                    "v": [1.0, 2.0],
                }
            ),
            {},
        ),
        (_sorted(s=pd.to_timedelta([1, 1, 2, 2], unit="s")), {"sort": ["s"]}),
        (_sorted(s=pd.array([1, 1, 2, pd.NA], dtype="Int64")), {"sort": ["s"]}),
        (_sorted(s=[1, 1, "b", "b"]), {"sort": ["s"]}),
        (_sorted(s=np.array(["3000-01-01"] * 4, dtype="datetime64[s]")), {"sort": ["s"]}),
        (_sorted(s=[dt.date(2026, 9, 25)] * 4), {"sort": ["s"]}),
        (
            pd.DataFrame({"g": ["a", "a"], "x": [0, 1], "y": [0, 0], "v": [1, "b"]}),
            {"numeric": False},
        ),
    ],
    ids=[
        "mixed-object-key",
        "categorical-key",
        "time-key",
        "timedelta-sort",
        "Int64-sort",
        "mixed-object-sort",
        "time-past-nanoseconds",
        "date-objects-sort",
        "mixed-object-value",
    ],
)
def test_a_frame_the_column_path_cannot_vouch_for_goes_the_row_path(
    tmp_path: Path, frame: pd.DataFrame, kw: dict[str, Any]
) -> None:
    args = _args(frame, **kw)
    lines: list[str] = []
    assert _by_arrays(frame, **args, progress=lines.append) is None
    assert lines == []  # declined before a word, or the row path would repeat it


def test_a_declined_frame_is_built_by_the_row_path(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {"g": pd.Categorical(["b", "a"]), "x": [0, 1], "y": [0, 0], "v": [1.0, 2.0]}
    )
    path = tmp_path / "c.vcache"
    build_facet_cache(frame, facet=["g"], x="x", y="y", value="v", path=path)
    assert [g.key for g in read_index(path).groups] == [("b",), ("a",)]


def test_the_plans_frames_take_the_column_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix is only a fix while the plan's frames go the fast way: text
    keys, integer or float axes, a float value and a float rank."""
    import chart_view.facet.build as build

    def refuse(*_a: Any, **_k: Any) -> None:
        raise AssertionError("went the row path")

    monkeypatch.setattr(build, "_by_rows", refuse)
    frame = _wafers(np.random.default_rng(2))
    build_facet_cache(
        frame, facet=["g"], x="x", y="y", value="v", sort=["rate"], path=tmp_path / "c.vcache"
    )
    frame["x"] = frame["x"].fillna(0).astype(np.int64)
    frame = frame.drop_duplicates(["g", "x", "y"])
    build_facet_cache(
        frame, facet=["g"], x="x", y="y", value="v", sort=["rate"], path=tmp_path / "d.vcache"
    )
