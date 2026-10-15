"""The two estimators the analysis rests on (docs/plan-capacity-usage.md §9.3,
§9.8): non-negative least squares for per-kind costs, and a one-break hinge for
the load at which things slow down — each against data whose answer is known."""

from __future__ import annotations

import numpy as np
import pytest

from workspace_app.capacity.analysis.knee import fit_knee
from workspace_app.capacity.analysis.nnls import nnls


def test_nnls_recovers_known_costs_from_counts():
    rng = np.random.default_rng(1)
    x = rng.poisson(20, (500, 3)).astype(float)
    truth = np.array([0.5, 2.0, 0.1])
    y = x @ truth + rng.normal(0, 0.5, 500)

    coef = nnls(x, y)

    assert np.allclose(coef, truth, rtol=0.05)


def test_nnls_never_returns_a_negative_cost():
    """Ordinary least squares would give the unused column a negative cost to
    soak up noise; a request cannot give CPU back."""
    rng = np.random.default_rng(2)
    x = rng.poisson(10, (300, 2)).astype(float)
    y = 3.0 * x[:, 0] - 0.0 * x[:, 1] + rng.normal(0, 2.0, 300)

    coef = nnls(x, y)

    assert (coef >= 0).all()
    assert coef[0] == pytest.approx(3.0, rel=0.05)


def test_nnls_handles_a_column_that_never_moves():
    x = np.column_stack([np.arange(50, dtype=float), np.zeros(50)])
    y = 2.0 * x[:, 0]

    coef = nnls(x, y)

    assert coef[0] == pytest.approx(2.0)
    assert coef[1] == 0.0


def test_a_hinge_is_found_where_it_was_put():
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 1, 2000)
    y = 100 + 20 * x + 900 * np.maximum(0, x - 0.6) + rng.normal(0, 10, 2000)

    knee = fit_knee(x, y)

    assert knee is not None
    assert knee.at == pytest.approx(0.6, abs=0.03)
    assert knee.slope_after > knee.slope_before


def test_a_straight_line_has_no_knee():
    rng = np.random.default_rng(4)
    x = rng.uniform(0, 1, 2000)
    y = 100 + 50 * x + rng.normal(0, 10, 2000)

    assert fit_knee(x, y) is None


def test_too_few_points_beyond_the_break_is_no_knee():
    """A break with a handful of points past it is a guess, not a measurement."""
    rng = np.random.default_rng(5)
    x = np.concatenate([rng.uniform(0, 0.5, 2000), rng.uniform(0.9, 1.0, 10)])
    y = 100 + 900 * np.maximum(0, x - 0.8)

    assert fit_knee(x, y) is None
