"""How the gallery reads a cache: sorted pages, the exact values an enlarged
group shows, and a header a browser can parse."""

import datetime as dt
import json
import math
from pathlib import Path

import pytest

from aiws_facet_cache import (
    MAGIC,
    CategoryScale,
    ContinuousScale,
    Group,
    read_exact,
    read_groups,
    read_index,
    read_records,
    write_cache,
)


def _write(path: Path, groups: list[Group], scale=None, cells: int = 1) -> None:
    write_cache(
        path,
        scale=scale or ContinuousScale(0.0, 100.0),
        facet=("g",),
        cells=cells,
        layout={},
        groups=groups,
    )


def test_a_sorted_page_reads_the_groups_at_the_positions_asked_in_that_order(
    tmp_path: Path,
) -> None:
    """Sorting uses the index in hand (P6), so the next page is scattered
    positions in the written order, not a contiguous range."""
    path = tmp_path / "c.vcache"
    _write(path, [Group(key=(f"g{i}",), sort={}, values=[float(i)]) for i in range(50)])
    index = read_index(path)
    page = read_groups(path, index, [42, 3, 17])
    assert [index.scale.decode(r) for r in page] == [
        pytest.approx([42.0], abs=0.2),
        pytest.approx([3.0], abs=0.2),
        pytest.approx([17.0], abs=0.2),
    ]


def test_a_position_outside_the_index_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path, [Group(key=("a",), sort={}, values=[1.0])])
    index = read_index(path)
    with pytest.raises(IndexError):
        read_groups(path, index, [1])
    with pytest.raises(IndexError):
        read_groups(path, index, [-1])


def test_a_negative_start_is_refused_never_read_as_header_bytes(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path, [Group(key=("a",), sort={}, values=[1.0])])
    with pytest.raises(IndexError):
        read_records(path, read_index(path), -1, 1)


def test_an_enlarged_group_gets_its_exact_values_back(tmp_path: Path) -> None:
    """Enlarge shows exact tooltips (P6) without re-reading the source."""
    path = tmp_path / "c.vcache"
    exact = [0.123456789, 99.987654321, None]
    _write(
        path,
        [Group(key=("a",), sort={}, values=[1.0, 2.0, 3.0]), Group(("b",), {}, exact)],
        cells=3,
    )
    index = read_index(path)
    assert read_exact(path, index, 1) == exact


def test_a_category_cache_has_no_exact_section_to_read(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path, [Group(key=("a",), sort={}, values=["x"])], scale=CategoryScale(["x"]))
    index = read_index(path)
    assert index.scale.decode(read_groups(path, index, [0])[0]) == ["x"]
    with pytest.raises(TypeError, match="category"):
        read_exact(path, index, 0)


def test_a_group_needs_at_least_one_cell(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cells"):
        _write(tmp_path / "c.vcache", [Group(key=("a",), sort={}, values=[])], cells=0)


def _header(path: Path) -> str:
    data = path.read_bytes()
    n = int.from_bytes(data[len(MAGIC) : len(MAGIC) + 4], "little")
    return data[len(MAGIC) + 4 : len(MAGIC) + 4 + n].decode()


def test_the_header_is_json_a_browser_parses(tmp_path: Path) -> None:
    """Python's json writes NaN/Infinity literals that JSON.parse rejects. A
    missing sort value (pandas NaN) must come out as null."""

    class NumpyLike:  # what a pandas/numpy scalar looks like to us
        def __init__(self, v: object) -> None:
            self.v = v

        def item(self) -> object:
            return self.v

    path = tmp_path / "c.vcache"
    _write(
        path,
        [
            Group(
                key=("a",),
                sort={
                    "rate": math.nan,
                    "n": NumpyLike(7),
                    "when": dt.datetime(2026, 9, 25, 1, 2, 3),
                    "day": dt.date(2026, 9, 25),
                },
                values=[1.0],
            )
        ],
    )
    raw = json.loads(_header(path), parse_constant=lambda c: pytest.fail(f"{c} in header"))
    assert raw["groups"][0]["sort"] == {
        "rate": None,
        "n": 7,
        "when": "2026-09-25T01:02:03",
        "day": "2026-09-25",
    }
    assert read_index(path).groups[0].sort["rate"] is None


def test_a_layout_json_cannot_carry_strictly_is_refused(tmp_path: Path) -> None:
    """The layout is the builder's, passed through as is: a NaN in it must fail
    the build, never land in a header the browser cannot parse."""
    with pytest.raises(ValueError):
        write_cache(
            tmp_path / "c.vcache",
            scale=ContinuousScale(0.0, 1.0),
            facet=("g",),
            cells=1,
            layout={"x": [math.nan]},
            groups=[Group(key=("a",), sort={}, values=[1.0])],
        )
    assert list(tmp_path.iterdir()) == []


def test_a_sort_value_json_cannot_carry_is_refused_by_field_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'weird'"):
        _write(tmp_path / "c.vcache", [Group(("a",), {"weird": object()}, [1.0])])
