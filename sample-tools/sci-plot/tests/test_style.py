"""Tests for the style/frame wrapper: rc-driven figsize, post-draw rotation, save."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
from matplotlib.figure import Figure

from sci_plot.framework.chart import IChart
from sci_plot.framework.style import PlotStyle, plt, render, save


class _FakeChart(IChart):
    name = "fake"
    description = "fake"

    def draw(self, df, roles, options) -> Figure:
        fig, ax = plt.subplots()  # bare → inherits rc figsize/dpi
        ax.plot([0, 1, 2], [0, 1, 2])
        ax.set_xticks([0, 1, 2])
        ax.set_xticklabels(["aa", "bb", "cc"])
        return fig


def test_render_inherits_rc_figsize():
    style = PlotStyle(figsize=(7.0, 3.0))
    fig = render(_FakeChart(), pd.DataFrame(), {}, None, style)
    assert tuple(fig.get_size_inches()) == (7.0, 3.0)
    plt.close(fig)


def test_render_applies_x_tick_rotation():
    style = replace(PlotStyle(), x_tick_rotation=45)
    fig = render(_FakeChart(), pd.DataFrame(), {}, None, style)
    rotations = {lbl.get_rotation() for lbl in fig.axes[0].get_xticklabels()}
    assert rotations == {45.0}
    plt.close(fig)


def test_save_writes_png(tmp_path: Path):
    style = PlotStyle()
    fig = render(_FakeChart(), pd.DataFrame(), {}, None, style)
    out = tmp_path / "x.png"
    save(fig, str(out), style)
    assert out.exists() and out.stat().st_size > 0


def test_default_style_values():
    s = PlotStyle()
    assert s.dpi == 110 and s.x_tick_rotation is None and s.tight is True


def test_merged_style_none_is_house_default():
    from sci_plot.framework.style import merged_style

    assert merged_style(None) == PlotStyle()


def test_merged_style_applies_only_set_fields():
    from sci_plot.framework.style import StyleOverride, merged_style

    s = merged_style(StyleOverride(dpi=200, x_tick_rotation=45))
    assert s.dpi == 200 and s.x_tick_rotation == 45.0
    assert s.figsize == PlotStyle().figsize  # unset field keeps the house default
