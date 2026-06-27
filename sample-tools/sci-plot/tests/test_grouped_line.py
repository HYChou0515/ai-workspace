"""Tests for grouped_line: the |-collapse span algorithm + structural render."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sci_plot.charts.grouped_line import (
    GroupedLine,
    GroupedLineOptions,
    _level_vertical,
    label_text,
    level_spans,
    span_bounds,
)
from sci_plot.framework.style import plt

# ─── pure: level_spans ────────────────────────────────────────────────


def test_level_spans_collapses_consecutive_runs():
    assert level_spans(["a", "a", "b", "a", "a", "a"]) == [
        (0, 1, "a"),
        (2, 2, "b"),
        (3, 5, "a"),
    ]


def test_level_spans_all_unique():
    assert level_spans([1, 2, 3]) == [(0, 0, 1), (1, 1, 2), (2, 2, 3)]


def test_level_spans_nan_run_collapses():
    spans = level_spans([np.nan, np.nan, 1.0])
    assert spans[0][:2] == (0, 1) and spans[1] == (2, 2, 1.0)


def test_level_spans_empty():
    assert level_spans([]) == []


# ─── pure: label_text + span_bounds ───────────────────────────────────


def test_label_text_value_and_nan():
    assert label_text("W07") == "W07"
    assert label_text(float("nan")) == "" and label_text(None) == ""


def test_span_bounds_are_cell_edges():
    # a span over positions 0..2 runs from -0.5 to 2.5 (unit cells)
    assert span_bounds(0, 2) == (-0.5, 2.5)
    assert span_bounds(3, 3) == (2.5, 3.5)


# ─── structural: draw ─────────────────────────────────────────────────


def test_single_line_when_no_line_level():
    df = pd.DataFrame({"x": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]})
    fig = GroupedLine().draw(df, {"levels": ["x"], "value": "v"}, GroupedLineOptions())
    assert len(fig.axes[0].lines) == 1
    plt.close(fig)


def test_line_level_splits_into_coloured_lines():
    df = pd.DataFrame(
        {"ts": ["t1", "t2", "t3", "t4"], "item": ["A", "A", "B", "B"], "v": [1.0, 2, 3, 4]}
    )
    fig = GroupedLine().draw(
        df, {"levels": ["ts", "item"], "value": "v"}, GroupedLineOptions(line_level="item")
    )
    ax = fig.axes[0]
    _, labels = ax.get_legend_handles_labels()
    assert set(labels) == {"A", "B"}  # one line per item run
    plt.close(fig)


def test_finest_bare_and_coarse_group_brackets():
    df = pd.DataFrame(
        {"ts": ["t1", "t2", "t3", "t4"], "item": ["A", "A", "B", "B"], "v": [1.0, 2, 3, 4]}
    )
    fig = GroupedLine().draw(df, {"levels": ["ts", "item"], "value": "v"}, GroupedLineOptions())
    ax = fig.axes[0]
    # labels are drawn below the axis, not as tick labels (ticks carry no text)
    assert all(t.get_text() == "" for t in ax.get_xticklabels())
    texts = {t.get_text() for t in ax.texts}
    assert {"t1", "t2", "t3", "t4", "A", "B"} <= texts  # bare names (no | in the text)
    assert not any("|" in t for t in texts)  # pipes are drawn boundaries, not characters
    # A/B each span 2 positions → a bracket (3 lines: 2 caps + connector) each.
    assert len(ax.lines) >= 1 + 2 * 3  # 1 data line + brackets for A and B
    plt.close(fig)


def test_group_bracket_pipes_sit_on_boundaries():
    # item A spans positions 0..1 → its bracket runs from -0.5 to 1.5; B 2..3 → 1.5..3.5.
    df = pd.DataFrame(
        {"ts": ["t1", "t2", "t3", "t4"], "item": ["A", "A", "B", "B"], "v": [1, 2, 3, 4]}
    )
    fig = GroupedLine().draw(df, {"levels": ["ts", "item"], "value": "v"}, GroupedLineOptions())
    ax = fig.axes[0]
    # the bracket cap x-positions (vertical lines, both endpoints equal x)
    caps = {
        round(ln.get_xdata()[0], 2) for ln in ax.lines if ln.get_xdata()[0] == ln.get_xdata()[1]
    }
    assert {-0.5, 1.5, 3.5} <= caps  # A: -0.5/1.5, B: 1.5/3.5
    plt.close(fig)


def test_repeated_finest_level_is_bracketed_too():
    # level1 = date that repeats across items → its bracket spans those positions.
    df = pd.DataFrame(
        {"date": ["d1", "d1", "d2", "d2"], "item": ["A", "B", "C", "D"], "v": [1.0, 2, 3, 4]}
    )
    fig = GroupedLine().draw(df, {"levels": ["date", "item"], "value": "v"}, GroupedLineOptions())
    texts = {t.get_text() for t in fig.axes[0].texts}
    assert {"d1", "d2"} <= texts and not any("|" in t for t in texts)
    plt.close(fig)


# ─── label orientation (vertical when crowded) ────────────────────────


def test_level_vertical_decision():
    spans = level_spans(["LONG-GROUP-NAME", "LONG-GROUP-NAME"])  # one wide group
    # forced modes ignore the geometry
    assert _level_vertical(spans, 1.0, 0.07, "vertical") is True
    assert _level_vertical(spans, 0.001, 0.07, "horizontal") is False
    # auto: a long name in a narrow span → vertical; roomy → horizontal
    assert _level_vertical(spans, 0.05, 0.07, "auto") is True
    assert _level_vertical(spans, 10.0, 0.07, "auto") is False


def test_auto_rotates_long_crowded_names_vertical():
    names = [f"EQUIP-LONGNAME-{i:02d}" for i in range(20)]
    df = pd.DataFrame({"eq": names, "v": list(range(20))})
    fig = GroupedLine().draw(df, {"levels": ["eq"], "value": "v"}, GroupedLineOptions())
    rotations = {t.get_rotation() for t in fig.axes[0].texts}
    assert 270.0 in rotations  # rotated to read top-to-bottom when they can't fit flat
    plt.close(fig)


def test_orientation_horizontal_keeps_long_names_flat():
    names = [f"EQUIP-LONGNAME-{i:02d}" for i in range(20)]
    df = pd.DataFrame({"eq": names, "v": list(range(20))})
    fig = GroupedLine().draw(
        df, {"levels": ["eq"], "value": "v"}, GroupedLineOptions(label_orientation="horizontal")
    )
    assert all(t.get_rotation() == 0.0 for t in fig.axes[0].texts)
    plt.close(fig)


def test_orientation_vertical_forces_rotation_even_when_short():
    df = pd.DataFrame({"x": ["a", "b", "c"], "v": [1.0, 2.0, 3.0]})
    fig = GroupedLine().draw(
        df, {"levels": ["x"], "value": "v"}, GroupedLineOptions(label_orientation="vertical")
    )
    assert {t.get_rotation() for t in fig.axes[0].texts} == {270.0}
    plt.close(fig)


def test_line_level_not_in_levels_raises():
    df = pd.DataFrame({"x": ["a", "b"], "v": [1.0, 2.0]})
    with pytest.raises(ValueError, match="not one of the levels"):
        GroupedLine().draw(
            df, {"levels": ["x"], "value": "v"}, GroupedLineOptions(line_level="nope")
        )


def test_empty_rows_raises():
    df = pd.DataFrame({"x": [], "v": []})
    with pytest.raises(ValueError, match="no rows"):
        GroupedLine().draw(df, {"levels": ["x"], "value": "v"}, GroupedLineOptions())


def test_title_and_marker_options():
    df = pd.DataFrame({"x": ["a", "b"], "v": [1.0, 2.0]})
    fig = GroupedLine().draw(
        df, {"levels": ["x"], "value": "v"}, GroupedLineOptions(title="Trend", marker="")
    )
    assert fig.axes[0].get_title() == "Trend"
    plt.close(fig)
