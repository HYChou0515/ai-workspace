"""``defectmap`` — a wafer with every defect drawn at its coordinate.

Shares the wafer outline + orientation with :mod:`sci_plot.charts._wafer`. Each
defect is a small square (red by default) at its (x, y) position; an optional
faint die grid (``die_pitch``) gives spatial reference. Unlike wafermap (one
cell per die, coloured by value), defectmap is a point map of individual
defects, so clusters and edge effects pop out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from pydantic import BaseModel, Field

from sci_plot.charts._wafer import apply_view, draw_outline, grid_geometry
from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt


class DefectmapOptions(BaseModel):
    wafer_diameter: float | None = Field(
        None, description="Wafer circle diameter (coord units); None auto-fits to the defects."
    )
    center_x: float | None = Field(
        None, description="Wafer-centre x (the wafer centre need not be the defect-cloud midpoint)."
    )
    center_y: float | None = Field(None, description="Wafer-centre y.")
    notch: str = Field("bottom", description="Orientation notch: bottom/top/left/right/none.")
    die_pitch: float | None = Field(
        None, description="If set, draw a faint die grid spaced this far apart for reference."
    )
    marker_size: float = Field(14.0, gt=0, description="Defect marker area.")
    color: str = Field("red", description="Defect marker colour.")
    title: str | None = Field(None, description="Optional figure title.")


def _draw_die_grid(ax, center, radius, pitch: float) -> None:
    cx, cy = center
    # Lines on the pitch lattice through the wafer center, spanning the circle.
    k = int(np.ceil(radius / pitch)) + 1
    for i in range(-k, k + 1):
        ax.axvline(cx + i * pitch, color="0.88", lw=0.6, zorder=0)
        ax.axhline(cy + i * pitch, color="0.88", lw=0.6, zorder=0)


class Defectmap(IChart):
    name = "defectmap"
    description = (
        "A wafer defect point map: each defect drawn as a small square at its (x, y) "
        "coordinate inside the wafer circle. Optional die grid for reference. Good for "
        "seeing spatial clusters / edge rings of defects."
    )
    roles = (
        Role("x", RoleKind.NUMBER, required=True, description="defect x coordinate"),
        Role("y", RoleKind.NUMBER, required=True, description="defect y coordinate"),
    )
    Options = DefectmapOptions

    def draw(self, df: pd.DataFrame, roles: dict, options: DefectmapOptions) -> Figure:
        xs = pd.to_numeric(df[roles["x"]], errors="coerce")
        ys = pd.to_numeric(df[roles["y"]], errors="coerce")
        keep = xs.notna() & ys.notna()
        xs_a = xs[keep].to_numpy(dtype=float)
        ys_a = ys[keep].to_numpy(dtype=float)
        if xs_a.size == 0:
            raise ValueError("no defect coordinates to plot")

        fig, ax = plt.subplots()
        ctr = (
            (options.center_x, options.center_y)
            if options.center_x is not None and options.center_y is not None
            else None
        )
        center, radius = grid_geometry(xs_a, ys_a, options.wafer_diameter, center=ctr)
        draw_outline(ax, center, radius, options.notch)
        if options.die_pitch:
            _draw_die_grid(ax, center, radius, options.die_pitch)
        ax.scatter(
            xs_a,
            ys_a,
            marker="s",
            s=options.marker_size,
            c=options.color,
            edgecolors="none",
            zorder=3,
            label="defect",
        )
        apply_view(ax, center, radius, xs_a, ys_a)
        if options.title:
            ax.set_title(options.title)
        return fig


register(Defectmap())
