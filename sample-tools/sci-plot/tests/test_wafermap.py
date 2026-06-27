"""Structural tests for wafermap + the wafer geometry helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.patches import Circle, Polygon, Rectangle

from sci_plot.charts._wafer import apply_view, grid_geometry
from sci_plot.charts.wafermap import Wafermap, WafermapOptions, _norm_and_cmap
from sci_plot.framework.style import plt


def _grid_df(n=3, value=None):
    rows = []
    for x in range(n):
        for y in range(n):
            rows.append({"die_x": x, "die_y": y, "v": (x + y) if value is None else value})
    return pd.DataFrame(rows)


def _dies(ax):
    return [p for p in ax.patches if isinstance(p, Rectangle)]


def test_draw_one_die_per_row_plus_outline():
    df = _grid_df(3)
    fig = Wafermap().draw(df, {"die_x": "die_x", "die_y": "die_y", "value": "v"}, WafermapOptions())
    ax = fig.axes[0]
    assert len(_dies(ax)) == 9
    assert sum(isinstance(p, Circle) for p in ax.patches) == 1  # wafer outline
    assert len(fig.axes) == 2  # main + colorbar
    plt.close(fig)


def test_notch_drawn_unless_none():
    df = _grid_df(2)
    roles = {"die_x": "die_x", "die_y": "die_y", "value": "v"}
    with_notch = Wafermap().draw(df, roles, WafermapOptions(notch="bottom"))
    assert any(isinstance(p, Polygon) for p in with_notch.axes[0].patches)
    plt.close(with_notch)
    without = Wafermap().draw(df, roles, WafermapOptions(notch="none"))
    assert not any(isinstance(p, Polygon) for p in without.axes[0].patches)
    plt.close(without)


def test_title_option():
    df = _grid_df(2)
    fig = Wafermap().draw(
        df, {"die_x": "die_x", "die_y": "die_y", "value": "v"}, WafermapOptions(title="Lot W25")
    )
    assert fig.axes[0].get_title() == "Lot W25"
    plt.close(fig)


def test_draw_notch_ignores_unknown_side():
    from sci_plot.charts._wafer import _draw_notch

    fig, ax = plt.subplots()
    _draw_notch(ax, (0.0, 0.0), 1.0, "diagonal")  # not a real side → no-op
    assert not ax.patches
    plt.close(fig)


def test_no_colorbar_when_disabled():
    df = _grid_df(2)
    fig = Wafermap().draw(
        df, {"die_x": "die_x", "die_y": "die_y", "value": "v"}, WafermapOptions(show_colorbar=False)
    )
    assert len(fig.axes) == 1
    plt.close(fig)


def test_partial_die_outside_circle_still_drawn():
    # A tiny wafer diameter → most die are "partial" (outside the circle) but kept.
    df = _grid_df(5)
    fig = Wafermap().draw(
        df,
        {"die_x": "die_x", "die_y": "die_y", "value": "v"},
        WafermapOptions(wafer_diameter=2.0),
    )
    assert len(_dies(fig.axes[0])) == 25  # all die kept, even partial ones
    plt.close(fig)


def test_nan_value_die_is_drawn_grey():
    df = pd.DataFrame({"die_x": [0, 1], "die_y": [0, 0], "v": [5.0, np.nan]})
    fig = Wafermap().draw(df, {"die_x": "die_x", "die_y": "die_y", "value": "v"}, WafermapOptions())
    dies = _dies(fig.axes[0])
    assert len(dies) == 2  # the NaN die is still drawn (as a no-data cell)
    plt.close(fig)


def test_missing_die_positions_dropped_and_empty_raises():
    df = pd.DataFrame({"die_x": [None, None], "die_y": [None, 1], "v": [1.0, 2.0]})
    with pytest.raises(ValueError, match="no die positions"):
        Wafermap().draw(df, {"die_x": "die_x", "die_y": "die_y", "value": "v"}, WafermapOptions())


def test_view_is_equal_aspect_and_y_inverted():
    df = _grid_df(3)
    fig = Wafermap().draw(df, {"die_x": "die_x", "die_y": "die_y", "value": "v"}, WafermapOptions())
    ax = fig.axes[0]
    assert ax.get_aspect() == 1.0  # equal
    lo, hi = ax.get_ylim()
    assert lo > hi  # inverted: row index increases downward
    plt.close(fig)


# ─── norm builder ─────────────────────────────────────────────────────


def test_uni_norm_defaults_vmin_zero():
    norm, _ = _norm_and_cmap("uni", np.array([2.0, 5.0, 8.0]), WafermapOptions())
    assert isinstance(norm, Normalize) and not isinstance(norm, TwoSlopeNorm)
    assert norm.vmin == 0.0 and norm.vmax == 8.0


def test_bi_norm_is_two_slope_about_center():
    norm, _ = _norm_and_cmap("bi", np.array([-3.0, 0.0, 4.0]), WafermapOptions(center=0.0))
    assert isinstance(norm, TwoSlopeNorm)
    assert norm.vcenter == 0.0 and norm.vmin <= -3.0 and norm.vmax >= 4.0


def test_bi_norm_straddles_center_even_if_data_one_sided():
    # all-positive data, center 0 → vmin still pushed below center so it's valid
    norm, _ = _norm_and_cmap("bi", np.array([1.0, 2.0, 3.0]), WafermapOptions(center=0.0))
    assert norm.vmin < 0.0 < norm.vmax


# ─── geometry helpers ─────────────────────────────────────────────────


def test_grid_geometry_explicit_diameter():
    xs = np.array([0.0, 4.0])
    ys = np.array([0.0, 4.0])
    (cx, cy), r = grid_geometry(xs, ys, diameter=6.0)
    assert (cx, cy) == (2.0, 2.0) and r == 3.0


def test_grid_geometry_autofit_encloses_die():
    xs = np.array([0.0, 2.0])
    ys = np.array([0.0, 0.0])
    (cx, cy), r = grid_geometry(xs, ys, diameter=None)
    assert cx == 1.0 and r > 1.0  # reaches past the far die corner


def test_apply_view_hides_ticks():
    fig, ax = plt.subplots()
    apply_view(ax, (1.0, 1.0), 2.0, np.array([0.0, 2.0]), np.array([0.0, 2.0]))
    assert ax.get_xticks().size == 0 and ax.get_yticks().size == 0
    plt.close(fig)
