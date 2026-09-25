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


@pytest.mark.parametrize("datum", ["2024-3-1", "2024-03-01T12:00:00+08", "2024-02-30"])
def test_a_date_datum_the_chart_cannot_place_is_refused_by_name(datum):
    # The renderer drew no rule and said nothing (review round 3).
    from chart_view.validate import check

    result = check(_rule_on_time(datum), lambda _s: _DAYS)
    assert result.summary is None
    assert any(f"x: datum {datum!r}" in e for e in result.errors), result.errors


@pytest.mark.parametrize("datum", ["2024-03-01", "2024-03-01T12:00:00Z", "1709294400000"])
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


def test_a_datum_on_a_grid_names_a_cell_not_a_date():
    # A grid's axes are its cells: the renderer finds a datum by its label.
    from chart_view.validate import check

    text = (
        "view: chart\nsource: a.csv\nlayer:\n"
        "  - mark: grid\n    encoding:\n"
        "      x: {field: t, type: temporal}\n      y: {field: v, type: ordinal}\n"
        "      color: {field: v, type: quantitative}\n"
        "  - mark: rule\n    encoding:\n      x: {datum: 2024-3-1}\n"
    )
    assert check(text, lambda _s: _DAYS).errors == []
