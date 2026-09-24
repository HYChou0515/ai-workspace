"""How the gallery reads a cache: sorted pages, the exact values an enlarged
group shows, and a header a browser can parse."""

import datetime as dt
import json
import math
from pathlib import Path

import pytest

from aiws_facet_cache import (
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
    return data[data.index(b"{") : read_index(path).data_offset].decode()


def test_the_header_is_json_a_browser_parses(tmp_path: Path) -> None:
    """Python's json writes NaN/Infinity literals that JSON.parse rejects. A
    non-finite sort value (pandas' missing NaN, or +/-inf) comes out as null."""
    path = tmp_path / "c.vcache"
    sort = {"rate": math.nan, "hi": math.inf, "n": 7, "ok": True, "name": "x", "none": None}
    _write(path, [Group(key=("a",), sort=sort, values=[1.0])])
    raw = json.loads(_header(path), parse_constant=lambda c: pytest.fail(f"{c} in header"))
    assert raw["groups"][0]["sort"] == {
        "rate": None,
        "hi": None,
        "n": 7,
        "ok": True,
        "name": "x",
        "none": None,
    }
    assert read_index(path).groups[0].sort["rate"] is None


@pytest.mark.parametrize(
    "value",
    [
        dt.datetime(2026, 9, 25, 1, 2, 3),  # the builder turns time into a number
        2**53 + 1,  # JSON.parse would round it
        object(),
    ],
    ids=["datetime", "int-over-2^53", "object"],
)
def test_a_sort_value_that_is_not_a_plain_json_scalar_is_refused_by_field_name(
    tmp_path: Path, value: object
) -> None:
    """The format does not guess at pandas/numpy types (NaT, datetime64, tz
    offsets all guess wrong): the builder hands plain values or the build fails."""
    with pytest.raises(ValueError, match="'weird'"):
        _write(tmp_path / "c.vcache", [Group(("a",), {"weird": value}, [1.0])])


@pytest.mark.parametrize("key", [12, math.nan, None], ids=["int", "nan", "none"])
def test_a_key_value_that_is_not_text_is_refused(tmp_path: Path, key: object) -> None:
    """Marking values are opaque strings (Q6): a key written as 12 would never
    match the "12" a linked view compares it with."""
    with pytest.raises(ValueError, match="key"):
        _write(tmp_path / "c.vcache", [Group((key,), {}, [1.0])])  # ty: ignore[invalid-argument-type]


def test_a_continuous_value_that_is_not_a_number_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'1.5'"):
        _write(tmp_path / "c.vcache", [Group(("a",), {}, ["1.5"])])


def test_a_category_cell_pandas_left_as_nan_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path, [Group(("a",), {}, ["x", math.nan])], scale=CategoryScale(["x"]), cells=2)
    index = read_index(path)
    assert index.scale.decode(read_groups(path, index, [0])[0]) == ["x", None]


@pytest.mark.parametrize("position", [True, 1.0, "1"], ids=["bool", "float", "str"])
def test_a_position_that_is_not_an_int_is_refused(tmp_path: Path, position: object) -> None:
    path = tmp_path / "c.vcache"
    _write(path, [Group((f"g{i}",), {}, [1.0]) for i in range(3)])
    with pytest.raises(TypeError, match="position"):
        read_groups(path, read_index(path), [position])  # ty: ignore[invalid-argument-type]


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
