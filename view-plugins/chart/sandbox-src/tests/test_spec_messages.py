"""What an author reads when a spec is refused.

`validate` hands these lines back to the AI that wrote the spec, so each one must
name the key and say what is wrong with it — "is not valid under any of the given
schemas" gives a small model nothing to fix.
"""

from __future__ import annotations

from chart_view.spec import parse_spec, spec_errors

BASE = "view: chart\nsource: data/a.csv\n"
ENC = "encoding:\n  x: {field: a, type: quantitative}\n  y: {field: b, type: quantitative}\n"


def _errors(text: str) -> list[str]:
    return spec_errors(parse_spec(text))


def test_an_unsupported_mark_lists_the_marks_there_are():
    [line] = _errors(BASE + "mark: geoshape\n" + ENC)
    assert line.startswith("mark: ")
    assert "'geoshape'" in line
    assert "scatter" in line and "errorbar" in line


def test_an_unknown_mark_property_is_named():
    [line] = _errors(BASE + "mark: {type: line, strokeDash: [4, 2]}\n" + ENC)
    assert "strokeDash" in line


def test_a_field_without_a_type_says_type_is_required():
    [line] = _errors(
        BASE + "mark: line\nencoding:\n  x: {field: a}\n  y: {field: b, type: nominal}\n"
    )
    assert line.startswith("encoding.x: ")
    assert "'type' is a required property" in line


def test_an_unsupported_aggregate_names_the_op():
    [line] = _errors(
        BASE + "mark: bar\nencoding:\n  x: {field: a, type: nominal}\n"
        "  y: {field: b, type: quantitative, aggregate: median}\n"
    )
    assert line.startswith("encoding.y.aggregate: ")
    assert "'median'" in line


def test_a_missing_diff_side_is_named():
    [line] = _errors(
        BASE + "mark: line\n" + ENC + "transform:\n  - diff: {by: phase, of: after}\n"
        "    aggregate: [{op: mean, field: t, as: d}]\n"
    )
    assert "'minus' is a required property" in line


def test_mark_and_layer_together_says_to_pick_one():
    [line] = _errors(
        BASE + "mark: line\n" + ENC + "layer:\n  - mark: rule\n    encoding:\n      y: {datum: 1}\n"
    )
    assert "either mark + encoding, or layer" in line


def test_a_source_that_is_not_a_table_file_says_which_files_are():
    [line] = _errors("view: chart\nsource: data/a.xlsx\nmark: line\n" + ENC)
    assert line.startswith("source: ")
    assert "not of type 'object'" not in line
    assert ".csv" in line and ".parquet" in line


def test_a_predicate_without_a_test_lists_the_tests():
    [line] = _errors(BASE + "mark: line\n" + ENC + "transform:\n  - filter: {field: a}\n")
    assert line.startswith("transform[0].filter: ")
    assert "oneOf" in line and "range" in line and "valid" in line


def test_nothing_to_draw_says_what_is_missing():
    [line] = _errors(BASE + "title: t\n")
    assert "either mark + encoding, or layer" in line


OFF_RULE = "a datum is drawn only as a rule's x or y — drop it and name a field here"


def test_a_datum_where_none_is_drawn_says_why():
    # Review round 8: the line said only "'field' is a required property", and
    # a model tried another datum. The renderer's spec.test.ts reads the same.
    area = (
        "mark: area\nencoding:\n  x: {field: a, type: quantitative}\n"
        "  y: {field: b, type: quantitative}\n  y2: {datum: 0}\n"
    )
    assert _errors(BASE + area) == [f"encoding.y2: {OFF_RULE}"]


def test_a_datum_on_a_mark_that_also_needs_the_field_says_it_once():
    grid = (
        "mark: grid\nencoding:\n  x: {datum: 1}\n  y: {field: b, type: ordinal}\n"
        "  color: {field: c, type: quantitative}\n"
    )
    lines = _errors(BASE + grid)
    assert [line for line in lines if line.startswith("encoding.x:")] == [f"encoding.x: {OFF_RULE}"]


def test_a_line_with_an_x_datum_gets_one_line_that_says_why():
    # Round 8 regression lens: markChannels and datumChannels each refused it,
    # so the model read the same key refused twice, with no reason either time.
    text = BASE + "mark: line\nencoding:\n  x: {datum: 1}\n  y: {field: b, type: quantitative}\n"
    assert _errors(text) == [f"encoding.x: {OFF_RULE}"]


def test_a_colour_datum_is_refused_too():
    # SKILL.md says a datum is a rule's x or y only; the schema took one on
    # color / size / tooltip, which the renderer never reads.
    text = BASE + "mark: scatter\n" + ENC + "  color: {datum: red}\n"
    assert _errors(text) == [f"encoding.color: {OFF_RULE}"]


def test_a_channel_with_no_field_and_no_datum_is_not_told_about_a_datum():
    # Review round 9: `x: {aggregate: count}` (a Vega-Lite habit) was told "a
    # datum is drawn only as a rule's x or y" — it holds no datum.
    text = (
        BASE
        + "mark: bar\nencoding:\n  x: {aggregate: count}\n  y: {field: b, type: quantitative}\n"
    )
    lines = _errors(text)
    assert lines and not any("datum is drawn" in line for line in lines), lines


def test_a_channel_with_a_field_and_a_datum_is_told_to_drop_the_datum():
    # Round 10: the line said "name a field here" to a channel that named one.
    text = BASE + "mark: scatter\n" + ENC + "  color: {datum: 1, field: a, type: nominal}\n"
    assert f"encoding.color: {OFF_RULE}" in _errors(text)
    assert "drop it" in OFF_RULE


def test_a_tooltip_item_that_is_no_channel_is_not_told_about_a_datum():
    # The datum rule is scoped to objects: a string item is refused for what
    # it is, not as a datum it does not hold.
    text = BASE + "mark: scatter\n" + ENC + "  tooltip: [a]\n"
    lines = _errors(text)
    assert lines and not any("datum is drawn" in line for line in lines), lines
