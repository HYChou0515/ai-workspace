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


# #847/#848 PR 5 P41 row 21: a stack links by its slot and colour, and a
# highlight may test its value, which is each segment's sum -- the summary
# says so, so the author reads "5 rows" as the segments they are.
ROWS = pd.DataFrame(
    {
        "item": ["r1", "r2", "r3", "r4", "r5", "r6"],
        "group": ["a", "a", "b", "b", "b", "c"],
        "region": ["n", "s", "n", "n", "s", "n"],
        "value": [4.0, 5.0, 3.0, 6.0, 1.0, 2.0],
    }
)
STACK = """\
view: chart
source: data/rows.csv
mark: {type: bar, stack: true}
encoding:
  x: {field: group, type: nominal}
  y: {field: value, type: quantitative}
  color: {field: region, type: nominal}
"""


def test_a_highlight_on_a_stacks_value_says_it_tests_each_segments_sum():
    result = check(STACK + "highlight: {where: 'value > 5'}\n", lambda _: ROWS)
    # segments: a.n 4, a.s 5, b.n 9, b.s 1, c.n 2
    assert result.summary == (
        "highlight matches 1/5 rows; on a stack, value is each segment's sum; value 1–9"
    )


def test_a_highlight_on_a_mean_stacks_value_says_each_segments_mean():
    # #847/#848 PR 5 P42 row 33: a stack whose value names its own aggregate
    # is that aggregate per segment, not a sum -- the summary names the op
    mean = STACK.replace("type: quantitative}", "type: quantitative, aggregate: mean}")
    result = check(mean + "highlight: {where: 'value > 4.2'}\n", lambda _: ROWS)
    # segments: a.n 4, a.s 5, b.n 4.5, b.s 1, c.n 2
    assert result.summary == (
        "highlight matches 2/5 rows; on a stack, value is each segment's mean; value 1–5"
    )


def test_a_highlight_on_a_stacks_slot_says_nothing_of_sums():
    result = check(STACK + "highlight: {where: \"group == 'b'\"}\n", lambda _: ROWS)
    assert result.summary == "highlight matches 2/5 rows; value 1–9"


# #847/#848 PR 5 P42 row 29 [user, 2026-09-26]: in a layered chart the stack
# rule limits the stack layer only. The stack neither writes nor lights by a
# field it does not link by (its tooltip keeps `item` where a segment's rows
# share one -- a.n is r1 alone -- and that still lights nothing), the other
# layers do, and the summary says which layer is a sum and what it links by.
LAYERED = """\
view: chart
source: data/rows.csv
layer:
  - mark: {type: bar, stack: true}
    encoding:
      x: {field: group, type: nominal}
      y: {field: value, type: quantitative}
      color: {field: region, type: nominal}
      tooltip: {field: item, type: nominal}
  - mark: scatter
    encoding:
      x: {field: group, type: nominal}
      y: {field: value, type: quantitative}
"""
SUM_NOTE = "the stack links by group, region only (each a sum)"
# the unstacked layer a mean per group: its rows have no `item`
AGGREGATED = LAYERED.split("  - mark: scatter")[0] + (
    "  - mark: line\n    encoding:\n      x: {field: group, type: nominal}\n"
    "      y: {field: value, type: quantitative, aggregate: mean}\n"
)


def test_a_layered_stack_keyed_by_a_row_field_is_summarised_with_what_it_links_by():
    result = check(LAYERED + "keys: [item]\n", lambda _: ROWS)
    assert result.errors == []
    assert result.summary == f"5 rows; {SUM_NOTE}; value 1–9"
    # two stacks that link alike are said once
    stack = LAYERED.split("  - mark: scatter")[0].split("layer:\n")[1]
    twice = LAYERED.replace("layer:\n", "layer:\n" + stack)
    assert twice.count("stack: true") == 2
    assert (
        check(twice + "keys: [item]\n", lambda _: ROWS).summary == f"5 rows; {SUM_NOTE}; value 1–9"
    )
    # a mean stack is said to be one (P42 row 33)
    mean = LAYERED.replace(
        "y: {field: value, type: quantitative}\n      color",
        "y: {field: value, type: quantitative, aggregate: mean}\n      color",
    )
    assert check(mean + "keys: [item]\n", lambda _: ROWS).summary == (
        "5 rows; the stack links by group, region only (each a mean); value 1–5"
    )


def test_a_layered_highlight_on_a_row_field_lights_only_the_unstacked_layer():
    result = check(LAYERED + "highlight: {where: \"item == 'r1'\"}\n", lambda _: ROWS)
    assert result.errors == []
    assert result.summary == f"highlight matches 1/6 rows; {SUM_NOTE}; value 1–9"


def test_a_row_field_no_unstacked_layer_has_is_refused():
    assert "aggregate: mean" in AGGREGATED and "scatter" not in AGGREGATED
    lit = check(AGGREGATED + "highlight: {where: \"item == 'r1'\"}\n", lambda _: ROWS)
    assert lit.errors == ["highlight: no layer has the columns it names (item == 'r1')"]
    keyed = check(AGGREGATED + "keys: [group, item]\n", lambda _: ROWS)
    assert keyed.errors == [
        f"keys: no layer can write 'item' — {SUM_NOTE}, and no other layer has 'item' row by row"
    ]
    # an aggregate is no row's value: the line's mean of `value` writes nothing
    measured = check(AGGREGATED + "keys: [value]\n", lambda _: ROWS)
    assert measured.errors == [
        f"keys: no layer can write 'value' — {SUM_NOTE}, and no other layer has 'value' row by row"
    ]
    assert check(LAYERED + "keys: [value]\n", lambda _: ROWS).errors == []


def test_a_row_field_only_a_binned_layer_has_is_refused():
    # a binned scatter's rows are bins: it writes no key
    over = ROWS.assign(at=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    text = LAYERED.replace("{field: group, type: nominal}", "{field: at, type: quantitative}")
    assert check(text + "keys: [item]\n", lambda _: over).errors == []
    binned = check(text + "keys: [item]\nbin_threshold: 1\n", lambda _: over)
    assert binned.errors == [
        f"keys: no layer can write 'item' — {SUM_NOTE.replace('group', 'at')}, and no other "
        "layer has 'item' row by row"
    ]
