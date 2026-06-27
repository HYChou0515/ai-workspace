"""House style + figure framing + save — the framework "owns the frame".

A :class:`PlotStyle` bundles the *presentation knobs* (figsize, dpi, font size,
tick rotation, margins). Charts create their figures with bare ``plt.subplots()``
so they inherit ``figure.figsize`` / ``figure.dpi`` / ``font.size`` from the
style's rc context — meaning the same knobs the VLM review loop (Phase 7) tweaks
flow straight into the next render without changing ``IChart.draw``'s signature.
``draw`` still owns the *content* (and may override frame bits like aspect).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, replace

import matplotlib
from pydantic import BaseModel, Field

matplotlib.use("Agg")  # headless — works in the sandbox with no display

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from sci_plot.framework.chart import IChart  # noqa: E402


@dataclass(frozen=True)
class PlotStyle:
    """Presentation knobs. Defaults are the house style; the VLM adjuster
    produces tweaked copies via :func:`dataclasses.replace`."""

    figsize: tuple[float, float] = (9.0, 5.5)
    dpi: int = 110
    font_size: float = 10.0
    x_tick_rotation: float | None = None
    tight: bool = True
    pad: float = 1.2


class StyleOverride(BaseModel):
    """Presentation overrides the VLM auto-review loop tweaks between passes.
    All optional; only the set fields override the house :class:`PlotStyle`.
    These are *presentation* knobs only — never anything semantic."""

    figsize: tuple[float, float] | None = Field(None, description="(width, height) inches.")
    dpi: int | None = Field(None, description="Render DPI.")
    font_size: float | None = Field(None, description="Base font size.")
    x_tick_rotation: float | None = Field(None, description="Rotate x tick labels (degrees).")
    pad: float | None = Field(None, description="tight_layout padding.")


def merged_style(override: StyleOverride | None) -> PlotStyle:
    """House style with any set override fields applied."""
    base = PlotStyle()
    if override is None:
        return base
    changes = {k: v for k, v in override.model_dump().items() if v is not None}
    return replace(base, **changes)


@contextlib.contextmanager
def _rc(style: PlotStyle) -> Iterator[None]:
    with plt.rc_context(
        {
            "figure.figsize": list(style.figsize),
            "figure.dpi": style.dpi,
            "savefig.dpi": style.dpi,
            "font.size": style.font_size,
            "axes.grid": True,
            "grid.alpha": 0.3,
        }
    ):
        yield


def render(
    chart: IChart,
    df,
    roles: dict,
    options,
    style: PlotStyle,
) -> Figure:
    """Run ``chart.draw`` inside the style context, then apply the uniform
    post-draw framing (tick rotation, tight layout). Returns the Figure
    (caller saves + closes)."""
    with _rc(style):
        fig = chart.draw(df, roles, options)
        if style.x_tick_rotation is not None:
            for ax in fig.axes:
                for label in ax.get_xticklabels():
                    label.set_rotation(style.x_tick_rotation)
                    label.set_ha("right")
        if style.tight:
            with contextlib.suppress(Exception):
                fig.tight_layout(pad=style.pad)
    return fig


def save(fig: Figure, path: str, style: PlotStyle) -> None:
    fig.savefig(path, dpi=style.dpi, bbox_inches="tight")
    plt.close(fig)
