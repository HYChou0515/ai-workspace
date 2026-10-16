"""The load at which things slow down (docs/plan-capacity-usage.md §9.8).

A continuous two-segment line, y = a + b·x + c·max(0, x − k), fitted with the
break k searched over x's 10th–99th percentiles (segmented regression, Muggeo,
*Statistics in Medicine* 22, 2003), then refined between the best percentile's
two neighbours. The refinement is not polish: load that comes in two clouds (a
working day, another system's weekend batch) has no percentile in the gap
between them, the break can lie there, and the gap's two edges are neighbours.
A break counts only when it is real:

- the second segment is steeper, and the two segments cut the squared error of
  one straight line by at least `MIN_GAIN`;
- at least `MIN_BEYOND` points lie past the break;
- it survives losing the `MIN_BEYOND` highest-load points. A break drawn by a
  handful of far points, past the edge of a dense cloud that never rose, is a
  guess; one the data really shows is still there without them.

Otherwise there is no knee: the load never reached one.

The search reaches the 99th percentile because a knee is a peak-hours thing:
in a month of 5-minute slots the busiest 1% is still some eighty slots.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_GAIN = 0.2
MIN_BEYOND = 30
_GRID = 60


@dataclass(frozen=True)
class Knee:
    at: float
    slope_before: float
    slope_after: float
    gain: float


def _sse(design: np.ndarray, y: np.ndarray) -> tuple[float, np.ndarray]:
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    return float(resid @ resid), coef


def _best(
    x: np.ndarray, y: np.ndarray, ones: np.ndarray, grid: np.ndarray
) -> tuple[float, float, np.ndarray] | None:
    """The break in `grid` with the least squared error and `MIN_BEYOND`
    points past it."""
    best: tuple[float, float, np.ndarray] | None = None
    for k in grid:
        if int((x > k).sum()) < MIN_BEYOND:
            continue
        sse, coef = _sse(np.column_stack([ones, x, np.maximum(0.0, x - k)]), y)
        if best is None or sse < best[0]:
            best = (sse, float(k), coef)
    return best


def _fit(x: np.ndarray, y: np.ndarray) -> Knee | None:
    if len(x) < 2 * MIN_BEYOND:
        return None
    ones = np.ones_like(x)
    line, _ = _sse(np.column_stack([ones, x]), y)
    if line <= 0:
        return None
    grid = np.unique(np.quantile(x, np.linspace(0.10, 0.99, _GRID)))
    best = _best(x, y, ones, grid)
    if best is None:
        return None
    at = int(np.searchsorted(grid, best[1]))
    fine = np.linspace(grid[max(0, at - 1)], grid[min(len(grid) - 1, at + 1)], _GRID)
    best = min(best, _best(x, y, ones, fine) or best, key=lambda b: b[0])
    sse, k, coef = best
    gain = 1.0 - sse / line
    if coef[2] <= 0 or gain < MIN_GAIN:
        return None
    return Knee(at=k, slope_before=float(coef[1]), slope_after=float(coef[1] + coef[2]), gain=gain)


def fit_knee(x: np.ndarray, y: np.ndarray) -> Knee | None:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    knee = _fit(x, y)
    if knee is None:
        return None
    keep = np.argsort(x)[:-MIN_BEYOND]
    if _fit(x[keep], y[keep]) is None:
        return None
    return knee
