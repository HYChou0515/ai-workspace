"""When the people trend reaches a limit (docs/plan-capacity-usage.md §9.7):
a straight line through the days' N, and an 80% prediction band that widens
the further ahead it looks — a month of history says less about next year
than about next week."""

from __future__ import annotations

import numpy as np
import pytest

from workspace_app.capacity.analysis.runway import fit_trend, reach, t90


def _grid_crossing(trend, today: float, target: float, side: int) -> float | None:  # noqa: ANN001
    """The oracle: walk forward a thousandth of a day at a time until the
    band's `side` edge (+1 upper, −1 lower, 0 the line) reaches `target`."""
    u = np.arange(today, today + 3000, 0.001)
    edge = trend.intercept + trend.slope * u + side * trend.half(u)
    hit = np.flatnonzero(edge >= target)
    return float(u[hit[0]] - today) if len(hit) else None


def _people(days: int, slope: float, spread: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = np.arange(days, dtype=float)
    return x, 40 + slope * x + rng.normal(0, spread, days)


@pytest.mark.parametrize(
    ("slope", "spread", "target"), [(0.5, 2.0, 80), (0.2, 3.0, 70), (1.0, 0.5, 200)]
)
def test_the_band_reaches_the_limit_where_its_edges_do(slope: float, spread: float, target: float):
    x, n = _people(40, slope, spread)
    trend = fit_trend(x, n)
    assert trend is not None
    today = x[-1]

    got = reach(trend, today, target)

    for key, side in (("early", 1), ("at", 0), ("late", -1)):
        want = _grid_crossing(trend, today, target, side)
        if want is None:
            assert got[key] is None, key
        else:
            assert got[key] == pytest.approx(want, abs=0.01), key


def test_the_band_widens_the_further_ahead_it_looks():
    """A fixed ± around the line said next year is as certain as next week."""
    x, n = _people(40, 0.5, 2.0)
    trend = fit_trend(x, n)
    assert trend is not None

    near = trend.half(np.array([x[-1] + 7]))[0]
    far = trend.half(np.array([x[-1] + 365]))[0]

    assert far > 3 * near


def test_a_band_that_outgrows_the_trend_never_says_how_late():
    """When the uncertainty widens faster than the people grow, the slow end
    of the band never reaches the limit: it has no date, not a far one."""
    x, n = _people(30, 0.05, 6.0, seed=9)
    trend = fit_trend(x, n)
    assert trend is not None
    assert trend.slope**2 < (trend.t * trend.residual_sd) ** 2 / trend.sxx  # it outgrows

    got = reach(trend, x[-1], 60)

    assert got["late"] is None
    assert got["early"] == pytest.approx(_grid_crossing(trend, x[-1], 60, 1), abs=0.01)
    assert got["at"] == pytest.approx(_grid_crossing(trend, x[-1], 60, 0), abs=0.01)


def test_a_flat_count_has_no_slope_at_all():
    """A count that never moved fits a slope of ±1e-15, which read as growth
    and dated the limit thousands of millennia away (an invalid date)."""
    x = np.arange(30, dtype=float)

    trend = fit_trend(x, np.full(30, 37.0))

    assert trend is not None and trend.slope == 0.0
    assert reach(trend, x[-1], 50) == {"early": None, "at": None, "late": None}


@pytest.mark.parametrize("dof", [5, 10, 26, 60, 400])
def test_t90_is_the_t_quantile(dof: int):
    stats = pytest.importorskip("scipy.stats")
    assert t90(dof) == pytest.approx(stats.t.ppf(0.9, dof), abs=2e-3)


def test_a_band_of_no_width_reaches_the_limit_with_the_line():
    """N that grew by exactly one a day has a band of no width; rounding put
    its one root a hair below zero, the band's dates went missing, and the
    page dated the order from today: order now."""
    x = np.array(
        [2, 5, 6, 8, 10, 11, 12, 15, 16, 21, 23, 24, 27, 32, 33, 34, 39, 42, 43, 44]
        + [45, 46, 49, 50, 52, 54, 56, 57, 59],
        dtype=float,
    )
    trend = fit_trend(x, 20 + x)
    assert trend is not None and trend.residual_sd < 1e-12

    got = reach(trend, 59.0, 119.0)

    assert got["at"] == pytest.approx(40.0)
    assert got["early"] == pytest.approx(40.0, abs=1e-6)
    assert got["late"] == pytest.approx(40.0, abs=1e-6)
