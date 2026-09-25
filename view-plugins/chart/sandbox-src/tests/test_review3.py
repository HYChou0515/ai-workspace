"""Review round 3 findings on the sandbox half, each with the input that
reproduced it."""

from __future__ import annotations

import base64
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chart_view.cli import main
from chart_view.query import build
from chart_view.spec import parse_spec
from chart_view.transforms import TransformError, apply_transforms
from chart_view.wire import canon, encode_column, epoch_ms


def _f64s(wire: dict) -> list[float]:
    return np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8").tolist()


def _ms(y: int, m: int, d: int) -> float:
    return (dt.datetime(y, m, d, tzinfo=dt.UTC) - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)) / (
        dt.timedelta(milliseconds=1)
    )


@pytest.mark.parametrize("links", [{"loop": "loop"}, {"a": "b", "b": "a"}])
def test_a_loop_of_links_in_a_source_is_a_refusal(tmp_path: Path, monkeypatch, capsys, links):
    # On 3.12 (the interpreter the bundle carries) `resolve()` raises
    # RuntimeError for a loop, not OSError; the CLI died with a traceback.
    for name, target in links.items():
        (tmp_path / name).symlink_to(target)
    first = next(iter(links))
    (tmp_path / "v.chart.yaml").write_text(
        f"view: chart\nsource: {first}/x.csv\nmark: scatter\nencoding:\n"
        "  x: {field: a, type: quantitative}\n  y: {field: b, type: quantitative}\n"
    )
    monkeypatch.chdir(tmp_path)
    assert main(["validate", json.dumps({"path": "v.chart.yaml"})]) == 2
    assert "not a usable path" in capsys.readouterr().err


def test_numbers_written_as_text_are_milliseconds_whatever_they_look_like():
    # "2000" was the year 2000 while "1000" was 1000 ms, in one column; and
    # "-5" / "1.5" were gaps. The renderer reads digits as ms, always.
    s = pd.Series(["1000", "2000", "20240301", " 7 ", "-5", "1.5"], dtype=object)
    assert epoch_ms(s).tolist() == [1000.0, 2000.0, 20240301.0, 7.0, -5.0, 1.5]


@pytest.mark.parametrize(
    "column",
    [
        pd.Series(["2300-01-01", "9999-12-31", "0999-01-01"], dtype=object),
        pd.Series([dt.date(2300, 1, 1), dt.date(9999, 12, 31), dt.date(999, 1, 1)], dtype=object),
        pd.Series(np.array(["2300-01-01", "9999-12-31", "0999-01-01"], dtype="datetime64[s]")),
    ],
    ids=["text", "dates", "datetime64[s]"],
)
def test_a_date_past_the_nanosecond_range_is_a_date_not_a_gap(column):
    # An open-ended validity ("valid to 9999-12-31") vanished from the chart.
    got = _f64s(encode_column(column, "time"))
    assert got == [_ms(2300, 1, 1), _ms(9999, 12, 31), _ms(999, 1, 1)]


def test_a_zoned_column_past_the_nanosecond_range_keeps_its_zone():
    s = pd.Series(["2300-01-01T08:00:00+08:00", "2024-03-01T00:00:00Z"], dtype=object)
    assert epoch_ms(s).tolist() == [_ms(2300, 1, 1), _ms(2024, 3, 1)]


@pytest.mark.parametrize("unit", ["ns", "s"])
def test_a_zoned_datetime_column_is_read_at_its_instant_without_parsing(unit, monkeypatch):
    # A parquet timestamp with a zone reads as datetime64[<unit>, <zone>]. It
    # is converted as a column: read value by value as text (the path it took
    # when the zone branch was dropped) it cost 6.8 s per million rows, not 0.01.
    # Only a second-unit column holds 9999; a nanosecond one gets 2025 there.
    far = "9999-12-31T08:00:00" if unit == "s" else "2025-01-01T08:00:00"
    stamps = np.array(["2024-03-01T08:00:00", far, "NaT"], dtype=f"datetime64[{unit}]")
    s = pd.Series(stamps).dt.tz_localize("Asia/Taipei")

    def parse(*_a, **_k):
        raise AssertionError("a zoned column was parsed value by value")

    monkeypatch.setattr(pd, "to_datetime", parse)
    got = epoch_ms(s).tolist()
    assert got[0] == _ms(2024, 3, 1) and math.isnan(got[2])
    assert got[1] == (_ms(9999, 12, 31) if unit == "s" else _ms(2025, 1, 1))


def test_a_far_date_with_slashes_is_a_date_as_a_near_one_is():
    # Review round 4: pandas read 2024/03/01, the far-date reader did not.
    got = epoch_ms(pd.Series(["9999/12/31", "2024/03/01"], dtype=object)).tolist()
    assert got == [_ms(9999, 12, 31), _ms(2024, 3, 1)]


def test_a_far_numpy_date_in_a_mixed_column_is_a_date():
    s = pd.Series([np.datetime64("9999-12-31"), np.datetime64("NaT", "D"), "x"], dtype=object)
    got = epoch_ms(s).tolist()
    assert got[0] == _ms(9999, 12, 31) and math.isnan(got[1]) and math.isnan(got[2])


def test_decimals_on_a_temporal_channel_are_milliseconds():
    # A parquet decimal column reads as object Decimals; they were gaps.
    from decimal import Decimal

    assert epoch_ms(pd.Series([Decimal("5"), Decimal("1.5")], dtype=object)).tolist() == [5.0, 1.5]


def test_text_that_is_no_date_stays_a_gap():
    got = epoch_ms(pd.Series(["soon", "2024-02-30", None], dtype=object)).tolist()
    assert all(math.isnan(v) for v in got)


def test_rate_over_lists_is_refused_by_name():
    df = pd.DataFrame({"g": ["a", "a"], "ok": pd.Series([[1, 2], [0]], dtype=object)})
    with pytest.raises(TransformError, match="'ok'"):
        apply_transforms(
            df, [{"aggregate": [{"op": "rate", "field": "ok", "as": "r"}], "groupby": ["g"]}]
        )


def _rule_on_time(datum: str) -> str:
    return (
        "view: chart\nsource: a.csv\nlayer:\n"
        "  - mark: line\n    encoding:\n"
        "      x: {field: t, type: temporal}\n      y: {field: v, type: quantitative}\n"
        f"  - mark: rule\n    encoding:\n      x: {{datum: {json.dumps(datum)}}}\n"
    )


_DAYS = pd.DataFrame({"t": ["2024-03-01", "2024-03-02"], "v": [1.0, 2.0]})


@pytest.mark.parametrize(
    # Digits too: a text datum is a date; "2024" was placed at 2024 ms, in 1970.
    "datum",
    ["2024-3-1", "2024-03-01T12:00:00+08", "2024-02-30", "2024", "1709294400000"],
)
def test_a_date_datum_the_chart_cannot_place_is_refused_by_name(datum):
    # The renderer drew no rule and said nothing (review round 3).
    from chart_view.validate import check

    result = check(_rule_on_time(datum), lambda _s: _DAYS)
    assert result.summary is None
    assert any(f"x: datum {datum!r}" in e for e in result.errors), result.errors


@pytest.mark.parametrize(
    "datum",
    ["2024-03-01", "2024-03-01T12:00:00Z", "2024/03/01T12:00:00+08:00", "0050-06-01 12:00Z"],
)
def test_a_date_datum_in_the_instant_forms_is_accepted(datum):
    from chart_view.validate import check

    assert check(_rule_on_time(datum), lambda _s: _DAYS).errors == []


def test_a_text_datum_on_a_category_axis_is_not_a_date():
    from chart_view.validate import check

    text = (
        "view: chart\nsource: a.csv\nlayer:\n"
        "  - mark: bar\n    encoding:\n"
        "      x: {field: g, type: nominal}\n      y: {field: v, type: quantitative}\n"
        "  - mark: rule\n    encoding:\n      x: {datum: b-3}\n"
    )
    df = pd.DataFrame({"g": ["a", "b-3"], "v": [1.0, 2.0]})
    assert check(text, lambda _s: df).errors == []


def test_a_value_that_is_no_date_and_no_number_is_a_gap():
    got = epoch_ms(pd.Series([True, ("a",), 1.0], dtype=object)).tolist()
    assert math.isnan(got[0]) and math.isnan(got[1]) and got[2] == 1.0


def _rule_on_grid(x_type: str, datum: str) -> str:
    return (
        "view: chart\nsource: a.csv\nlayer:\n"
        "  - mark: grid\n    encoding:\n"
        f"      x: {{field: t, type: {x_type}}}\n      y: {{field: v, type: ordinal}}\n"
        "      color: {field: v, type: quantitative}\n"
        f"  - mark: rule\n    encoding:\n      x: {{datum: {json.dumps(datum)}}}\n"
    )


def test_a_date_datum_on_a_temporal_grid_is_held_to_the_instant_forms():
    # Review round 4: a grid's temporal cells are dates too; the check skipped
    # every grid, and the renderer could not read a text datum on one — it
    # sent null, and ECharts threw on the whole chart.
    from chart_view.validate import check

    assert check(_rule_on_grid("temporal", "2024-03-01"), lambda _s: _DAYS).errors == []
    assert check(_rule_on_grid("temporal", "2024-3-1"), lambda _s: _DAYS).errors


def test_a_text_datum_on_an_ordinal_grid_names_a_cell():
    # Review round 5: a datum no cell holds was accepted, and the renderer
    # sent it to ECharts as null, which broke the whole chart.
    from chart_view.validate import check

    assert check(_rule_on_grid("ordinal", "2024-03-01"), lambda _s: _DAYS).errors == []
    errors = check(_rule_on_grid("ordinal", "2024-3-1"), lambda _s: _DAYS).errors
    assert errors == ["x: datum '2024-3-1' is not a cell of the grid, nor between two"]


def test_query_refuses_the_datum_validate_refuses(tmp_path: Path, monkeypatch, capsys):
    # A hand-edited file never passed validate: the chart drew without its rule.
    (tmp_path / "a.csv").write_text("t,v\n2024-03-01,1\n2024-03-02,2\n")
    monkeypatch.chdir(tmp_path)
    assert main(["query", json.dumps({"spec": _rule_on_time("2024-3-1")})]) == 2
    assert "x: datum '2024-3-1'" in capsys.readouterr().err


def test_a_binned_time_scatter_holds_a_date_past_the_nanosecond_range():
    # Review round 4: P18 placed 9999-12-31, and binning turned the bin centre
    # back into a nanosecond datetime — the query died with a traceback.
    from chart_view.query import build
    from chart_view.spec import parse_spec

    df = pd.DataFrame({"t": ["2024-01-01", "2024-01-02", "9999-12-31"], "v": [1.0, 2.0, 3.0]})
    spec = parse_spec(
        "view: chart\nsource: a.csv\nbin_threshold: 2\nmark: scatter\nencoding:\n"
        "  x: {field: t, type: temporal}\n  y: {field: v, type: quantitative}\n"
    )
    [layer] = build(spec, df)["layers"]
    got = _f64s(layer["columns"]["t"])
    # One bin per point; the far one drawn at its bin's centre, late in the 9900s.
    assert len(got) == 3 and max(got) > _ms(9900, 1, 1)


@pytest.mark.parametrize("datum", ["１７０９２９４４００００", "٢٠٢٤-03-01"])
def test_digits_the_renderer_does_not_read_as_digits_are_refused(datum):
    # Python's \d matches any Unicode digit; JavaScript's only 0-9, so
    # validate accepted a datum the renderer placed nowhere.
    from chart_view.validate import check

    result = check(_rule_on_time(datum), lambda _s: _DAYS)
    assert any(f"x: datum {datum!r}" in e for e in result.errors), result.errors


@pytest.mark.parametrize("unit", ["ns", "s"])
def test_a_temporal_measure_is_not_summarised_as_raw_numbers(unit):
    # Review round 5: show_file's line read a date column's storage, so the
    # summary said `t 1710000000000000000–…` for ns and `t 1710000000–…` for s.
    from chart_view.validate import check

    df = pd.DataFrame(
        {"v": [1.0, 2.0], "t": np.array(["2024-03-01", "2024-03-05"], dtype=f"datetime64[{unit}]")}
    )
    text = (
        "view: chart\nsource: a.csv\nmark: scatter\nencoding:\n"
        "  x: {field: v, type: quantitative}\n  y: {field: t, type: temporal}\n"
    )
    assert check(text, lambda _s: df).summary == "2 rows"


@pytest.mark.parametrize("datum", [".inf", "-.inf", ".nan"])
@pytest.mark.parametrize("scale", ["", ", scale: {type: log}"])
def test_a_datum_that_is_no_finite_number_is_refused(datum, scale):
    # Review round 6: YAML reads .inf / .nan as floats; the renderer's schema
    # check refuses them and JSON sends them as null. NaN <= 0 is False, so a
    # log axis let .nan through.
    from chart_view.validate import check

    text = (
        "view: chart\nsource: a.csv\nlayer:\n"
        f"  - mark: scatter\n    encoding:\n      x: {{field: v, type: quantitative{scale}}}\n"
        "      y: {field: v, type: quantitative}\n"
        f"  - mark: rule\n    encoding:\n      x: {{datum: {datum}}}\n"
    )
    errors = check(text, lambda _s: _DAYS).errors
    assert errors and errors[0].startswith("x: datum"), errors


def test_a_number_in_other_digits_in_a_time_column_is_milliseconds():
    # The data reader is the sandbox's own (the renderer never parses data):
    # "١٢٣" is 123, as float() reads it. Only a datum is held to [0-9].
    assert epoch_ms(pd.Series(["١٢٣"], dtype=object)).tolist() == [123.0]


def test_a_rule_drawn_from_a_field_has_no_datum_to_place():
    from chart_view.validate import check

    text = (
        "view: chart\nsource: a.csv\nlayer:\n"
        "  - mark: line\n    encoding:\n"
        "      x: {field: t, type: temporal}\n      y: {field: v, type: quantitative}\n"
        "  - mark: rule\n    encoding:\n      y: {field: v, type: quantitative}\n"
    )
    assert check(text, lambda _s: _DAYS).errors == []


@pytest.mark.parametrize(
    "rule",
    ["", "  - mark: rule\n    encoding:\n      y: {datum: 0.5}\n"],
    ids=["no rule", "a number-axis datum"],
)
def test_validate_builds_no_answer_a_datum_does_not_need(rule, monkeypatch):
    # Review round 6: encoding and decoding the whole answer cost 0.5 s on a
    # million rows for every chart; only a category or grid datum reads it.
    import chart_view.validate as validate

    def build(*_a, **_k):
        raise AssertionError("the answer was built")

    monkeypatch.setattr(validate, "answer", build)
    text = (
        "view: chart\nsource: a.csv\nlayer:\n"
        "  - mark: scatter\n    encoding:\n      x: {field: v, type: quantitative}\n"
        "      y: {field: v, type: quantitative}\n" + rule
    )
    assert validate.check(text, lambda _s: _DAYS).errors == []


def test_a_parquet_list_column_is_marking_text_not_a_traceback(tmp_path: Path, monkeypatch, capsys):
    # Review round 7: pyarrow reads a list column as numpy arrays, which
    # `_cat` did not turn into marking text; validate and query both crashed.
    pd.DataFrame({"k": [["a"], ["b"]], "v": [1.0, 2.0]}).to_parquet(tmp_path / "a.parquet")
    monkeypatch.chdir(tmp_path)
    bar = (
        "view: chart\nsource: a.parquet\nlayer:\n"
        "  - mark: bar\n    encoding:\n"
        "      x: {field: k, type: nominal}\n      y: {field: v, type: quantitative}\n"
    )
    assert main(["query", json.dumps({"spec": bar})]) == 0
    capsys.readouterr()
    (tmp_path / "v.chart.yaml").write_text(
        bar + "  - mark: rule\n    encoding:\n      x: {datum: a}\n"
    )
    assert main(["validate", json.dumps({"path": "v.chart.yaml"})]) == 2
    assert "x: datum 'a' is not a value the axis shows" in capsys.readouterr().err


def test_a_parquet_list_column_can_be_grouped(tmp_path: Path, monkeypatch, capsys):
    # Review round 8: P27 turned the arrays into text only when encoding, after
    # grouping — an aggregate over a list column still crashed.
    pd.DataFrame({"k": [["a", "b"], ["b"], ["b"]], "v": [1.0, 2.0, 3.0]}).to_parquet(
        tmp_path / "a.parquet"
    )
    monkeypatch.chdir(tmp_path)
    spec = (
        "view: chart\nsource: a.parquet\nmark: bar\nencoding:\n"
        "  x: {field: k, type: nominal}\n"
        "  y: {field: v, type: quantitative, aggregate: sum}\n"
    )
    assert main(["query", json.dumps({"spec": spec})]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["layers"][0]["columns"]["k"]["levels"] == ["['a', 'b']", "['b']"]


def test_an_array_reads_as_the_list_it_holds():
    # A parquet list and an entity list without nulls are one marking, at any
    # length (a parquet list with a null reads as floats with nan).
    assert canon(np.array(["a", "b"])) == canon(["a", "b"]) == "['a', 'b']"
    assert canon(np.array([1, 2])) == canon([1, 2]) == "[1, 2]"


def test_a_nested_or_timed_array_reads_as_plain_values():
    # Review round 9: numpy's own repr leaked into markings — nested arrays
    # printed "[array([1, 2]), array([3])]", and one instant printed apart for
    # ns and us columns.
    nested = np.array([np.array([1, 2]), np.array([3])], dtype=object)
    assert canon(nested) == canon([[1, 2], [3]]) == "[[1, 2], [3]]"
    # A parquet struct holding a list reads as a dict of arrays.
    assert canon({"a": np.array([1, 2])}) == canon({"a": [1, 2]}) == "{'a': [1, 2]}"
    assert canon({np.int64(1)}) == canon({1}) == "[1]"  # a set, in sorted order
    ns = np.array(["2024-01-01"], dtype="datetime64[ns]")
    us = np.array(["2024-01-01"], dtype="datetime64[us]")
    assert canon(ns) == canon(us) == "['2024-01-01T00:00:00']"


def test_a_filter_sees_a_list_as_a_list():
    # Review round 9: lists turned to text before the transforms, so a filter
    # on their length measured the text and kept every row.
    df = pd.DataFrame({"tags": [["x", "y"], ["x"], []], "v": [1.0, 2.0, 3.0]})
    spec = parse_spec(
        "view: chart\nsource: a.csv\nmark: bar\n"
        "transform:\n  - filter: 'tags.str.len() > 1'\n"
        "encoding:\n  x: {field: tags, type: nominal}\n  y: {field: v, type: quantitative}\n"
    )
    [layer] = build(spec, df)["layers"]
    assert layer["rows"] == 1


def test_an_entity_list_column_can_be_grouped():
    # Round 9 conformance: only numpy arrays had a test; an entity field holds
    # Python lists, and grouping by one raised TypeError before P29.
    df = pd.DataFrame({"k": [["a", "b"], ["b"], ["b"]], "v": [1.0, 2.0, 3.0]})
    spec = parse_spec(
        "view: chart\nsource: a.csv\nmark: bar\nencoding:\n"
        "  x: {field: k, type: nominal}\n  y: {field: v, type: quantitative, aggregate: sum}\n"
    )
    [layer] = build(spec, df)["layers"]
    assert layer["columns"]["k"]["levels"] == ["['a', 'b']", "['b']"]


def test_only_the_columns_a_group_needs_are_made_text(monkeypatch):
    # Review round 9: every object column of the source was mapped cell by
    # cell — 8.6 s on a million rows of 20 text columns the chart never read.
    df = pd.DataFrame({"g": ["a", "b"], "v": [1.0, 2.0], "unused": [["x"], ["y"]]})
    real = pd.Series.map

    def watch(self, *a, **k):
        assert self.name != "unused", "a column the chart does not read was mapped"
        return real(self, *a, **k)

    monkeypatch.setattr(pd.Series, "map", watch)
    spec = parse_spec(
        "view: chart\nsource: a.csv\nmark: bar\nencoding:\n"
        "  x: {field: g, type: nominal}\n  y: {field: v, type: quantitative, aggregate: sum}\n"
    )
    assert build(spec, df)["layers"][0]["rows"] == 2


def _arrays() -> pd.DataFrame:
    # What pyarrow gives for a parquet list column: numpy array cells. The
    # last row's longer arrays are the ones `==` raised on; a one-element array
    # compared as its only item (`equal: a` matched it) and never as its
    # marking text.
    return pd.DataFrame(
        {
            "tags": pd.Series(
                [np.array(["a"]), np.array(["b"]), np.array(["a"]), np.array(["a", "b"])],
                dtype=object,
            ),
            "n": pd.Series(
                [np.array([1]), np.array([2]), np.array([3]), np.array([1, 2])], dtype=object
            ),
            "v": [1.0, 2.0, 3.0, 4.0],
        }
    )


@pytest.mark.parametrize(
    "pred",
    [{"equal": "['a']"}, {"oneOf": ["['a']"]}],
)
def test_a_predicate_on_a_list_column_compares_its_marking(pred):
    # Review round 10: P31 left the arrays as arrays here, and `==` on one
    # raised "The truth value of an array … is ambiguous" — a traceback.
    out = apply_transforms(_arrays(), [{"filter": {"field": "tags", **pred}}])
    assert out["v"].tolist() == [1.0, 3.0]


@pytest.mark.parametrize(
    "pred", [{"lt": 1}, {"gte": 1}, {"range": [0, 1]}, {"gt": "2024-01-01"}, {"lt": "a"}]
)
@pytest.mark.parametrize("field", ["n", "tags"])
def test_an_order_on_a_list_column_is_refused_by_name(pred, field):
    # Round 11: against text, a list's marking text was ordered alphabetically
    # ("[" sorts after "2", so every list was "after" 2024) — refuse it.
    with pytest.raises(TransformError, match=f"'{field}' holds lists or mappings"):
        apply_transforms(_arrays(), [{"filter": {"field": field, **pred}}])


def test_a_column_of_plain_values_is_not_mapped_for_a_predicate(monkeypatch):
    # Round 11: object ints and entity dates were mapped cell by cell (33 ms ->
    # 510 ms per million rows, and their dtype changed): only a column that
    # can hold a list is.
    import datetime as dt

    from chart_view.wire import unhashable_as_text

    def no_map(*_a, **_k):
        raise AssertionError("a column with no list was mapped cell by cell")

    monkeypatch.setattr(pd.Series, "map", no_map)
    for s in [
        pd.Series([1, 2, None], dtype=object),
        pd.Series([dt.date(2024, 1, 1)], dtype=object),
        pd.Series([True, False], dtype=object),
    ]:
        assert unhashable_as_text(s) is s


def test_a_range_on_text_is_refused_by_name():
    # `range` compared outside the wrapper `lt` / `gt` had: a bare TypeError.
    df = pd.DataFrame({"t": ["a", "b"], "v": [1.0, 2.0]})
    with pytest.raises(TransformError, match="'t'"):
        apply_transforms(df, [{"filter": {"field": "t", "range": [0, 1]}}])


def test_a_diff_by_a_list_column_splits_on_its_marking():
    t = {
        "diff": {"by": "tags", "of": "['a']", "minus": "['b']"},
        "aggregate": [{"op": "sum", "field": "v", "as": "s"}],
    }
    assert apply_transforms(_arrays(), [t])["s"].tolist() == [2.0]


def test_a_mapping_reads_the_same_in_any_key_order():
    # Round 10: two records holding one mapping in two orders were two groups;
    # a set printed in hash order, which changes from process to process.
    assert canon({"b": 2, "a": 1}) == canon({"a": 1, "b": 2}) == "{'a': 1, 'b': 2}"
    # Ten letters: a few came out sorted by chance under some hash seeds.
    assert canon(set("jihgfedcba")) == str(list("abcdefghij"))
    # Keys sorted as they print, whatever numpy type holds them.
    assert (
        canon({np.int64(1): "a", 2: "b"}) == canon({1: "a", np.int64(2): "b"}) == "{1: 'a', 2: 'b'}"
    )
    assert canon({("a", 1)}) == "[['a', 1]]"


def test_a_missing_instant_in_a_list_is_none():
    got = canon(np.array(["NaT", "2024-01-01"], dtype="datetime64[ns]"))
    assert got == "[None, '2024-01-01T00:00:00']"


def test_every_kind_of_instant_in_a_list_is_iso_text():
    # Round 10 regression lens: a Python datetime or date (an entity list, a
    # parquet list<date32> or struct) kept its repr, and pd.NaT printed "NaT".
    import datetime as dt

    iso = "['2024-01-01T00:00:00']"
    assert canon([dt.datetime(2024, 1, 1)]) == canon([pd.Timestamp("2024-01-01")]) == iso
    assert canon([dt.date(2024, 1, 1)]) == "['2024-01-01']"
    assert canon([pd.NaT]) == "[None]"
    assert canon({np.int64(1): 2}) == "{1: 2}"


def test_a_text_column_is_not_visited_cell_by_cell(monkeypatch):
    # Round 10 conformance: the fast path P31 claimed had no test.
    from chart_view.wire import unhashable_as_text

    def no_map(*_a, **_k):
        raise AssertionError("a text column was mapped cell by cell")

    s = pd.Series(["a", "b", None], dtype=object)
    monkeypatch.setattr(pd.Series, "map", no_map)
    assert unhashable_as_text(s) is s


LIST_KEY = pd.DataFrame(
    {
        "k": pd.Series([["a"], ["b"], ["a"], ["b"]], dtype=object),
        "arr": pd.Series([np.array(["a"]), np.array(["b"])] * 2, dtype=object),
        "v": [1.0, 2.0, 3.0, 4.0],
    }
)


@pytest.mark.parametrize("mark", ["boxplot", "errorbar"])
@pytest.mark.parametrize("key", ["k", "arr"])
def test_a_list_key_groups_a_statistic(mark, key):
    # Round 10 conformance: only the aggregate's conversion had a test; the
    # boxplot's, errorbar's and binning's were applied but unpinned.
    spec = parse_spec(
        f"view: chart\nsource: a.csv\nmark: {mark}\nencoding:\n"
        f"  x: {{field: {key}, type: nominal}}\n  y: {{field: v, type: quantitative}}\n"
    )
    [layer] = build(spec, LIST_KEY)["layers"]
    assert layer["columns"][key]["levels"] == ["['a']", "['b']"]


def test_a_list_colour_groups_the_bins():
    spec = parse_spec(
        "view: chart\nsource: a.csv\nbin_threshold: 2\nmark: scatter\nencoding:\n"
        "  x: {field: v, type: quantitative}\n  y: {field: v, type: quantitative}\n"
        "  color: {field: k, type: nominal}\n"
    )
    [layer] = build(spec, LIST_KEY)["layers"]
    assert layer["binned"] is not None and layer["columns"]["k"]["levels"] == ["['a']", "['b']"]


def _days() -> pd.DataFrame:
    # A parquet date32 column reads as an object column of datetime.date.
    import datetime as dt

    days = [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(9999, 12, 31)]
    return pd.DataFrame({"day": pd.Series(days, dtype=object), "v": [1.0, 2.0, 3.0]})


@pytest.mark.parametrize(
    ("pred", "kept"),
    [
        ({"equal": "2024-01-01"}, [1.0]),
        ({"oneOf": ["2024-01-02", "9999-12-31"]}, [2.0, 3.0]),
        ({"gte": "2024-01-02"}, [2.0, 3.0]),
        ({"range": ["2024-01-01", "2024-01-02"]}, [1.0, 2.0]),
    ],
)
def test_a_predicate_on_a_date_column_reads_its_text_as_a_date(pred, kept):
    # Round 11: the same "2024-01-01" lit a highlight and matched nothing in a
    # filter (a date object is never equal to text), and an order was refused.
    out = apply_transforms(_days(), [{"filter": {"field": "day", **pred}}])
    assert out["v"].tolist() == kept


def test_a_predicate_on_zoned_datetimes_reads_them_at_utc():
    # Datetime objects with zones (mixed offsets) cannot be one datetime64
    # column as they are: read at UTC, as zone-less text is, far ones kept.
    import datetime as dt

    zoned = [
        dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        dt.datetime(2024, 1, 1, 8, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        dt.datetime(9999, 1, 1, tzinfo=dt.UTC),
    ]
    df = pd.DataFrame({"at": pd.Series(zoned, dtype=object), "v": [1.0, 2.0, 3.0]})
    kept = apply_transforms(df, [{"filter": {"field": "at", "equal": "2024-01-01"}}])
    assert kept["v"].tolist() == [1.0, 2.0]
    late = apply_transforms(df, [{"filter": {"field": "at", "gt": "2025-01-01"}}])
    assert late["v"].tolist() == [3.0]


def test_a_diff_by_a_date_column_reads_its_sides_as_dates():
    t = {
        "diff": {"by": "day", "of": "2024-01-02", "minus": "2024-01-01"},
        "aggregate": [{"op": "sum", "field": "v", "as": "s"}],
    }
    assert apply_transforms(_days(), [t])["s"].tolist() == [1.0]


def test_a_refused_range_names_the_range():
    # Round 11: the refusal named `gte`, an op the author never wrote.
    df = pd.DataFrame({"t": ["a", "b"], "v": [1.0, 2.0]})
    with pytest.raises(TransformError, match=r"range \[0, 1\]"):
        apply_transforms(df, [{"filter": {"field": "t", "range": [0, 1]}}])


def _skill_list_filters() -> list[str]:
    # Every query filter the skill's list-field paragraph quotes, read from the
    # skill itself (round 12: the test ran its own copy of the text).
    import re

    skill = (Path(__file__).resolve().parents[2] / "skill" / "SKILL.md").read_text()
    paragraph = skill[skill.index("A cell holding a list") :].split("\n\n")[0]
    return re.findall(r'`[^`"]*"([^"`]+)"`', paragraph)  # every `"…"` and `key: "…"`


# What each quoted filter keeps of: an item "a" with another, an item that only
# contains "a", no "a", a record without the field.
_SKILL_FILTER_KEEPS = {
    "tags.str.contains('a', regex=False, na=False)": [1.0],
    "tags.str.len() > 1": [1.0],
}


def test_the_skill_quotes_the_list_filters_this_test_runs():
    assert sorted(_skill_list_filters()) == sorted(_SKILL_FILTER_KEEPS)


@pytest.mark.parametrize("query", sorted(_SKILL_FILTER_KEEPS))
@pytest.mark.parametrize("cells", ["lists", "arrays"])
def test_the_skills_list_filters_keep_what_they_say(query, cells):
    tags = [["a", "b"], ["ab"], ["c"], None]
    if cells == "arrays":  # a parquet list column, with a null
        tags = [None if t is None else np.array(t) for t in tags]
    df = pd.DataFrame({"tags": pd.Series(tags, dtype=object), "v": [1.0, 2.0, 3.0, 4.0]})
    kept = apply_transforms(df, [{"filter": query}])
    assert kept["v"].tolist() == _SKILL_FILTER_KEEPS[query]


@pytest.mark.parametrize("value", ["soon", "10000-01-01"])
def test_a_one_of_value_that_is_no_date_is_refused_on_a_date_column(value):
    # Round 12: P33 cast the whole list to the column's dtype, and one value
    # that is no date raised DateParseError out of the CLI.
    with pytest.raises(TransformError, match=f"oneOf {value!r} is not a date"):
        apply_transforms(_days(), [{"filter": {"field": "day", "oneOf": ["2024-01-01", value]}}])


@pytest.mark.parametrize("op", ["equal", "oneOf"])
def test_a_predicate_holds_no_null(op):
    # Round 13 regression lens: so a predicate's value is never None, and
    # `valid: false` is how a spec names the missing rows.
    from chart_view.spec import spec_errors

    value = "null" if op == "equal" else "[null]"
    spec = (
        "view: chart\nsource: data/a.csv\nmark: point\n"
        f"transform:\n  - filter: {{field: a, {op}: {value}}}\n"
        "encoding:\n  x: {field: a, type: temporal}\n  y: {field: b, type: quantitative}\n"
    )
    assert any("None is not of type" in e for e in spec_errors(parse_spec(spec)))


@pytest.mark.parametrize(
    ("column", "values"),
    [
        # a nanosecond column cannot hold year 1 or 9999
        (pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"])), ["0001-01-01", "9999-12-31"]),
        # 2024-03-10 02:30 never happened in New York (the clocks sprang ahead)
        (
            pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"])).dt.tz_localize(
                "America/New_York"
            ),
            ["2024-03-10T02:30"],
        ),
    ],
    ids=["past-nanoseconds", "no-such-local-time"],
)
def test_a_one_of_date_the_column_cannot_hold_matches_nothing(column, values):
    # Round 12 regression lens: these raised out of the CLI (OutOfBounds,
    # NonExistentTime) where 4e467420 matched nothing. A date the column cannot
    # hold names none of its rows.
    df = pd.DataFrame({"at": column, "v": [1.0, 2.0]})
    kept = apply_transforms(df, [{"filter": {"field": "at", "oneOf": [*values, "2024-01-02"]}}])
    assert kept["v"].tolist() == [2.0]


def test_a_zoned_one_of_value_on_a_zoned_column_is_the_same_instant():
    at = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"])).dt.tz_localize("Asia/Taipei")
    df = pd.DataFrame({"at": at, "v": [1.0, 2.0]})
    kept = apply_transforms(df, [{"filter": {"field": "at", "oneOf": ["2024-01-01T16:00:00Z"]}}])
    assert kept["v"].tolist() == [2.0]  # 16:00 UTC is midnight in Taipei


def test_a_one_of_value_that_is_no_date_is_refused_on_a_datetime_column():
    at = pd.Series(pd.to_datetime(["2024-01-01"]))
    df = pd.DataFrame({"at": at, "v": [1.0]})
    with pytest.raises(TransformError, match="oneOf 'abc' is not a date"):
        apply_transforms(df, [{"filter": {"field": "at", "oneOf": ["abc"]}}])
    with pytest.raises(TransformError, match="oneOf True is not a date"):
        apply_transforms(_days(), [{"filter": {"field": "day", "oneOf": [True]}}])


def test_a_one_of_date_on_a_zoned_column_is_read_in_its_zone():
    # A typed zoned column compares text in its own zone, as `equal` does there.
    at = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"])).dt.tz_localize("Asia/Taipei")
    df = pd.DataFrame({"at": at, "v": [1.0, 2.0]})
    kept = apply_transforms(df, [{"filter": {"field": "at", "oneOf": ["2024-01-02"]}}])
    assert kept["v"].tolist() == [2.0]


def test_a_one_of_value_with_a_zone_names_its_instant_on_a_date_column():
    kept = apply_transforms(
        _days(), [{"filter": {"field": "day", "oneOf": ["2024-01-02T08:00:00+08:00"]}}]
    )
    assert kept["v"].tolist() == [2.0]


def test_a_datetime_keeps_its_fraction_of_a_second():
    # Round 12: datetime64[s] dropped it — all three matched midnight.
    import datetime as dt

    times = [dt.datetime(2024, 1, 1, 0, 0, 0, us) for us in (1, 2, 900000)]
    df = pd.DataFrame({"at": pd.Series(times, dtype=object), "v": [1.0, 2.0, 3.0]})
    assert apply_transforms(df, [{"filter": {"field": "at", "equal": "2024-01-01"}}]).empty
    early = apply_transforms(df, [{"filter": {"field": "at", "lt": "2024-01-01T00:00:00.5"}}])
    assert early["v"].tolist() == [1.0, 2.0]


def test_an_instant_past_the_calendar_at_utc_is_refused_by_name():
    # Round 12: 0001-01-01 at +08:00 is before year 1 at UTC; OverflowError.
    import datetime as dt

    zone = dt.timezone(dt.timedelta(hours=8))
    df = pd.DataFrame(
        {"at": pd.Series([dt.datetime(1, 1, 1, tzinfo=zone)], dtype=object), "v": [1.0]}
    )
    with pytest.raises(TransformError, match="'at'"):
        apply_transforms(df, [{"filter": {"field": "at", "lt": "2024-01-01"}}])


def test_an_object_column_of_numpy_instants_reads_text_as_a_date():
    # Round 12: `infer_dtype` calls it "datetime64", which the date branch
    # left out — `equal: "2024-01-01"` matched nothing.
    at = pd.Series([np.datetime64("2024-01-01"), np.datetime64("2024-01-02")], dtype=object)
    df = pd.DataFrame({"at": at, "v": [1.0, 2.0]})
    kept = apply_transforms(df, [{"filter": {"field": "at", "equal": "2024-01-01"}}])
    assert kept["v"].tolist() == [1.0]


@pytest.mark.parametrize(
    ("cells", "text"),
    [
        ([[1], 2, 3], "[1]"),  # infer_dtype: "mixed-integer"
        ([{"a": 1}, {"a": 2}, {"a": 3}], "{'a': 1}"),
        ([(1,), (2,), (3,)], "[1]"),
        ([{1}, {2}, {3}], "[1]"),
    ],
    ids=["mixed-integer", "mappings", "tuples", "sets"],
)
def test_every_kind_of_container_is_compared_as_its_text(cells, text):
    # Round 12: only lists and arrays had a test; each member of the
    # container test, and the "mixed-integer" column, are pinned here.
    df = pd.DataFrame({"k": pd.Series(cells, dtype=object), "v": [1.0, 2.0, 3.0]})
    kept = apply_transforms(df, [{"filter": {"field": "k", "equal": text}}])
    assert kept["v"].tolist() == [1.0]
    with pytest.raises(TransformError, match="holds lists or mappings"):
        apply_transforms(df, [{"filter": {"field": "k", "lt": "z"}}])


def _new_york() -> pd.DataFrame:
    # 2024-11-03 01:30 happened twice in New York (EDT, then EST); 2024-03-10
    # 02:30 never did. Rows: the two 01:30s, and a later day.
    at = pd.Series(
        pd.to_datetime(["2024-11-03T05:30Z", "2024-11-03T06:30Z", "2024-12-01T12:00Z"])
    ).dt.tz_convert("America/New_York")
    return pd.DataFrame({"at": at, "v": [1.0, 2.0, 3.0]})


@pytest.mark.parametrize("op", ["equal", "oneOf"])
def test_an_ambiguous_local_time_names_both_of_its_instants(op):
    # Round 13: `equal` raised AmbiguousTimeError out of the CLI, and `oneOf`
    # dropped it — the chart shows both rows at 01:30.
    value = "2024-11-03T01:30" if op == "equal" else ["2024-11-03T01:30"]
    kept = apply_transforms(_new_york(), [{"filter": {"field": "at", op: value}}])
    assert kept["v"].tolist() == [1.0, 2.0]


@pytest.mark.parametrize("op", ["equal", "oneOf"])
def test_a_local_time_a_zone_never_had_names_no_row(op):
    # Round 13: nor the missing rows — the NaT a zone gives it is left out.
    at = pd.Series(pd.to_datetime(["2024-03-11", None])).dt.tz_localize("America/New_York")
    df = pd.DataFrame({"at": at, "v": [1.0, 2.0]})
    value = "2024-03-10T02:30" if op == "equal" else ["2024-03-10T02:30"]
    assert apply_transforms(df, [{"filter": {"field": "at", op: value}}]).empty


@pytest.mark.parametrize(
    "pred", [{"lt": "2024-11-03T01:30"}, {"range": ["2024-03-10T02:30", "2024-12-01"]}]
)
def test_an_order_against_no_single_local_time_is_refused_by_name(pred):
    with pytest.raises(TransformError, match="no single time in America/New_York"):
        apply_transforms(_new_york(), [{"filter": {"field": "at", **pred}}])


@pytest.mark.parametrize("op", ["equal", "oneOf"])
def test_a_number_on_a_datetime_column_is_epoch_milliseconds(op):
    # As a temporal datum and a number in a time column are: 1704153600000 is
    # 2024-01-02. `oneOf` read it as nanoseconds; `equal` matched nothing.
    at = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"]))
    df = pd.DataFrame({"at": at, "v": [1.0, 2.0]})
    value = 1704153600000 if op == "equal" else [1704153600000]
    assert apply_transforms(df, [{"filter": {"field": "at", op: value}}])["v"].tolist() == [2.0]


@pytest.mark.parametrize("value", ["", "NaT"])
def test_text_that_names_no_instant_is_refused_on_a_datetime_column(value):
    # pd.Timestamp("") is NaT, which matched the missing rows.
    with pytest.raises(TransformError, match="is not a date"):
        apply_transforms(_days(), [{"filter": {"field": "day", "oneOf": [value]}}])


def test_a_date_column_with_a_date_left_as_text_is_still_a_date_column():
    # Round 13: an entity date field where YAML left one value as text
    # ("2024-01-03 08:00") was "mixed", so no text matched its dates.
    import datetime as dt

    day = pd.Series([dt.date(2024, 1, 1), dt.date(2024, 1, 2), "2024-01-03 08:00", None])
    df = pd.DataFrame({"day": day.astype(object), "v": [1.0, 2.0, 3.0, 4.0]})
    assert apply_transforms(df, [{"filter": {"field": "day", "equal": "2024-01-02"}}])[
        "v"
    ].tolist() == [2.0]
    later = apply_transforms(df, [{"filter": {"field": "day", "gte": "2024-01-02"}}])
    assert later["v"].tolist() == [2.0, 3.0]


@pytest.mark.parametrize("other", ["soon", 5.5], ids=["text-no-date", "number"])
def test_a_column_of_dates_and_other_values_is_left_as_it_is(other):
    # All of them dates, or none: a cell that is no date keeps the column as
    # its values are, and that cell still matches itself.
    import datetime as dt

    day = pd.Series([dt.date(2024, 1, 1), other], dtype=object)
    df = pd.DataFrame({"day": day, "v": [1.0, 2.0]})
    kept = apply_transforms(df, [{"filter": {"field": "day", "equal": other}}])
    assert kept["v"].tolist() == [2.0]
