"""Shared wafer geometry for wafermap + defectmap: the outline circle, the
orientation notch, and the equal-aspect view. Die positions are grid indices
(integer row/col); a die is a unit cell centered on its (x, y). The wafer is a
reference circle — die may extend past it (partial die), which is expected, so
the die layer draws on top of the outline.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.axes import Axes
from matplotlib.patches import Circle, Polygon

NotchSide = str  # "bottom" | "top" | "left" | "right" | "none"


def grid_geometry(
    xs: np.ndarray,
    ys: np.ndarray,
    diameter: float | None,
    *,
    center: tuple[float, float] | None = None,
    half_w: float = 0.5,
    half_h: float = 0.5,
) -> tuple[tuple[float, float], float]:
    """Return (center, radius) for the wafer outline, in the same (already die-
    sized) coordinates as ``xs``/``ys``. ``center`` pins the wafer centre when
    it is NOT the die bounding-box midpoint (a real wafer's centre rarely sits on
    a die centroid); default is that midpoint. With an explicit ``diameter`` the
    circle is that size (so die can poke out = partial die); otherwise it
    auto-fits to enclose every die cell (``half_w``/``half_h`` = die half-size)."""
    if center is not None:
        cx, cy = center
    else:
        cx = (float(np.min(xs)) + float(np.max(xs))) / 2
        cy = (float(np.min(ys)) + float(np.max(ys))) / 2
    if diameter is not None:
        return (cx, cy), diameter / 2
    # Auto-fit: reach the far corner of the outermost die cell (+a hair).
    far = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    return (cx, cy), far + math.hypot(half_w, half_h) + 0.05


def draw_outline(ax: Axes, center: tuple[float, float], radius: float, notch: NotchSide) -> None:
    ax.add_patch(Circle(center, radius, fill=False, edgecolor="0.3", lw=1.5, zorder=1))
    if notch and notch != "none":
        _draw_notch(ax, center, radius, notch)


def _draw_notch(ax: Axes, center: tuple[float, float], radius: float, side: NotchSide) -> None:
    cx, cy = center
    s = radius * 0.06  # notch half-width
    # (edge point, inward unit direction) per side. NB the view inverts y (row
    # increases downward, see apply_view), so the *visual* bottom is the larger
    # data-y (cy + radius) — hence bottom/top map opposite to raw data coords.
    edges = {
        "bottom": ((cx, cy + radius), (0.0, -1.0)),
        "top": ((cx, cy - radius), (0.0, 1.0)),
        "left": ((cx - radius, cy), (1.0, 0.0)),
        "right": ((cx + radius, cy), (-1.0, 0.0)),
    }
    if side not in edges:
        return
    (ex, ey), (dx, dy) = edges[side]
    # A small triangle pointing inward from the edge.
    tip = (ex + dx * 2 * s, ey + dy * 2 * s)
    # base corners are perpendicular to the inward direction
    px, py = -dy, dx
    base1 = (ex + px * s, ey + py * s)
    base2 = (ex - px * s, ey - py * s)
    ax.add_patch(
        Polygon([tip, base1, base2], closed=True, facecolor="0.3", edgecolor="0.3", zorder=2)
    )


def apply_view(
    ax: Axes,
    center: tuple[float, float],
    radius: float,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    half_w: float = 0.5,
    half_h: float = 0.5,
) -> None:
    """Equal aspect, axes off, limits covering the circle and all die. Row index
    increases downward (wafer convention: row 1 at the top)."""
    cx, cy = center
    lo_x = min(cx - radius, float(np.min(xs)) - half_w)
    hi_x = max(cx + radius, float(np.max(xs)) + half_w)
    lo_y = min(cy - radius, float(np.min(ys)) - half_h)
    hi_y = max(cy + radius, float(np.max(ys)) + half_h)
    ax.set_xlim(lo_x, hi_x)
    ax.set_ylim(hi_y, lo_y)  # inverted → row increases downward
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
