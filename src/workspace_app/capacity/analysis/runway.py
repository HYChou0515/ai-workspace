"""When the people trend reaches a limit (docs/plan-capacity-usage.md §9.7).

A straight line through the days' N (least squares), and an 80% prediction
band around it: t · s · √(1 + 1/n + (x − x̄)² / Sxx) either side — the interval
for one future day's N, which widens the further x lies from the days the line
was fitted on (the prediction interval of a new observation, e.g. Montgomery,
Peck & Vining, *Introduction to Linear Regression Analysis*). A fixed ± around
the line said next year is as certain as next week.

The days the line, and each edge of the band, reach the limit are worked out
here once; the page only formats them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

#: The standard normal quantile with 90% below it: an 80% band's edge.
_Z90 = 1.2815515655446004


def t90(dof: int) -> float:
    """Student's t quantile with 90% below it, for `dof` degrees of freedom —
    the Cornish–Fisher expansion in the normal quantile (Abramowitz & Stegun,
    *Handbook of Mathematical Functions*, 26.7.5); scipy is not in the API
    image. Within 1e-4 of the exact value from 5 degrees of freedom on."""
    z, v = _Z90, float(dof)
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / v + g2 / v**2 + g3 / v**3 + g4 / v**4


@dataclass(frozen=True)
class Trend:
    intercept: float
    slope: float
    #: the residual standard error (n − 2 degrees of freedom)
    residual_sd: float
    n: int
    x_mean: float
    #: Σ (x − x̄)²
    sxx: float
    #: the t quantile of the 80% band
    t: float

    def half(self, x: np.ndarray) -> np.ndarray:
        """The 80% band's half-width at day `x`."""
        spread = 1.0 + 1.0 / self.n + (np.asarray(x, dtype=float) - self.x_mean) ** 2 / self.sxx
        return self.t * self.residual_sd * np.sqrt(spread)

    def json(self) -> dict[str, Any]:
        return asdict(self)


def fit_trend(x: np.ndarray, y: np.ndarray) -> Trend | None:
    """Least squares y = a + b·x. A slope within rounding of zero is zero: a
    count that never moved fits ±1e-15, which is not growth."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    if n < 2 or np.ptp(x) == 0:
        return None
    x_mean, y_mean = float(x.mean()), float(y.mean())
    sxx = float(((x - x_mean) ** 2).sum())
    slope = float(((x - x_mean) * (y - y_mean)).sum() / sxx)
    if abs(slope) * float(np.ptp(x)) <= 1e-9 * max(1.0, float(np.abs(y).max())):
        slope = 0.0
    intercept = y_mean - slope * x_mean
    resid = y - (intercept + slope * x)
    sd = float(np.sqrt((resid**2).sum() / (n - 2))) if n > 2 else 0.0
    return Trend(intercept, slope, sd, n, x_mean, sxx, t90(n - 2) if n > 2 else 0.0)


def _first(trend: Trend, today: float, target: float, side: int) -> float | None:
    """The first day from `today` on when the line (`side` 0) or the band's
    upper (+1) or lower (−1) edge reaches `target`; None when it never does."""

    def edge(x: float) -> float:
        return trend.intercept + trend.slope * x + side * float(trend.half(np.array([x]))[0])

    if edge(today) >= target:
        return today
    b = trend.slope
    r0 = target - trend.intercept
    if side == 0:
        return r0 / b
    # (r0 − b·x)² = k²·(1 + 1/n + (x − x̄)²/Sxx): the edge's equation squared
    k2 = (trend.t * trend.residual_sd) ** 2
    c = 1.0 + 1.0 / trend.n
    xm, sxx = trend.x_mean, trend.sxx
    qa = b * b - k2 / sxx
    qb = -2 * r0 * b + 2 * k2 * xm / sxx
    qc = r0 * r0 - k2 * c - k2 * xm * xm / sxx
    if abs(qa) < 1e-15:
        roots = [-qc / qb] if qb else []
    else:
        disc = qb * qb - 4 * qa * qc
        if disc < 0:
            return None
        root = math.sqrt(disc)
        roots = [(-qb - root) / (2 * qa), (-qb + root) / (2 * qa)]
    valid = [
        x
        for x in roots
        if x >= today
        # squaring admits the other edge's crossings: keep this edge's own
        and math.isclose(edge(x), target, rel_tol=1e-6, abs_tol=1e-6)
    ]
    return min(valid) if valid else None


def reach(trend: Trend, today: float, target: float) -> dict[str, float | None]:
    """Days from `today` until the band's upper edge ("early"), the line
    ("at") and its lower edge ("late") reach `target`: 0 for one already
    there, None for one that never does — and all None when the people are
    not growing."""
    if trend.slope <= 0:
        return {"early": None, "at": None, "late": None}
    out: dict[str, float | None] = {}
    for key, side in (("early", 1), ("at", 0), ("late", -1)):
        x = _first(trend, today, target, side)
        out[key] = None if x is None else x - today
    return out
