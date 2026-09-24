import math

import pytest

from aiws_facet_cache import MISSING, CategoryScale, ContinuousScale


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


def test_more_categories_than_one_byte_holds_are_refused() -> None:
    CategoryScale(labels=[str(i) for i in range(255)])
    with pytest.raises(ValueError, match="256 categories"):
        CategoryScale(labels=[str(i) for i in range(256)])


def test_a_value_outside_the_categories_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="'scrap'"):
        CategoryScale(labels=["pass", "fail"]).encode(["pass", "scrap"])
