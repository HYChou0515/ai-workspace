"""`facet:` in the chart spec (plan-view-plugins-pr4 P6): one small grid map
per group, a gallery. It lives in the one schema both readers load, so the
renderer refuses what `validate` refuses, with the same sentence."""

from typing import Any

import pytest

from chart_view.spec import spec_errors

GRID = {
    "mark": "grid",
    "encoding": {
        "x": {"field": "die_x", "type": "ordinal"},
        "y": {"field": "die_y", "type": "ordinal"},
        "color": {"field": "bin", "type": "nominal"},
    },
}


def _spec(facet: Any, **drawing: Any) -> dict:
    return {"view": "chart", "source": "data/d.csv", "facet": facet, **(drawing or GRID)}


@pytest.mark.parametrize(
    "facet",
    [
        {"field": "wafer"},
        {"field": ["lot", "wafer"]},
        {"field": "wafer", "sort": {"field": "yield"}},
        {"field": "wafer", "sort": {"field": "yield", "order": "descending"}, "cache_mb": 50},
    ],
    ids=["one-column", "two-columns", "sort", "sort-order-and-cap"],
)
def test_a_facet_over_a_grid_is_a_valid_spec(facet: dict) -> None:
    assert spec_errors(_spec(facet)) == []


def test_a_grid_named_as_an_object_mark_is_a_grid_too() -> None:
    assert (
        spec_errors(_spec({"field": "w"}, mark={"type": "grid"}, encoding=GRID["encoding"])) == []
    )


def test_a_facet_over_another_mark_says_it_needs_a_grid() -> None:
    errors = spec_errors(_spec({"field": "w"}, mark="scatter", encoding=GRID["encoding"]))
    assert errors == ["mark: 'scatter' — expected grid, since facet draws a gallery of grid maps"]


def test_a_facet_over_layers_says_it_needs_a_grid() -> None:
    errors = spec_errors(_spec({"field": "w"}, layer=[GRID]))
    assert "(top level): facet draws a gallery of grid maps: it needs mark: grid" in errors


@pytest.mark.parametrize(
    ("facet", "fragment"),
    [
        ({}, "'field' is a required property"),
        ({"field": []}, "facet.field"),
        ({"field": ["w", "w"]}, "facet.field"),
        ({"field": "w", "columns": 8}, "'columns' was unexpected"),
        ({"field": "w", "sort": {"order": "descending"}}, "'field' is a required property"),
        ({"field": "w", "sort": {"field": "y", "order": "up"}}, "facet.sort.order"),
        ({"field": "w", "cache_mb": 0}, "facet.cache_mb"),
    ],
    ids=[
        "no-field",
        "no-columns",
        "a-column-twice",
        "unknown-key",
        "sort-without-field",
        "sort-order-unknown",
        "cap-zero",
    ],
)
def test_a_malformed_facet_is_refused_where_it_is_wrong(facet: dict, fragment: str) -> None:
    errors = spec_errors(_spec(facet))
    assert any(fragment in e for e in errors), errors
