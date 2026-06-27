"""Structural tests for the box_scatter renderer (no pixel diffing)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sci_plot.charts.box_scatter import BoxScatter, BoxScatterOptions, _outliers
from sci_plot.framework.style import plt


def _draw(df, **opts):
    return BoxScatter().draw(df, {"group": "g", "y": "y"}, BoxScatterOptions(**opts))


def test_draw_one_axes_with_group_ticks_and_labels():
    df = pd.DataFrame({"g": ["a", "a", "b", "b"], "y": [1.0, 2.0, 3.0, 4.0]})
    fig = _draw(df)
    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["a", "b"]
    assert ax.get_xlabel() == "g" and ax.get_ylabel() == "y"
    plt.close(fig)


def test_all_points_drawn_below_threshold():
    df = pd.DataFrame({"g": ["a"] * 5, "y": [1.0, 2, 3, 4, 5]})
    fig = _draw(df, max_points=1000)
    total = sum(len(c.get_offsets()) for c in fig.axes[0].collections)
    assert total == 5
    plt.close(fig)


def test_dense_group_draws_only_outliers():
    big = [0.0] * 2000 + [1000.0]  # 2001 pts > max_points → outliers only
    small = [1.0, 2.0, 3.0]
    df = pd.DataFrame({"g": ["big"] * len(big) + ["small"] * 3, "y": big + small})
    fig = _draw(df, max_points=1000)
    total = sum(len(c.get_offsets()) for c in fig.axes[0].collections)
    assert total == 1 + 3  # big → 1 outlier, small → all 3
    plt.close(fig)


def test_title_option():
    df = pd.DataFrame({"g": ["a", "b"], "y": [1.0, 2.0]})
    fig = _draw(df, title="My chart")
    assert fig.axes[0].get_title() == "My chart"
    plt.close(fig)


def test_nan_group_is_skipped():
    df = pd.DataFrame({"g": ["a", None, "b"], "y": [1.0, 2.0, 3.0]})
    fig = _draw(df)
    assert [t.get_text() for t in fig.axes[0].get_xticklabels()] == ["a", "b"]
    plt.close(fig)


def test_dense_group_with_no_outliers_draws_no_points_for_it():
    flat = [5.0] * 1100  # >max_points, identical → zero outliers
    ok = [1.0, 2.0, 3.0]
    df = pd.DataFrame({"g": ["flat"] * 1100 + ["ok"] * 3, "y": flat + ok})
    fig = _draw(df, max_points=1000)
    total = sum(len(c.get_offsets()) for c in fig.axes[0].collections)
    assert total == 3  # flat → 0, ok → 3
    plt.close(fig)


def test_no_numeric_values_raises():
    df = pd.DataFrame({"g": ["a", "b"], "y": ["x", "z"]})
    with pytest.raises(ValueError, match="no numeric"):
        _draw(df)


def test_outliers_helper():
    vals = np.array([0.0] * 100 + [50.0])
    assert _outliers(vals).tolist() == [50.0]


def test_chart_contract_surface():
    c = BoxScatter()
    assert c.name == "box_scatter" and c.roles and c.Options is BoxScatterOptions
