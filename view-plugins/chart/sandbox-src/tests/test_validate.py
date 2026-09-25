"""`validate`: what `show_file` hears before it shows a chart (Q9).

A refusal is a list of lines naming what to fix; an acceptance is ONE summary
line that `show_file` appends to its reply.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chart_view.sources import SourceError
from chart_view.validate import check

WAFERS = pd.DataFrame(
    {
        "lot": ["A", "A", "B", "B", "C"],
        "wafer": [1, 2, 3, 4, 5],
        "thickness": [100.0, 101.0, 99.0, 104.0, 98.0],
        "fail_rate": [0.1, 0.4, 0.05, 0.35, 0.2],
    }
)

SCATTER = """\
view: chart
source: data/wafers.csv
mark: scatter
encoding:
  x: {field: thickness, type: quantitative}
  y: {field: fail_rate, type: quantitative}
"""


def _read(source) -> pd.DataFrame:
    assert source == "data/wafers.csv"
    return WAFERS


def test_a_good_spec_is_summarised_in_one_line():
    result = check(SCATTER + "highlight: {where: 'fail_rate > 0.3'}\n", _read)
    assert result.errors == []
    assert result.summary == "highlight matches 2/5 rows; fail_rate 0.05–0.4"


def test_without_a_highlight_the_summary_counts_rows():
    assert check(SCATTER, _read).summary == "5 rows; fail_rate 0.05–0.4"


def test_the_measure_of_a_grid_is_its_colour():
    text = """\
view: chart
source: data/wafers.csv
mark: heatmap
encoding:
  x: {field: lot, type: nominal}
  y: {field: wafer, type: ordinal}
  color: {field: thickness, type: quantitative}
"""
    assert check(text, _read).summary == "5 rows; thickness 98–104"


def test_an_aggregate_is_summarised_over_its_groups():
    text = """\
view: chart
source: data/wafers.csv
mark: bar
encoding:
  x: {field: lot, type: nominal}
  y: {field: fail_rate, type: quantitative, aggregate: mean}
highlight: {values: {lot: [B]}}
"""
    assert check(text, _read).summary == "highlight matches 1/3 rows; fail_rate 0.2–0.25"


def test_a_binned_scatter_says_so():
    big = pd.DataFrame({"thickness": [float(i) for i in range(50)], "fail_rate": [0.5] * 50})
    result = check(SCATTER + "bin_threshold: 10\n", lambda _s: big)
    assert result.summary == "50 points drawn as bins; fail_rate 0.5"


def test_a_schema_error_is_refused_with_its_key():
    result = check(SCATTER.replace("scatter", "geoshape"), _read)
    assert result.summary is None
    assert result.errors[0].startswith("mark: 'geoshape'")


def test_bad_yaml_is_refused():
    assert check("view: [chart\n", _read).errors[0].startswith("not valid YAML")


def test_an_unreadable_source_is_refused_by_name():
    def missing(source):
        raise SourceError(f"source {source!r} is not a file in the workspace")

    assert check(SCATTER, missing).errors == [
        "source 'data/wafers.csv' is not a file in the workspace"
    ]


def test_a_column_the_data_lacks_is_refused_by_name():
    [line] = check(SCATTER.replace("fail_rate", "yield"), _read).errors
    assert "'yield'" in line and "fail_rate" in line  # names it and lists what exists


@pytest.mark.parametrize(
    ("where", "count"),
    [("fail_rate > 5", "0"), ("fail_rate >= 0", "all 5")],
)
def test_a_highlight_that_lights_none_or_all_is_refused_with_the_count(where, count):
    [line] = check(SCATTER + f"highlight: {{where: '{where}'}}\n", _read).errors
    assert line.startswith("highlight: ")
    assert f"matches {count} " in line


def test_a_highlight_no_layer_can_see_is_refused():
    [line] = check(SCATTER + "highlight: {values: {operator: [x]}}\n", _read).errors
    assert "operator" in line


def test_a_highlight_that_does_not_evaluate_is_refused():
    [line] = check(SCATTER + "highlight: {where: 'fail_rate >'}\n", _read).errors
    assert line.startswith("highlight ")


def test_a_chart_of_only_datum_rules_is_refused():
    # Review round 5: no layer draws a field, so there is no axis for the
    # datum; the renderer handed ECharts the raw value with nothing to read it.
    text = """\
view: chart
source: data/wafers.csv
mark: rule
encoding:
  y: {datum: 3}
"""
    assert check(text, _read).errors == [
        "y: datum 3 has no axis to sit on — no layer draws a field there"
    ]
