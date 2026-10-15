"""Non-negative least squares (Lawson & Hanson, *Solving Least Squares
Problems*, 1974, ch. 23), on numpy alone: scipy is only in the
`process-sandbox` extra, which the API image does not install.

A request's CPU cost cannot be negative, so the utilization-law regression
(docs/plan-capacity-usage.md §9.3) solves min ‖Xc − y‖ subject to c ≥ 0.
Columns are scaled to unit norm first so a column counted in thousands and
one counted in tens converge alike.
"""

from __future__ import annotations

import numpy as np


def nnls(x: np.ndarray, y: np.ndarray, *, max_iter: int | None = None) -> np.ndarray:
    """c ≥ 0 minimising ‖x c − y‖₂. A column of zeros gets 0."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.shape[1]
    norms = np.linalg.norm(x, axis=0)
    live = norms > 0
    scaled = np.zeros_like(x)
    scaled[:, live] = x[:, live] / norms[live]
    coef = np.zeros(n)
    passive = np.zeros(n, dtype=bool)
    limit = max_iter if max_iter is not None else 3 * n
    tol = 1e-10 * max(1.0, float(np.abs(scaled.T @ y).max(initial=0.0)))
    for _ in range(limit):
        w = scaled.T @ (y - scaled @ coef)
        candidates = ~passive & live & (w > tol)
        if not candidates.any():
            break
        passive[int(np.argmax(np.where(candidates, w, -np.inf)))] = True
        while True:
            z = np.zeros(n)
            z[passive] = np.linalg.lstsq(scaled[:, passive], y, rcond=None)[0]
            if (z[passive] > 0).all():
                coef = z
                break
            neg = passive & (z <= 0)
            alpha = np.min(coef[neg] / (coef[neg] - z[neg]))
            coef = coef + alpha * (z - coef)
            passive &= coef > tol
            coef[~passive] = 0.0
    out = np.zeros(n)
    out[live] = coef[live] / norms[live]
    return out
