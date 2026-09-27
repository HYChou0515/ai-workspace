"""Same cells, same pixels (Q13): the facet cache's continuous record and the
chart's `q8` wire column feed the one raster core, so for the same values over
the same range they must be the same bytes. `chart_view.wire._q8` is the
oracle; the cache never reimplements it by hand and hopes."""

import base64
import math

import numpy as np
import pytest

from chart_view.facet import ContinuousScale
from chart_view.wire import _q8

CASES = {
    "levels-and-halves": [i / 2 for i in range(0, 509)],  # every level and every .5 between
    "random": [float(v) for v in np.random.default_rng(848).normal(0, 1e3, 5000)],
    "tiny-span": [1.0, 1.0 + 1e-12, 1.0 + 2e-12],
    "constant": [3.0, 3.0, 3.0],
    "with-missing": [0.0, math.nan, 5.0, math.inf, -math.inf, 10.0],
    "range-too-wide": [-1.7e308, 0.0, 1.7e308],
    "subnormal": [0.0, 5e-324, 1e-320],
}


@pytest.mark.parametrize("values", CASES.values(), ids=CASES.keys())
def test_a_continuous_record_is_byte_for_byte_the_chart_q8_column(values: list[float]) -> None:
    with np.errstate(over="ignore", invalid="ignore"):  # the oracle's own inf span
        wire = _q8(np.asarray(values, dtype=float))
    scale = ContinuousScale(wire["min"], wire["max"])
    ours = scale.encode([None if math.isnan(v) else v for v in values])
    assert ours == base64.b64decode(wire["codes"])
