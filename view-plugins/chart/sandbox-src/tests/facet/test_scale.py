import math

import pytest

from chart_view.facet import MISSING, CategoryScale, ContinuousScale


def test_nan_is_a_missing_cell_like_none() -> None:
    """pandas hands the builder NaN for a cell a group has no row for."""
    assert ContinuousScale(0.0, 1.0).encode([math.nan, None]) == bytes([MISSING, MISSING])


def test_values_outside_the_range_clamp_to_its_ends_never_to_missing() -> None:
    scale = ContinuousScale(0.0, 1.0)
    assert scale.decode(scale.encode([-5.0, 7.0])) == [0.0, 1.0]


def test_a_constant_range_decodes_every_present_cell_to_that_constant() -> None:
    scale = ContinuousScale(3.0, 3.0)
    assert scale.decode(scale.encode([3.0, None, 3.0])) == [3.0, None, 3.0]


def test_the_ends_of_the_range_decode_exactly() -> None:
    scale = ContinuousScale(-2.5, 7.5)
    assert scale.decode(scale.encode([-2.5, 7.5])) == [-2.5, 7.5]


def test_a_range_too_wide_for_a_float_codes_every_cell_0_as_q8_does() -> None:
    """hi - lo overflows to inf; PR 2's _q8 then codes every present cell 0."""
    scale = ContinuousScale(-1.7e308, 1.7e308)
    assert scale.encode([-1.7e308, 0.0, 1.7e308, None]) == bytes([0, 0, 0, MISSING])


def test_infinite_values_are_missing_as_in_the_chart_q8_wire() -> None:
    """PR 2's `q8` (chart_view/wire.py `_q8`) codes a non-finite value 255, and
    the one raster core paints both, so a thumbnail must too: same cells, same
    pixels (Q13)."""
    scale = ContinuousScale(0.0, 1.0)
    assert scale.encode([-math.inf, math.inf]) == bytes([MISSING, MISSING])


@pytest.mark.parametrize(
    ("lo", "hi"),
    [(math.nan, 1.0), (0.0, math.inf), (-math.inf, 0.0), (2.0, 1.0)],
    ids=["nan", "inf", "-inf", "reversed"],
)
def test_a_range_that_is_not_finite_and_ordered_is_refused(lo: float, hi: float) -> None:
    """Every cell missing leaves the builder a NaN range; that is its bug to
    handle, never a header a browser cannot parse."""
    with pytest.raises(ValueError, match="range"):
        ContinuousScale(lo, hi)


def test_more_categories_than_one_byte_holds_are_refused() -> None:
    CategoryScale(labels=[str(i) for i in range(255)])
    with pytest.raises(ValueError, match="256 categories"):
        CategoryScale(labels=[str(i) for i in range(256)])


def test_a_value_outside_the_categories_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="'scrap'"):
        CategoryScale(labels=["pass", "fail"]).encode(["pass", "scrap"])
