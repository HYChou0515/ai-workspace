"""The format takes plain Python values only, by exact type, and says which
type it refused: a numpy scalar (a float/str subclass) is P3's to convert, and
must fail the same way whether or not it happens to be NaN."""

import math
from pathlib import Path

import pytest

from aiws_facet_cache import (
    CacheUnusable,
    CategoryScale,
    ContinuousScale,
    Group,
    read_index,
    write_cache,
)


class SubFloat(float):  # what np.float64 is to Python
    pass


class SubStr(str):  # what np.str_ is to Python
    pass


def _write(path: Path, *, key=("a",), sort=None, values=(1.0,), scale=None, facet=("g",)):
    write_cache(
        path,
        scale=scale or ContinuousScale(0.0, 1.0),
        facet=facet,
        cells=len(values),
        layout={},
        groups=[Group(key=key, sort=sort or {}, values=list(values))],
    )


@pytest.mark.parametrize("value", [SubFloat(1.5), SubFloat(math.nan)], ids=["finite", "nan"])
def test_a_float_subclass_sort_value_is_refused_nan_or_not(tmp_path: Path, value: float) -> None:
    with pytest.raises(ValueError, match="SubFloat"):
        _write(tmp_path / "c.vcache", sort={"s": value})


def test_a_sort_field_name_must_be_text(tmp_path: Path) -> None:
    """JSON keys are text: 1 and "1" would collapse into one field."""
    with pytest.raises(ValueError, match="field"):
        _write(tmp_path / "c.vcache", sort={1: "x", "1": "y"})


def test_a_key_of_a_str_subclass_is_refused_naming_its_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SubStr"):
        _write(tmp_path / "c.vcache", key=(SubStr("a"),))


def test_a_facet_column_name_must_be_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="facet"):
        _write(tmp_path / "c.vcache", facet=(1,))


@pytest.mark.parametrize("value", [SubFloat(0.5), 10**400], ids=["float-subclass", "huge-int"])
def test_a_continuous_cell_that_is_not_a_plain_finite_number_is_refused(
    tmp_path: Path, value: object
) -> None:
    with pytest.raises(ValueError, match="cell"):
        _write(tmp_path / "c.vcache", values=(value,))


def test_a_category_cell_of_a_str_subclass_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SubStr"):
        _write(tmp_path / "c.vcache", values=(SubStr("x"),), scale=CategoryScale(["x"]))


def test_category_labels_must_be_text() -> None:
    with pytest.raises(ValueError, match="label"):
        CategoryScale([1, 2])  # ty: ignore[invalid-argument-type]


def test_a_group_whose_cell_count_differs_from_the_cache_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cells"):
        write_cache(
            tmp_path / "c.vcache",
            scale=ContinuousScale(0.0, 1.0),
            facet=("g",),
            cells=2,
            layout={},
            groups=[Group(key=("a",), sort={}, values=[1.0])],
        )


def _replace_header(path: Path, old: bytes, new: bytes) -> None:
    data = path.read_bytes()
    n = int.from_bytes(data[40:44], "little")  # after magic (8) + build id (32)
    header = data[44 : 44 + n]
    assert old in header
    header = header.replace(old, new)
    path.write_bytes(data[:40] + len(header).to_bytes(4, "little") + header + data[44 + n :])


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'"lo":0.0', b'"lo":-1' + b"0" * 400),  # an int no float holds
        (b'"layout":{}', b'"layout":' + b"[" * 100_000 + b"]" * 100_000),  # nested too deep
    ],
    ids=["huge-int-range", "deep-nesting"],
)
def test_a_header_python_cannot_hold_is_unusable(tmp_path: Path, old: bytes, new: bytes) -> None:
    path = tmp_path / "c.vcache"
    _write(path)
    _replace_header(path, old, new)
    with pytest.raises(CacheUnusable):
        read_index(path)
