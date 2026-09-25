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
