"""Structural tests for defectmap."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from matplotlib.collections import PathCollection
from matplotlib.patches import Circle

from sci_plot.charts.defectmap import Defectmap, DefectmapOptions
from sci_plot.framework.style import plt


def _df(n=12):
    rng = np.random.RandomState(1)
    return pd.DataFrame({"x": rng.uniform(-5, 5, n), "y": rng.uniform(-5, 5, n)})


def _scatter(ax):
    return [c for c in ax.collections if isinstance(c, PathCollection)]


def test_defects_scattered_inside_a_wafer_outline():
    df = _df(12)
    fig = Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions())
    ax = fig.axes[0]
    assert sum(isinstance(p, Circle) for p in ax.patches) == 1  # wafer outline
    sc = _scatter(ax)
    assert len(sc) == 1 and len(sc[0].get_offsets()) == 12  # one square per defect
    plt.close(fig)


def test_defect_marker_is_red_square():
    df = _df(3)
    fig = Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions())
    sc = _scatter(fig.axes[0])[0]
    # red facecolor (RGBA with R≈1, G=B≈0)
    r, g, b, _ = sc.get_facecolor()[0]
    assert r > 0.9 and g < 0.2 and b < 0.2
    plt.close(fig)


def test_die_grid_drawn_only_when_pitch_set():
    df = _df(5)
    no_grid = Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions())
    base_lines = len(no_grid.axes[0].lines)
    plt.close(no_grid)
    with_grid = Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions(die_pitch=2.0))
    assert len(with_grid.axes[0].lines) > base_lines  # grid added axvline/axhline
    plt.close(with_grid)


def test_nan_coords_dropped():
    df = pd.DataFrame({"x": [1.0, np.nan, 2.0], "y": [1.0, 2.0, np.nan]})
    fig = Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions())
    assert len(_scatter(fig.axes[0])[0].get_offsets()) == 1  # only the complete row
    plt.close(fig)


def test_explicit_wafer_center():
    df = _df(6)
    fig = Defectmap().draw(
        df, {"x": "x", "y": "y"}, DefectmapOptions(center_x=0.0, center_y=0.0, wafer_diameter=12.0)
    )
    circle = next(p for p in fig.axes[0].patches if isinstance(p, Circle))
    assert circle.center == (0.0, 0.0) and circle.radius == 6.0
    plt.close(fig)


def test_no_defects_raises():
    df = pd.DataFrame({"x": [np.nan], "y": [np.nan]})
    with pytest.raises(ValueError, match="no defect coordinates"):
        Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions())


def test_title_and_equal_aspect():
    df = _df(4)
    fig = Defectmap().draw(df, {"x": "x", "y": "y"}, DefectmapOptions(title="Defects"))
    ax = fig.axes[0]
    assert ax.get_title() == "Defects" and ax.get_aspect() == 1.0
    plt.close(fig)
