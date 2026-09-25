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
from chart_view.transforms import TransformError, apply_transforms
from chart_view.wire import encode_column, epoch_ms


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


def test_a_zoned_datetime_column_is_read_at_its_instant():
    # A parquet timestamp with a zone reads as datetime64[ns, <zone>].
    s = pd.Series(pd.to_datetime(["2024-03-01T08:00:00", None])).dt.tz_localize("Asia/Taipei")
    got = epoch_ms(s).tolist()
    assert got[0] == _ms(2024, 3, 1) and math.isnan(got[1])


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
    # every grid, and the renderer placed no text datum on one.
    from chart_view.validate import check

    assert check(_rule_on_grid("temporal", "2024-03-01"), lambda _s: _DAYS).errors == []
    assert check(_rule_on_grid("temporal", "2024-3-1"), lambda _s: _DAYS).errors


def test_a_text_datum_on_an_ordinal_grid_names_a_cell():
    from chart_view.validate import check

    assert check(_rule_on_grid("ordinal", "2024-3-1"), lambda _s: _DAYS).errors == []


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
