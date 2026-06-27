"""``wafermap`` — a die grid inside a wafer circle, each die coloured by a value.

Two colour modes: ``uni`` (sequential, value ≥ 0 — e.g. defect counts) and
``bi`` (diverging about a center — e.g. signed measurement data). Die may extend
past the wafer circle (partial die); they draw on top of the outline. Geometry
knobs (diameter, notch, range) are Options — exact wafer specifics are
deployment-calibrated.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from pydantic import BaseModel, Field

from sci_plot.charts._wafer import apply_view, draw_outline, grid_geometry
from sci_plot.framework.chart import IChart
from sci_plot.framework.registry import register
from sci_plot.framework.roles import Role, RoleKind
from sci_plot.framework.style import plt

_EPS = 1e-9


class WafermapOptions(BaseModel):
    color_mode: Literal["uni", "bi"] = Field(
        "uni",
        description="uni: sequential, value>=0 (defect count). bi: diverging about `center` (measurement).",
    )
    colormap: str | None = Field(None, description="Override the colormap name (else viridis/RdBu_r).")
    vmin: float | None = Field(None, description="Color scale minimum (uni defaults to 0).")
    vmax: float | None = Field(None, description="Color scale maximum (defaults to the data max).")
    center: float | None = Field(None, description="bi mode center value (defaults to 0).")
    wafer_diameter: float | None = Field(
        None, description="Wafer circle diameter in die units; None auto-fits to the die."
    )
    notch: Literal["bottom", "top", "left", "right", "none"] = Field(
        "bottom", description="Orientation notch position on the wafer edge."
    )
    show_colorbar: bool = Field(True, description="Draw the value colorbar.")
    title: str | None = Field(None, description="Optional figure title.")


def _norm_and_cmap(mode: str, vals: np.ndarray, opt: WafermapOptions):
    finite = vals[np.isfinite(vals)]
    dmin = float(finite.min()) if finite.size else 0.0
    dmax = float(finite.max()) if finite.size else 1.0
    if mode == "uni":
        name = opt.colormap or "viridis"
        vmin = opt.vmin if opt.vmin is not None else 0.0
        vmax = opt.vmax if opt.vmax is not None else max(dmax, vmin + 1.0)
        norm: Normalize = Normalize(vmin=vmin, vmax=vmax)
    else:
        name = opt.colormap or "RdBu_r"
        center = opt.center if opt.center is not None else 0.0
        vmin = min(opt.vmin if opt.vmin is not None else dmin, center - _EPS)
        vmax = max(opt.vmax if opt.vmax is not None else dmax, center + _EPS)
        norm = TwoSlopeNorm(vcenter=center, vmin=vmin, vmax=vmax)
    return norm, plt.get_cmap(name)


class Wafermap(IChart):
    name = "wafermap"
    description = (
        "A wafer die map: each die (die_x, die_y grid index) coloured by a value. "
        "color_mode 'uni' for non-negative data (defect counts), 'bi' for signed "
        "measurements diverging about a center. Die outside the wafer circle (partial "
        "die) are kept."
    )
    roles = (
        Role("die_x", RoleKind.INT, required=True, description="die column/x grid index"),
        Role("die_y", RoleKind.INT, required=True, description="die row/y grid index"),
        Role("value", RoleKind.NUMBER, required=True, description="value to colour each die by"),
    )
    Options = WafermapOptions

    def draw(self, df: pd.DataFrame, roles: dict, options: WafermapOptions) -> Figure:
        xs = pd.to_numeric(df[roles["die_x"]], errors="coerce")
        ys = pd.to_numeric(df[roles["die_y"]], errors="coerce")
        vals = pd.to_numeric(df[roles["value"]], errors="coerce")
        keep = xs.notna() & ys.notna()
        xs_a = xs[keep].to_numpy(dtype=float)
        ys_a = ys[keep].to_numpy(dtype=float)
        vals_a = vals[keep].to_numpy(dtype=float)
        if xs_a.size == 0:
            raise ValueError("no die positions to plot (die_x/die_y all missing)")

        fig, ax = plt.subplots()
        norm, cmap = _norm_and_cmap(options.color_mode, vals_a, options)
        for x, y, v in zip(xs_a, ys_a, vals_a):
            color = "0.85" if not np.isfinite(v) else cmap(norm(v))
            ax.add_patch(
                Rectangle((x - 0.5, y - 0.5), 1, 1, facecolor=color, edgecolor="white", lw=0.3, zorder=3)
            )
        center, radius = grid_geometry(xs_a, ys_a, options.wafer_diameter)
        draw_outline(ax, center, radius, options.notch)
        apply_view(ax, center, radius, xs_a, ys_a)
        if options.title:
            ax.set_title(options.title)
        if options.show_colorbar:
            sm = ScalarMappable(norm=norm, cmap=cmap)
            sm.set_array([])
            fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
        return fig


register(Wafermap())
