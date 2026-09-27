"""Stack and diff (plan-view-plugins-pr4 P5): N groups on one lattice become
ONE lattice through the chart's own `aggregate`, and "group A minus group B"
through its `diff` -- the same `query` and the same answer format a single
`grid` uses, with nothing facet-specific in between."""

import base64

import numpy as np
import pandas as pd
import pytest

from chart_view.query import build
from chart_view.spec import spec_errors

ENC = {
    "x": {"field": "x", "type": "ordinal"},
    "y": {"field": "y", "type": "ordinal"},
    "color": {"field": "v", "type": "quantitative"},
}


@pytest.fixture
def wafers() -> pd.DataFrame:
    """Three wafers on one 2 x 2 lattice; wafer k's cell (x, y) is
    10x + y + k(1 + x), so wafers differ by a different amount per column."""
    rows = [
        {"wafer": f"W{k}", "x": x, "y": y, "v": 10.0 * x + y + k * (1 + x)}
        for k in (1, 2, 3)
        for x in (0, 1)
        for y in (0, 1)
    ]
    return pd.DataFrame(rows)


def _spec(transform: list[dict]) -> dict:
    return {
        "view": "chart",
        "source": "data/w.csv",
        "mark": "grid",
        "encoding": ENC,
        "transform": transform,
    }


def _cells(layer: dict) -> dict[tuple[float, float], float | None]:
    """(x, y) -> the colour value, decoded from the answer's q8 column."""
    cols = layer["columns"]

    def axis(col: dict) -> list:
        if col["kind"] == "f64":
            return list(np.frombuffer(base64.b64decode(col["data"]), dtype="<f8"))
        codes = np.frombuffer(base64.b64decode(col["codes"]), dtype="<u1")
        return [col["levels"][c] for c in codes]

    q8 = cols["v"]
    codes = base64.b64decode(q8["codes"])
    span = q8["max"] - q8["min"]
    values = [None if c == 255 else q8["min"] + c / 254 * span for c in codes]
    return dict(zip(zip(axis(cols["x"]), axis(cols["y"]), strict=True), values, strict=True))


def test_a_cross_group_aggregate_stacks_the_groups_into_one_lattice(
    wafers: pd.DataFrame,
) -> None:
    spec = _spec([{"aggregate": [{"op": "mean", "field": "v", "as": "v"}], "groupby": ["x", "y"]}])
    assert spec_errors(spec) == []
    [layer] = build(spec, wafers)["layers"]
    assert layer["rows"] == 4  # one lattice, not three
    # mean over k in (1, 2, 3) of 10x + y + k(1 + x) = 10x + y + 2(1 + x), to
    # within the half a q8 level the colour column is sent at
    half_level = (15.0 - 2.0) / 254 / 2
    assert _cells(layer) == pytest.approx(
        {(0, 0): 2.0, (0, 1): 3.0, (1, 0): 14.0, (1, 1): 15.0}, abs=half_level
    )


def test_a_diff_shows_group_a_minus_group_b_cell_by_cell(wafers: pd.DataFrame) -> None:
    spec = _spec(
        [
            {
                "diff": {"by": "wafer", "of": "W3", "minus": "W1"},
                "aggregate": [{"op": "mean", "field": "v", "as": "v"}],
                "groupby": ["x", "y"],
            }
        ]
    )
    assert spec_errors(spec) == []
    [layer] = build(spec, wafers)["layers"]
    assert layer["rows"] == 4
    # W3 - W1 = 2(1 + x): 2 in column 0 and 4 in column 1; the q8 range is
    # exactly [2, 4], so both ends decode exactly
    assert _cells(layer) == {(0, 0): 2.0, (0, 1): 2.0, (1, 0): 4.0, (1, 1): 4.0}
