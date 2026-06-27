"""``box_scatter`` — one color + one x-region per group; box plot overlaid with
the per-group points. Above ``max_points`` points in a group, only that group's
**outliers** are drawn (else dense groups render to a solid blob / explode)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from pydantic import BaseModel, Field

from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt


class BoxScatterOptions(BaseModel):
    max_points: int = Field(
        1000,
        ge=1,
        description="Per group, draw all points up to this many; above it, draw only outliers.",
    )
    point_size: float = Field(14.0, gt=0, description="Marker area for the scatter points.")
    jitter: float = Field(0.18, ge=0, description="Horizontal jitter half-width for the points.")
    title: str | None = Field(None, description="Optional figure title.")


class BoxScatter(IChart):
    name = "box_scatter"
    description = (
        "Box plot with the underlying points overlaid, one color + x-region per group "
        "(group = category column, y = numeric value). Dense groups (>max_points) show "
        "only their outliers. Good for comparing a measurement across categories."
    )
    roles = (
        Role(
            "group",
            RoleKind.CATEGORY,
            required=True,
            description="category column for the x groups",
        ),
        Role(
            "y", RoleKind.NUMBER, required=True, description="numeric value column for the y axis"
        ),
    )
    Options = BoxScatterOptions

    def draw(self, df: pd.DataFrame, roles: dict, options: BoxScatterOptions) -> Figure:
        gcol, ycol = roles["group"], roles["y"]
        # Stable group order (first appearance), keeping only groups with data.
        per_group: list[tuple[str, np.ndarray]] = []
        for g in pd.unique(df[gcol]):
            if pd.isna(g):
                continue
            vals = pd.to_numeric(df.loc[df[gcol] == g, ycol], errors="coerce").dropna()
            if len(vals):
                per_group.append((str(g), vals.to_numpy(dtype=float)))
        if not per_group:
            raise ValueError(f"no numeric {ycol!r} values to plot across {gcol!r} groups")

        fig, ax = plt.subplots()
        positions = list(range(1, len(per_group) + 1))
        ax.boxplot(
            [vals for _, vals in per_group],
            positions=positions,
            showfliers=False,  # points are drawn separately (all, or outliers-only)
            widths=0.6,
        )
        cmap = plt.get_cmap("tab10")
        rng = np.random.RandomState(0)  # deterministic jitter
        for i, (label, vals) in enumerate(per_group):
            pts = vals if len(vals) <= options.max_points else _outliers(vals)
            if not len(pts):
                continue
            x = positions[i] + rng.uniform(-options.jitter, options.jitter, size=len(pts))
            ax.scatter(
                x,
                pts,
                s=options.point_size,
                color=cmap(i % 10),
                alpha=0.6,
                edgecolors="none",
                zorder=3,
            )
        ax.set_xticks(positions)
        ax.set_xticklabels([label for label, _ in per_group])
        ax.set_xlabel(str(gcol))
        ax.set_ylabel(str(ycol))
        if options.title:
            ax.set_title(options.title)
        return fig


def _outliers(vals: np.ndarray) -> np.ndarray:
    """Tukey fences: points below Q1-1.5·IQR or above Q3+1.5·IQR."""
    q1, q3 = np.percentile(vals, [25, 75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return vals[(vals < lo) | (vals > hi)]


register(BoxScatter())
