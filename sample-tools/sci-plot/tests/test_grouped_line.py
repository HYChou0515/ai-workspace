"""Tests for grouped_line: the |-collapse span algorithm + structural render."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sci_plot.charts.grouped_line import GroupedLine, GroupedLineOptions, level_spans, span_label
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


# ─── pure: span_label ─────────────────────────────────────────────────


def test_span_label_single_position_is_bare():
    assert span_label("W07", 3, 3) == "W07"


def test_span_label_multi_position_is_bracketed():
    assert span_label("itemA", 0, 2) == "|itemA|"


def test_span_label_nan_is_empty():
    assert span_label(float("nan"), 0, 0) == ""


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


def test_finest_level_ticks_and_coarse_group_brackets():
    df = pd.DataFrame(
        {"ts": ["t1", "t2", "t3", "t4"], "item": ["A", "A", "B", "B"], "v": [1.0, 2, 3, 4]}
    )
    fig = GroupedLine().draw(df, {"levels": ["ts", "item"], "value": "v"}, GroupedLineOptions())
    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["t1", "t2", "t3", "t4"]  # unique → bare
    texts = {t.get_text() for t in ax.texts}
    assert {"|A|", "|B|"} <= texts  # coarser groups collapse to brackets
    plt.close(fig)


def test_repeated_finest_level_gets_brackets():
    # level1 = date that repeats across items → it too collapses to |date|.
    df = pd.DataFrame(
        {"date": ["d1", "d1", "d2", "d2"], "item": ["A", "B", "C", "D"], "v": [1.0, 2, 3, 4]}
    )
    fig = GroupedLine().draw(df, {"levels": ["date", "item"], "value": "v"}, GroupedLineOptions())
    ax = fig.axes[0]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["|d1|", "|d2|"]
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
