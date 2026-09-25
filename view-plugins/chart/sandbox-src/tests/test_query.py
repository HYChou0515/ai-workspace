"""`query`: a validated spec over its source frame → what each layer draws."""

from __future__ import annotations

import base64

import numpy as np
import pandas as pd
import pytest

from chart_view.query import DEFAULT_BIN_THRESHOLD, build
from chart_view.transforms import TransformError


def _f64(col: dict) -> list[float | None]:
    raw = np.frombuffer(base64.b64decode(col["data"]), dtype="<f8")
    return [None if np.isnan(v) else float(v) for v in raw]


def _cat(col: dict) -> list:
    width = col["width"]
    dtype = {1: "<u1", 2: "<u2", 4: "<u4"}[width]
    codes = np.frombuffer(base64.b64decode(col["codes"]), dtype=dtype)
    missing = 2 ** (8 * width) - 1
    return [None if c == missing else col["levels"][c] for c in codes]


def _bits(b64: str, rows: int) -> list[bool]:
    raw = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    return [bool(b) for b in np.unpackbits(raw, bitorder="little")[:rows]]


def _spec(**kw) -> dict:
    return {"view": "chart", "source": "data/a.csv", **kw}


@pytest.fixture
def wafers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "lot": ["A", "A", "B", "B", "C"],
            "wafer": [1, 2, 3, 4, 5],
            "thickness": [100.0, 101.0, 99.0, 104.0, 98.0],
            "fail_rate": [0.1, 0.4, 0.05, 0.35, 0.2],
            "day": ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-03", "2024-01-02"],
        }
    )


SCATTER = {
    "x": {"field": "thickness", "type": "quantitative"},
    "y": {"field": "fail_rate", "type": "quantitative"},
    "color": {"field": "lot", "type": "nominal"},
}


def test_a_scatter_sends_its_channels_column_wise(wafers):
    out = build(_spec(mark="scatter", encoding=SCATTER), wafers)
    [layer] = out["layers"]
    assert out["format"] == 1
    assert layer["mark"] == "scatter" and layer["rows"] == 5
    assert _f64(layer["columns"]["thickness"]) == [100.0, 101.0, 99.0, 104.0, 98.0]
    assert _cat(layer["columns"]["lot"]) == ["A", "A", "B", "B", "C"]
    assert layer["highlight"] is None and layer["lit"] is None and layer["binned"] is None


def test_only_the_columns_a_layer_uses_are_sent(wafers):
    [layer] = build(_spec(mark="scatter", encoding=SCATTER), wafers)["layers"]
    assert set(layer["columns"]) == {"thickness", "fail_rate", "lot"}


def test_keys_are_sent_so_a_selection_can_name_them(wafers):
    [layer] = build(_spec(mark="scatter", encoding=SCATTER, keys=["wafer"]), wafers)["layers"]
    assert _cat(layer["columns"]["wafer"]) == [1, 2, 3, 4, 5]


def test_a_temporal_channel_is_sent_as_epoch_ms(wafers):
    enc = {"x": {"field": "day", "type": "temporal"}, "y": SCATTER["y"]}
    [layer] = build(_spec(mark="line", encoding=enc), wafers)["layers"]
    assert layer["columns"]["day"]["kind"] == "time"
    assert _f64(layer["columns"]["day"])[0] == pd.Timestamp("2024-01-01", tz="UTC").value / 1e6


def test_a_tooltip_list_sends_each_field(wafers):
    enc = {**SCATTER, "tooltip": [{"field": "wafer", "type": "nominal"}]}
    [layer] = build(_spec(mark="scatter", encoding=enc), wafers)["layers"]
    assert "wafer" in layer["columns"]


def test_where_highlight_lights_the_matching_rows(wafers):
    spec = _spec(mark="scatter", encoding=SCATTER, highlight={"where": "fail_rate > 0.3"})
    [layer] = build(spec, wafers)["layers"]
    assert _bits(layer["highlight"], 5) == [False, True, False, True, False]
    assert layer["lit"] == 2


def test_values_highlight_compares_marking_strings(wafers):
    # `wafer` holds numbers; a marking holds opaque strings (Q6). 2 and "2" and
    # 2.0 are the same value to a person, and the same string to canon().
    spec = _spec(
        mark="scatter",
        encoding=SCATTER,
        highlight={"values": {"wafer": ["2", 4.0], "lot": ["A", "B"]}},
    )
    [layer] = build(spec, wafers)["layers"]
    assert _bits(layer["highlight"], 5) == [False, True, False, True, False]


def test_a_highlight_a_layer_cannot_see_leaves_that_layer_unlit(wafers):
    spec = _spec(
        highlight={"where": "fail_rate > 0.3"},
        layer=[
            {"mark": "scatter", "encoding": SCATTER},
            {"mark": "rule", "encoding": {"y": {"datum": 0.3}}},
        ],
    )
    scatter, rule = build(spec, wafers)["layers"]
    assert scatter["lit"] == 2
    assert rule["highlight"] is None and rule["lit"] is None and rule["rows"] == 0


def test_an_encoding_aggregate_groups_by_the_other_channels(wafers):
    enc = {
        "x": {"field": "lot", "type": "nominal"},
        "y": {"field": "fail_rate", "type": "quantitative", "aggregate": "mean"},
    }
    [layer] = build(_spec(mark="bar", encoding=enc), wafers)["layers"]
    assert _cat(layer["columns"]["lot"]) == ["A", "B", "C"]
    assert _f64(layer["columns"]["fail_rate"]) == pytest.approx([0.25, 0.2, 0.2])


def test_count_aggregate_counts_rows_per_group(wafers):
    enc = {
        "x": {"field": "lot", "type": "nominal"},
        "y": {"field": "wafer", "type": "quantitative", "aggregate": "count"},
    }
    [layer] = build(_spec(mark="bar", encoding=enc), wafers)["layers"]
    assert _f64(layer["columns"]["wafer"]) == [2.0, 2.0, 1.0]


def test_a_highlight_on_an_aggregated_layer_is_on_its_rows(wafers):
    enc = {
        "x": {"field": "lot", "type": "nominal"},
        "y": {"field": "fail_rate", "type": "quantitative", "aggregate": "mean"},
    }
    spec = _spec(mark="bar", encoding=enc, highlight={"values": {"lot": ["B"]}})
    [layer] = build(spec, wafers)["layers"]
    assert _bits(layer["highlight"], 3) == [False, True, False]


def test_spec_transforms_run_before_layer_transforms(wafers):
    spec = _spec(
        transform=[{"filter": "lot != 'C'"}],
        layer=[
            {
                "mark": "scatter",
                "encoding": SCATTER,
                "transform": [{"filter": {"field": "thickness", "gt": 100}}],
            }
        ],
    )
    [layer] = build(spec, wafers)["layers"]
    assert _f64(layer["columns"]["thickness"]) == [101.0, 104.0]


def test_a_grid_colour_is_quantized_for_the_raster():
    df = pd.DataFrame({"x": [0, 1, 0, 1], "y": [0, 0, 1, 1], "v": [0.0, 1.0, 0.5, None]})
    enc = {
        "x": {"field": "x", "type": "ordinal"},
        "y": {"field": "y", "type": "ordinal"},
        "color": {"field": "v", "type": "quantitative"},
    }
    [layer] = build(_spec(mark="grid", encoding=enc), df)["layers"]
    color = layer["columns"]["v"]
    assert color["kind"] == "q8" and (color["min"], color["max"]) == (0.0, 1.0)
    assert list(base64.b64decode(color["codes"])) == [0, 254, 127, 255]


def test_a_heatmap_colour_keeps_exact_values():
    df = pd.DataFrame({"x": ["a"], "y": ["b"], "v": [0.123456]})
    enc = {
        "x": {"field": "x", "type": "nominal"},
        "y": {"field": "y", "type": "nominal"},
        "color": {"field": "v", "type": "quantitative"},
    }
    [layer] = build(_spec(mark="heatmap", encoding=enc), df)["layers"]
    assert _f64(layer["columns"]["v"]) == [0.123456]


def test_a_boxplot_sends_five_numbers_and_the_outliers():
    df = pd.DataFrame({"g": ["a"] * 9 + ["b"] * 3, "v": [1, 2, 3, 4, 5, 6, 7, 8, 100, 10, 11, 12]})
    enc = {"x": {"field": "g", "type": "nominal"}, "y": {"field": "v", "type": "quantitative"}}
    [layer] = build(_spec(mark="boxplot", encoding=enc), df)["layers"]
    cols = layer["columns"]
    assert _cat(cols["g"]) == ["a", "b"]
    # a: q1 3, median 5, q3 7, IQR 4 → whiskers reach the furthest data within
    # [q1 - 6, q3 + 6] = [-3, 13]: 1 and 8; 100 is an outlier.
    assert _f64(cols["$q1"])[0] == 3.0 and _f64(cols["$mid"])[0] == 5.0
    assert _f64(cols["$q3"])[0] == 7.0
    assert _f64(cols["$lo"])[0] == 1.0 and _f64(cols["$hi"])[0] == 8.0
    outliers = layer["outliers"]
    assert outliers["rows"] == 1
    assert _cat(outliers["columns"]["g"]) == ["a"] and _f64(outliers["columns"]["v"]) == [100.0]


def test_a_boxplot_with_no_outliers_sends_none():
    df = pd.DataFrame({"g": ["a"] * 3, "v": [1.0, 2.0, 3.0]})
    enc = {"x": {"field": "g", "type": "nominal"}, "y": {"field": "v", "type": "quantitative"}}
    [layer] = build(_spec(mark="boxplot", encoding=enc), df)["layers"]
    assert layer["outliers"] is None


@pytest.mark.parametrize(
    ("extent", "lo", "mid", "hi"),
    [
        # a = [2, 4, 6]: mean 4, stdev 2, stderr 2/sqrt(3); q1 3, median 4, q3 5.
        ("stderr", 4 - 2 / 3**0.5, 4.0, 4 + 2 / 3**0.5),
        ("stdev", 2.0, 4.0, 6.0),
        ("iqr", 3.0, 4.0, 5.0),
    ],
)
def test_an_errorbar_summarises_each_group(extent, lo, mid, hi):
    df = pd.DataFrame({"g": ["a"] * 3, "v": [2.0, 4.0, 6.0]})
    enc = {"x": {"field": "g", "type": "nominal"}, "y": {"field": "v", "type": "quantitative"}}
    [layer] = build(_spec(mark={"type": "errorbar", "extent": extent}, encoding=enc), df)["layers"]
    cols = layer["columns"]
    assert _f64(cols["$lo"]) == pytest.approx([lo])
    assert _f64(cols["$mid"]) == pytest.approx([mid])
    assert _f64(cols["$hi"]) == pytest.approx([hi])


def test_an_errorbar_defaults_to_stderr():
    df = pd.DataFrame({"g": ["a"] * 3, "v": [2.0, 4.0, 6.0]})
    enc = {"x": {"field": "g", "type": "nominal"}, "y": {"field": "v", "type": "quantitative"}}
    [layer] = build(_spec(mark="errorbar", encoding=enc), df)["layers"]
    assert _f64(layer["columns"]["$hi"]) == pytest.approx([4 + 2 / 3**0.5])


def test_an_errorbar_with_y2_draws_the_rows_as_given():
    df = pd.DataFrame({"g": ["a", "b"], "lo": [1.0, 2.0], "hi": [3.0, 5.0]})
    enc = {
        "x": {"field": "g", "type": "nominal"},
        "y": {"field": "lo", "type": "quantitative"},
        "y2": {"field": "hi", "type": "quantitative"},
    }
    [layer] = build(_spec(mark="errorbar", encoding=enc), df)["layers"]
    assert _f64(layer["columns"]["hi"]) == [3.0, 5.0]
    assert "$mid" not in layer["columns"]


def test_a_scatter_above_the_threshold_is_binned_and_says_so():
    rng = np.random.default_rng(0)
    n = 5000
    df = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    enc = {"x": {"field": "a", "type": "quantitative"}, "y": {"field": "b", "type": "quantitative"}}
    spec = _spec(mark="scatter", encoding=enc, bin_threshold=1000, highlight={"where": "a > 2"})
    [layer] = build(spec, df)["layers"]
    binned = layer["binned"]
    assert binned["points"] == n and binned["bins"] == layer["rows"] < n
    assert sum(c or 0 for c in _f64(layer["columns"]["$count"])) == n
    # A bin is lit when any of its points is.
    assert 0 < layer["lit"] <= int((df["a"] > 2).sum())


def test_binning_keeps_a_colour_category_apart():
    df = pd.DataFrame({"a": [0.0] * 4, "b": [0.0] * 4, "k": ["p", "q", "p", "q"]})
    enc = {
        "x": {"field": "a", "type": "quantitative"},
        "y": {"field": "b", "type": "quantitative"},
        "color": {"field": "k", "type": "nominal"},
    }
    [layer] = build(_spec(mark="scatter", encoding=enc, bin_threshold=2), df)["layers"]
    assert layer["rows"] == 2 and sorted(_cat(layer["columns"]["k"])) == ["p", "q"]
    assert _f64(layer["columns"]["$count"]) == [2.0, 2.0]


def test_the_default_threshold_is_ten_thousand():
    assert DEFAULT_BIN_THRESHOLD == 10_000


def test_a_missing_column_is_named(wafers):
    enc = {**SCATTER, "size": {"field": "area", "type": "quantitative"}}
    with pytest.raises(TransformError, match="'area'"):
        build(_spec(mark="scatter", encoding=enc), wafers)


def test_a_highlight_that_does_not_evaluate_is_named(wafers):
    spec = _spec(mark="scatter", encoding=SCATTER, highlight={"where": "fail_rate >"})
    with pytest.raises(TransformError, match="highlight"):
        build(spec, wafers)


def test_a_where_on_a_column_an_aggregated_layer_lost_leaves_it_unlit(wafers):
    # The bar layer keeps lot + mean fail_rate; `thickness` is gone from its rows.
    spec = _spec(
        highlight={"where": "thickness > 100"},
        layer=[
            {"mark": "scatter", "encoding": SCATTER},
            {
                "mark": "bar",
                "encoding": {
                    "x": {"field": "lot", "type": "nominal"},
                    "y": {"field": "fail_rate", "type": "quantitative", "aggregate": "mean"},
                },
            },
        ],
    )
    scatter, bar = build(spec, wafers)["layers"]
    assert scatter["lit"] == 2 and bar["lit"] is None


def test_values_on_a_column_a_layer_lacks_leave_it_unlit(wafers):
    enc = {
        "x": {"field": "lot", "type": "nominal"},
        "y": {"field": "fail_rate", "type": "quantitative", "aggregate": "mean"},
    }
    spec = _spec(mark="bar", encoding=enc, highlight={"values": {"wafer": [1]}})
    [layer] = build(spec, wafers)["layers"]
    assert layer["highlight"] is None


def test_a_field_in_two_channels_is_sent_once_as_its_first_channel_says(wafers):
    enc = {**SCATTER, "tooltip": [{"field": "thickness", "type": "nominal"}]}
    [layer] = build(_spec(mark="scatter", encoding=enc), wafers)["layers"]
    assert layer["columns"]["thickness"]["kind"] == "f64"


def test_a_tooltip_datum_is_refused_before_a_query(wafers):
    # A datum is drawn only as a rule's x or y ($defs.datumChannels): the
    # renderer read no tooltip datum, so the schema no longer lets one reach
    # query (it used to be sent as nothing).
    from chart_view.spec import spec_errors

    enc = {**SCATTER, "tooltip": {"datum": "wafer map"}}
    assert spec_errors(_spec(mark="scatter", encoding=enc)) == [
        "encoding.tooltip: a datum is drawn only as a rule's x or y — drop it and name a field here"
    ]


def test_a_temporal_scatter_is_binned_on_time_and_sent_as_time():
    n = 50
    df = pd.DataFrame(
        {
            "when": [f"2024-01-{1 + i % 28:02d}T00:00:00Z" for i in range(n)],
            "v": [float(i % 5) for i in range(n)],
        }
    )
    enc = {"x": {"field": "when", "type": "temporal"}, "y": {"field": "v", "type": "quantitative"}}
    [layer] = build(_spec(mark="scatter", encoding=enc, bin_threshold=10), df)["layers"]
    assert layer["binned"]["points"] == n
    assert layer["columns"]["when"]["kind"] == "time"
    times = [t for t in _f64(layer["columns"]["when"]) if t is not None]
    start = pd.Timestamp("2024-01-01", tz="UTC").value / 1e6
    end = pd.Timestamp("2024-01-28", tz="UTC").value / 1e6
    assert all(start <= t <= end for t in times)
    assert sum(c or 0 for c in _f64(layer["columns"]["$count"])) == n
