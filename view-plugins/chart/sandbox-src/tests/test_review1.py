"""Review round 1 findings on the sandbox half, each with the input that
reproduced it."""

from __future__ import annotations

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
from chart_view.validate import check
from chart_view.wire import canon, encode_column

BIG = 1152921504606846976  # 2**60: a JS Number, but printed as 1152921504606847000


def test_an_integer_past_2_53_reads_as_the_host_reads_it():
    # js-yaml reads it into a Number; the renderer sends that as JSON, which
    # prints 1152921504606847000. Both must come out as the same value here,
    # or validate lights a row the chart does not.
    from_file = parse_spec(f"k: {BIG}")["k"]
    from_renderer = parse_spec(json.dumps({"k": float(BIG)}))["k"]
    assert from_file == from_renderer == float(BIG)
    assert canon(BIG) == canon(float(BIG)) == "1152921504606847000"


def test_a_highlight_on_a_huge_id_lights_the_same_rows_either_way():
    df = pd.DataFrame({"id": [BIG, 1, 2], "v": [1.0, 2.0, 3.0]})
    text = (
        "view: chart\nsource: a.csv\nmark: scatter\nencoding:\n"
        "  x: {field: v, type: quantitative}\n  y: {field: v, type: quantitative}\n"
        f"highlight: {{values: {{id: [{BIG}]}}}}\n"
    )
    summary = check(text, lambda _s: df).summary
    assert summary is not None and summary.startswith("highlight matches 1/3 rows")
    doc = parse_spec(text)
    # What the renderer sends: js-yaml's document as JSON (the id as a Number).
    sent = json.dumps({**doc, "highlight": {"values": {"id": [float(BIG)]}}})
    [layer] = build(parse_spec(sent), df)["layers"]
    assert layer["lit"] == 1


def test_a_measure_with_infinities_is_summarised_over_its_finite_values():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [1.0, math.inf, 3.0]})
    text = (
        "view: chart\nsource: a.csv\nmark: scatter\nencoding:\n"
        "  x: {field: x, type: quantitative}\n  y: {field: y, type: quantitative}\n"
    )
    assert check(text, lambda _s: df).summary == "3 rows; y 1–3"


def test_a_boxplot_whose_filter_leaves_nothing_draws_nothing():
    df = pd.DataFrame({"g": ["a"], "v": [1.0]})
    spec = parse_spec(
        "view: chart\nsource: a.csv\nmark: boxplot\nencoding:\n"
        "  x: {field: g, type: nominal}\n  y: {field: v, type: quantitative}\n"
        "transform:\n  - filter: 'v > 5'\n"
    )
    [layer] = build(spec, df)["layers"]
    assert layer["rows"] == 0 and layer["outliers"] is None


def test_comparing_text_with_a_number_is_named_not_a_crash():
    df = pd.DataFrame({"name": ["a", "b"]})
    with pytest.raises(TransformError, match="'name'"):
        apply_transforms(df, [{"filter": {"field": "name", "gt": 3}}])


def test_an_aggregate_named_like_a_group_column_is_named_not_a_crash():
    df = pd.DataFrame({"g": ["a", "b"]})
    with pytest.raises(TransformError, match="'g'"):
        apply_transforms(df, [{"aggregate": [{"op": "count", "as": "g"}], "groupby": ["g"]}])


def test_a_column_of_lists_is_sent_as_marking_strings():
    s = pd.Series([["a", "b"], ["c"], ["a", "b"]], dtype=object)
    wire = encode_column(s, "cat")
    assert wire["levels"] == ["['a', 'b']", "['c']"]


def test_numbers_on_a_temporal_channel_are_epoch_milliseconds():
    ms = 1_700_000_000_000
    wire = encode_column(pd.Series([ms, ms + 1]), "time")
    import base64

    got = np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8").tolist()
    assert got == [float(ms), float(ms + 1)]


def test_a_key_that_is_not_a_category_is_also_sent_as_marking_strings():
    df = pd.DataFrame({"day": ["2024-01-01", "2024-01-02"], "v": [1.0, 2.0]})
    spec = parse_spec(
        "view: chart\nsource: a.csv\nkeys: [day]\nmark: line\nencoding:\n"
        "  x: {field: day, type: temporal}\n  y: {field: v, type: quantitative}\n"
    )
    [layer] = build(spec, df)["layers"]
    assert layer["columns"]["day"]["kind"] == "time"
    assert layer["columns"]["$key.day"] == encode_column(df["day"], "cat")


def test_keys_a_layer_lost_or_already_sends_as_categories_add_nothing():
    df = pd.DataFrame({"lot": ["A", "A", "B"], "wafer": [1, 2, 3], "v": [1.0, 2.0, 3.0]})
    spec = parse_spec(
        "view: chart\nsource: a.csv\nkeys: [lot, wafer]\nmark: bar\nencoding:\n"
        "  x: {field: lot, type: nominal}\n  y: {field: v, type: quantitative, aggregate: mean}\n"
    )
    [layer] = build(spec, df)["layers"]
    # `wafer` is aggregated away; `lot` is already a category channel.
    assert set(layer["columns"]) == {"lot", "v"}


def _f64s(wire: dict) -> list[float]:
    import base64

    return np.frombuffer(base64.b64decode(wire["data"]), dtype="<f8").tolist()


@pytest.mark.parametrize("kind", ["f64", "time"])
def test_an_infinity_travels_as_missing(kind):
    # Round 2: validate summarised the finite values, but the wire still sent
    # inf and the renderer's colour scale became 0.1–Infinity.
    got = _f64s(encode_column(pd.Series([0.1, math.inf, -math.inf]), kind))
    assert got[0] == 0.1 and math.isnan(got[1]) and math.isnan(got[2])


def test_a_temporal_column_mixing_numbers_and_text_reads_each_its_own_way():
    ms = 1709294400000
    s = pd.Series([ms, "2024-03-02", str(ms), None], dtype=object)
    got = _f64s(encode_column(s, "time"))
    day = pd.Timestamp("2024-03-02", tz="UTC").value / 1e6
    assert got[:3] == [float(ms), day, float(ms)] and math.isnan(got[3])


def test_a_nul_byte_in_a_path_is_a_refusal(tmp_path: Path):
    from chart_view.sources import SourceError, inside_workspace

    with pytest.raises(SourceError, match="not a usable path"):
        inside_workspace(tmp_path, "a\0.csv")


def test_rate_over_text_is_refused_by_name():
    # `bool("no")` is True: a text column of yes / no rated 1.0.
    df = pd.DataFrame({"g": ["a", "a"], "ok": ["yes", "no"]})
    with pytest.raises(TransformError, match="'ok'"):
        apply_transforms(
            df, [{"aggregate": [{"op": "rate", "field": "ok", "as": "r"}], "groupby": ["g"]}]
        )


@pytest.mark.parametrize("dtype", ["Int64", "UInt8"])
def test_a_nullable_integer_category_keeps_integer_levels(dtype):
    # A parquet integer column with a null reads as Int64; its levels are the
    # integers, not 9.0 / 10.0 (review round 2, from #857's report).
    wire = encode_column(pd.Series([10, 9, None], dtype=dtype), "cat")
    assert wire["levels"] == [9, 10]
    assert all(type(v) is int for v in wire["levels"])


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    (root / "views").mkdir(parents=True)
    (tmp_path / "outside.ai.yaml").write_text("secret-token-abc123\n")
    (tmp_path / "outside.csv").write_text("a,b\n1,2\n")
    monkeypatch.chdir(root)
    return root


def test_validate_refuses_a_path_outside_the_workspace(workspace, capsys):
    assert main(["validate", json.dumps({"path": "../outside.ai.yaml"})]) == 2
    err = capsys.readouterr().err
    assert "outside the workspace" in err and "secret-token" not in err


def test_a_source_outside_the_workspace_is_refused(workspace, capsys):
    spec = (
        "view: chart\nsource: ../outside.csv\nmark: scatter\nencoding:\n"
        "  x: {field: a, type: quantitative}\n  y: {field: b, type: quantitative}\n"
    )
    assert main(["query", json.dumps({"spec": spec})]) == 2
    assert "outside the workspace" in capsys.readouterr().err


def test_a_view_file_that_is_not_utf8_is_refused_by_name(workspace, capsys):
    (workspace / "views" / "bin.ai.yaml").write_bytes(b"\xff\xfe\x00bad")
    assert main(["validate", json.dumps({"path": "views/bin.ai.yaml"})]) == 2
    assert "views/bin.ai.yaml" in capsys.readouterr().err
