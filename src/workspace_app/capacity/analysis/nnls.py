"""Non-negative least squares on cross-products (docs/plan-capacity-usage.md §9.3).

A request's CPU cost cannot be negative, so the utilization-law regression
solves min ‖Xc − y‖ subject to c ≥ 0 — Lawson & Hanson's active-set method
(*Solving Least Squares Problems*, 1974, ch. 23) in the form of Bro & De Jong's
fast NNLS (*Journal of Chemometrics* 11, 1997), which needs only XᵀX and Xᵀy.
The regression's rows are pods × slots — tens of thousands — and its columns
grow with every pod a rollout names (one intercept each): the cross-products
are columns², whatever the rows, and the rows are never laid out all at once. On
numpy alone: scipy is only in the `process-sandbox` extra, which the API image
does not install.

Columns are scaled to unit norm first so a column counted in thousands and one
counted in tens converge alike.
"""

from __future__ import annotations

import numpy as np


def nnls_normal(g: np.ndarray, b: np.ndarray, *, max_iter: int | None = None) -> np.ndarray:
    """c ≥ 0 minimising ‖X c − y‖₂, given g = XᵀX and b = Xᵀy. A column of
    zeros gets 0."""
    g = np.asarray(g, dtype=float)
    b = np.asarray(b, dtype=float)
    n = len(b)
    norms = np.sqrt(np.clip(np.diag(g), 0.0, None))
    live = norms > 0
    inv = np.zeros(n)
    inv[live] = 1.0 / norms[live]
    gs = g * inv[:, None] * inv[None, :]
    bs = b * inv
    coef = np.zeros(n)
    passive = np.zeros(n, dtype=bool)
    limit = max_iter if max_iter is not None else 3 * n
    tol = 1e-10 * max(1.0, float(np.abs(bs).max(initial=0.0)))
    for _ in range(limit):
        w = bs - gs @ coef
        candidates = ~passive & live & (w > tol)
        if not candidates.any():
            break
        passive[int(np.argmax(np.where(candidates, w, -np.inf)))] = True
        while True:
            z = np.zeros(n)
            z[passive] = np.linalg.lstsq(gs[np.ix_(passive, passive)], bs[passive], rcond=None)[0]
            if (z[passive] > 0).all():
                coef = z
                break
            neg = passive & (z <= 0)
            alpha = np.min(coef[neg] / (coef[neg] - z[neg]))
            coef = coef + alpha * (z - coef)
            passive &= coef > tol
            coef[~passive] = 0.0
    return coef * inv


def nnls(x: np.ndarray, y: np.ndarray, *, max_iter: int | None = None) -> np.ndarray:
    """c ≥ 0 minimising ‖x c − y‖₂. A column of zeros gets 0."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    return nnls_normal(x.T @ x, x.T @ y, max_iter=max_iter)
