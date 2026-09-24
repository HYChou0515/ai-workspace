"""The builder (plan-view-plugins-pr4 P3): a long-format frame, one row per
cell, becomes one facet cache. It is the one place pandas values are turned
into the plain ones the format takes."""

import datetime as dt
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from chart_view.facet import (
    MISSING,
    CategoryScale,
    ContinuousScale,
    read_exact,
    read_groups,
    read_index,
)
from chart_view.facet.build import BuildError, build_facet_cache


def _frame() -> pd.DataFrame:
    rows = []
    for lot, wafer, base in [("L1", 3, 0.0), ("L1", 4, 10.0)]:
        for x in (0, 1):
            for y in (0, 1):
                row = {"lot": lot, "wafer": wafer, "x": x, "y": y, "v": base + 2 * x + y}
                rows.append(row | {"rate": base})
    return pd.DataFrame(rows)


def test_a_frame_becomes_one_group_per_facet_key_over_the_shared_lattice(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.vcache"
    build_facet_cache(
        _frame(), facet=["lot", "wafer"], x="x", y="y", value="v", sort=["rate"], path=path
    )
    index = read_index(path)
    assert index.facet == ("lot", "wafer")
    # keys are the marking strings a linked view compares (canon): 3 -> "3"
    assert [g.key for g in index.groups] == [("L1", "3"), ("L1", "4")]
    assert [g.sort for g in index.groups] == [{"rate": 0.0}, {"rate": 10.0}]
    assert index.cells == 4
    assert sorted(zip(index.layout["x"], index.layout["y"], strict=True)) == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert index.scale == ContinuousScale(0.0, 13.0)
    # the exact values come back cell for cell, in the layout's order
    got = dict(
        zip(
            zip(index.layout["x"], index.layout["y"], strict=True),
            read_exact(path, index, 1),
            strict=True,
        )
    )
    assert got == {(0, 0): 10.0, (0, 1): 11.0, (1, 0): 12.0, (1, 1): 13.0}


def _one(tmp_path: Path, rows: list[dict], **kw: Any) -> Path:
    path = tmp_path / "c.vcache"
    args: dict[str, Any] = {"facet": ["g"], "x": "x", "y": "y", "value": "v", "path": path}
    build_facet_cache(pd.DataFrame(rows), **(args | kw))
    return path


def test_a_text_value_builds_a_category_cache_with_missing_cells(tmp_path: Path) -> None:
    rows = [
        {"g": "a", "x": 0, "y": 0, "v": "pass"},
        {"g": "a", "x": 1, "y": 0, "v": "fail"},
        {"g": "b", "x": 0, "y": 0, "v": None},
    ]
    path = _one(tmp_path, rows)
    index = read_index(path)
    assert index.scale == CategoryScale(["fail", "pass"])
    a, b = (index.scale.decode(r) for r in read_groups(path, index, [0, 1]))
    assert a == ["pass", "fail"] and b == [None, None]  # b has no row at (1, 0) either


def test_a_true_false_value_is_a_category_as_the_chart_marks_it(tmp_path: Path) -> None:
    """pandas calls bool numeric; as a colour it is two categories, named as
    JavaScript's String() names them."""
    rows = [{"g": "a", "x": 0, "y": 0, "v": True}, {"g": "a", "x": 1, "y": 0, "v": False}]
    index = read_index(_one(tmp_path, rows))
    assert index.scale == CategoryScale(["false", "true"])


def test_an_infinite_cell_is_missing_on_the_thumbnail_and_exact_on_enlarge(
    tmp_path: Path,
) -> None:
    rows = [{"g": "a", "x": 0, "y": 0, "v": math.inf}, {"g": "a", "x": 1, "y": 0, "v": 1.0}]
    path = _one(tmp_path, rows)
    index = read_index(path)
    assert read_groups(path, index, [0])[0][0] == MISSING
    assert read_exact(path, index, 0)[0] == math.inf


def test_keys_are_the_marking_strings_of_their_values(tmp_path: Path) -> None:
    """A float key 3.0 is "3" to JavaScript's String(), which is what a linked
    view's marking holds."""
    rows = [{"g": 3.0, "x": 0, "y": 0, "v": 1.0}, {"g": 4.5, "x": 0, "y": 0, "v": 1.0}]
    assert [g.key for g in read_index(_one(tmp_path, rows)).groups] == [("3",), ("4.5",)]


def test_text_axes_are_placed_by_their_text(tmp_path: Path) -> None:
    path = _one(tmp_path, [{"g": "a", "x": "c1", "y": "r1", "v": 1.0}])
    assert read_index(path).layout == {"x": ["c1"], "y": ["r1"]}


def test_sort_values_become_plain_json_values(tmp_path: Path) -> None:
    rows = [
        {
            "g": "a",
            "x": 0,
            "y": 0,
            "v": 1.0,
            "when": pd.Timestamp("2026-09-25 01:00:00"),
            "zoned": pd.Timestamp("2026-09-25 03:00:00+02:00"),
            "never": pd.NaT,
            "n": np.int64(7),
            "gap": np.nan,
            "day": dt.date(2026, 9, 25),
            "name": "x",
        }
    ]
    path = _one(tmp_path, rows, sort=["when", "zoned", "never", "n", "gap", "day", "name"])
    ms = pd.Timestamp("2026-09-25 01:00:00", tz="UTC").value / 1e6
    day = pd.Timestamp("2026-09-25", tz="UTC").value / 1e6
    assert read_index(path).groups[0].sort == {
        "when": ms,
        "zoned": ms,
        "never": None,
        "n": 7,
        "gap": None,
        "day": day,
        "name": "x",
    }


@pytest.mark.parametrize(
    ("rows", "kw", "message"),
    [
        ([{"g": "a", "x": 0, "y": 0}], {}, "'v'"),
        ([{"g": None, "x": 0, "y": 0, "v": 1.0}], {}, "'g'"),
        (
            [{"g": "a", "x": 0, "y": 0, "v": 1.0}, {"g": "a", "x": 0, "y": 0, "v": 2.0}],
            {},
            "aggregate first",
        ),
        (
            [
                {"g": "a", "x": 0, "y": 0, "v": 1.0, "s": 1},
                {"g": "a", "x": 1, "y": 0, "v": 1.0, "s": 2},
            ],
            {"sort": ["s"]},
            "'s'",
        ),
    ],
    ids=["missing-column", "missing-key", "two-rows-one-cell", "sort-varies-in-group"],
)
def test_a_frame_that_is_not_one_facet_cache_is_refused_by_name(
    tmp_path: Path, rows: list[dict], kw: dict, message: str
) -> None:
    with pytest.raises(BuildError, match=message):
        _one(tmp_path, rows, **kw)


def test_the_build_reports_its_progress(tmp_path: Path) -> None:
    lines: list[str] = []
    build_facet_cache(
        _frame(),
        facet=["lot", "wafer"],
        x="x",
        y="y",
        value="v",
        path=tmp_path / "c.vcache",
        progress=lines.append,
    )
    assert lines[0] == "read 8 rows"
    assert lines[1] == "2 groups over 4 cells"
    assert lines[2].startswith("wrote ") and lines[2].endswith(" bytes")
