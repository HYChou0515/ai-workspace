"""A stacked layer is summed here, by its slot and colour (#847/#848 PR 5 P40
row 18): exactly an `aggregate: sum` on its value channel. The browser draws
one row per slot and colour, as for any aggregated layer; P37 had summed in
the browser, a second aggregation beside this one, and every channel needed
its own rule for it. The oracle is pandas' own groupby sum."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from chart_view.query import build
from chart_view.sources import read_source
from chart_view.spec import parse_spec


def _f64(col: dict) -> list[float | None]:
    raw = np.frombuffer(base64.b64decode(col["data"]), dtype="<f8")
    return [None if np.isnan(v) else float(v) for v in raw]


def _cat(col: dict) -> list:
    width = col["width"]
    codes = np.frombuffer(base64.b64decode(col["codes"]), dtype={1: "<u1", 2: "<u2"}[width])
    missing = 2 ** (8 * width) - 1
    return [None if c == missing else col["levels"][c] for c in codes]


def _spec(mark, encoding, **kw) -> dict:
    return {"view": "chart", "source": "data/a.csv", "mark": mark, "encoding": encoding, **kw}


@pytest.fixture
def rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "item": list("ppqpqqqr"),
            "group": list("aaabbbab"),
            "value": [3.0, 4.0, 7.0, 5.0, 1.0, 2.0, 6.0, 8.0],
            "region": ["n", "n", "s", "n", "s", "s", "n", "s"],
            "size": [1, 1, 2, 3, 4, 4, 5, 6],
        }
    )


ITEM = {"field": "item", "type": "nominal"}
GROUP = {"field": "group", "type": "nominal"}
VALUE = {"field": "value", "type": "quantitative"}
STACKED = {"type": "bar", "stack": True}


def _drawn(layer: dict, slot: str, colour: str | None = "group") -> dict:
    """(slot, colour) -> value, as the answer holds it."""
    slots = _cat(layer["columns"][slot])
    colours = _cat(layer["columns"][colour]) if colour else [None] * layer["rows"]
    values = _f64(layer["columns"]["value"])
    return {(s, c): v for s, c, v in zip(slots, colours, values, strict=True)}


def _oracle(df: pd.DataFrame, by: list[str]) -> dict:
    sums = df.groupby(by, dropna=False)["value"].sum()
    return {(k if isinstance(k, tuple) else (k, None)): float(v) for k, v in sums.items()}


def test_a_stacked_bar_is_one_row_per_slot_and_colour_its_sum(rows):
    [layer] = build(_spec(STACKED, {"x": ITEM, "y": VALUE, "color": GROUP}), rows)["layers"]
    assert layer["rows"] == 5  # p·a q·a p·b q·b r·b
    assert _drawn(layer, "item") == _oracle(rows, ["item", "group"])


def test_a_horizontal_bar_is_summed_by_its_y(rows):
    [layer] = build(_spec(STACKED, {"x": VALUE, "y": ITEM, "color": GROUP}), rows)["layers"]
    assert _drawn(layer, "item") == _oracle(rows, ["item", "group"])


@pytest.mark.parametrize("kind", ["ordinal", "nominal"])
def test_a_stacked_area_the_same_and_an_ordinal_slot_too(rows, kind):
    enc = {"x": {"field": "item", "type": kind}, "y": VALUE, "color": GROUP}
    [layer] = build(_spec({"type": "area", "stack": True}, enc), rows)["layers"]
    assert _drawn(layer, "item") == _oracle(rows, ["item", "group"])


def test_a_stack_with_no_colour_is_summed_by_its_slot_alone(rows):
    [layer] = build(_spec(STACKED, {"x": ITEM, "y": VALUE}), rows)["layers"]
    assert _drawn(layer, "item", None) == _oracle(rows, ["item"])


def test_a_stack_on_a_number_x_is_summed_by_each_x(rows):
    df = rows.assign(at=[1, 1, 2, 1, 2, 2, 2, 3])
    enc = {"x": {"field": "at", "type": "quantitative"}, "y": VALUE, "color": GROUP}
    [layer] = build(_spec({"type": "area", "stack": True}, enc), df)["layers"]
    got = dict(
        zip(
            zip(_f64(layer["columns"]["at"]), _cat(layer["columns"]["group"]), strict=True),
            _f64(layer["columns"]["value"]),
            strict=True,
        )
    )
    want = {
        (float(k[0]), k[1]): float(v) for k, v in df.groupby(["at", "group"])["value"].sum().items()
    }
    assert got == want


def test_a_temporal_colour_splits_a_stack_as_the_chart_splits_its_series(rows):
    # the renderer draws one series per value of any colour but a quantitative one
    df = rows.assign(day=["2024-01-01", "2024-01-02"] * 4)
    enc = {"x": ITEM, "y": VALUE, "color": {"field": "day", "type": "temporal"}}
    [layer] = build(_spec(STACKED, enc), df)["layers"]
    assert layer["rows"] == len(df.groupby(["item", "day"]))


def test_other_channels_keep_a_value_the_group_shares_else_none(rows):
    enc = {
        "x": ITEM,
        "y": VALUE,
        "color": GROUP,
        "tooltip": [{"field": "region", "type": "nominal"}],
        "size": {"field": "size", "type": "quantitative"},
    }
    [layer] = build(_spec(STACKED, enc), rows)["layers"]
    slots = list(zip(_cat(layer["columns"]["item"]), _cat(layer["columns"]["group"]), strict=True))
    region = dict(zip(slots, _cat(layer["columns"]["region"]), strict=True))
    size = dict(zip(slots, _f64(layer["columns"]["size"]), strict=True))
    # p·a: rows 0, 1 (n, n; 1, 1); q·a: rows 2, 6 (s, n; 2, 5); q·b: rows 4, 5 (s, s; 4, 4)
    assert region == {
        ("p", "a"): "n",
        ("q", "a"): None,
        ("p", "b"): "n",
        ("q", "b"): "s",
        ("r", "b"): "s",
    }
    assert size == {
        ("p", "a"): 1.0,
        ("q", "a"): None,
        ("p", "b"): 3.0,
        ("q", "b"): 4.0,
        ("r", "b"): 6.0,
    }


def test_an_integer_label_the_group_shares_stays_an_integer(rows):
    # a shared value is the rows' own, not a float the missing ones turned it into
    enc = {"x": ITEM, "y": VALUE, "color": GROUP, "text": {"field": "size", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc), rows)["layers"]
    assert sorted(v for v in _cat(layer["columns"]["size"]) if v is not None) == [1, 3, 4, 6]
    assert all(isinstance(v, int) for v in layer["columns"]["size"]["levels"])


def test_a_true_false_label_the_group_shares_is_kept(rows):
    df = rows.assign(flag=[True, True, False, True, False, False, True, False])
    enc = {"x": ITEM, "y": VALUE, "color": GROUP, "tooltip": {"field": "flag", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc), df)["layers"]
    slots = list(zip(_cat(layer["columns"]["item"]), _cat(layer["columns"]["group"]), strict=True))
    got = dict(zip(slots, _cat(layer["columns"]["flag"]), strict=True))
    assert got == {
        ("p", "a"): True,
        ("q", "a"): None,
        ("p", "b"): True,
        ("q", "b"): False,
        ("r", "b"): False,
    }
    # true / false, not 1 / 0 (which compare equal to them above)
    assert all(isinstance(v, bool) for v in layer["columns"]["flag"]["levels"])


def test_a_value_beside_a_missing_one_is_not_shared(rows):
    # p·a's rows hold n and nothing: they do not share one value
    df = rows.assign(region=["n", None, "s", "n", "s", "s", "n", "s"])
    enc = {"x": ITEM, "y": VALUE, "color": GROUP, "tooltip": {"field": "region", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc), df)["layers"]
    slots = list(zip(_cat(layer["columns"]["item"]), _cat(layer["columns"]["group"]), strict=True))
    assert dict(zip(slots, _cat(layer["columns"]["region"]), strict=True))[("p", "a")] is None


def test_a_list_field_is_compared_as_its_text(rows):
    # an entity field can hold a list, which has no hash
    df = rows.assign(tags=[["x"], ["x"], ["y"], ["x"], ["y"], ["z"], ["x"], ["y"]])
    enc = {"x": ITEM, "y": VALUE, "color": GROUP, "tooltip": {"field": "tags", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc), df)["layers"]
    slots = list(zip(_cat(layer["columns"]["item"]), _cat(layer["columns"]["group"]), strict=True))
    got = dict(zip(slots, _cat(layer["columns"]["tags"]), strict=True))
    # (a list travels as its marking text)
    assert got == {
        ("p", "a"): "['x']",
        ("q", "a"): None,
        ("p", "b"): "['x']",
        ("q", "b"): None,
        ("r", "b"): "['y']",
    }


def test_a_missing_value_adds_nothing_and_a_group_of_none_sums_to_0(rows):
    # as `aggregate: sum` does (pandas' sum skips a missing value; Vega-Lite's
    # sum of none is 0 too)
    df = rows.assign(value=[3.0, None, None, 5.0, None, None, 6.0, 8.0])
    [layer] = build(_spec(STACKED, {"x": ITEM, "y": VALUE, "color": GROUP}), df)["layers"]
    assert _drawn(layer, "item") == {
        ("p", "a"): 3.0,
        ("q", "a"): 6.0,
        ("p", "b"): 5.0,
        ("q", "b"): 0.0,
        ("r", "b"): 8.0,
    }
    assert _drawn(layer, "item") == _oracle(df, ["item", "group"])


def test_a_stack_says_what_it_summed_and_what_it_kept_where_shared(rows):
    # a summed or kept-where-shared field is no key: a brush over a sum writes
    # its slot and colour, which the marking lights row by row (P32)
    enc = {"x": ITEM, "y": VALUE, "color": GROUP, "tooltip": {"field": "region", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc, keys=["item", "group", "region"]), rows)["layers"]
    assert layer["measured"] == ["region", "value"]


def test_an_aggregated_layer_says_what_it_aggregated(rows):
    enc = {"x": ITEM, "y": {**VALUE, "aggregate": "mean"}, "color": GROUP}
    [layer] = build(_spec("bar", enc), rows)["layers"]
    assert layer["measured"] == ["value"]
    [plain] = build(_spec("bar", {"x": ITEM, "y": VALUE}), rows)["layers"]
    assert plain["measured"] == []


def test_a_stack_with_an_aggregate_groups_by_its_slot_and_colour_alone(rows):
    # a mean per slot and colour, not per slot, colour and tooltip
    enc = {
        "x": ITEM,
        "y": {**VALUE, "aggregate": "mean"},
        "color": GROUP,
        "tooltip": {"field": "region", "type": "nominal"},
    }
    [layer] = build(_spec(STACKED, enc), rows)["layers"]
    means = rows.groupby(["item", "group"])["value"].mean()
    assert _drawn(layer, "item") == {k: float(v) for k, v in means.items()}
    assert layer["measured"] == ["region", "value"]


def test_a_slot_key_sent_as_time_is_sent_as_marking_text_too(rows):
    df = rows.assign(day=["2024-01-01", "2024-01-02"] * 4)
    enc = {"x": {"field": "day", "type": "temporal"}, "y": VALUE, "color": GROUP}
    [layer] = build(_spec(STACKED, enc, keys=["day"]), df)["layers"]
    assert layer["rows"] == 4
    assert sorted(_cat(layer["columns"]["$key.day"])) == [
        "2024-01-01",
        "2024-01-01",
        "2024-01-02",
        "2024-01-02",
    ]


@pytest.mark.parametrize(
    "mark", [{"type": "line", "stack": True}, {"type": "bar", "stack": False}, "bar", "scatter"]
)
def test_a_layer_that_is_not_stacked_keeps_its_rows(rows, mark):
    [layer] = build(_spec(mark, {"x": ITEM, "y": VALUE, "color": GROUP}), rows)["layers"]
    assert layer["rows"] == 8 and layer["measured"] == []


def test_a_colour_by_value_is_no_group_of_a_stack(rows):
    # `validate` refuses it (a stack coloured by value has no segments); fed
    # one anyway, the stack is its slots' sums, the colour kept where shared
    enc = {"x": ITEM, "y": VALUE, "color": {"field": "size", "type": "quantitative"}}
    [layer] = build(_spec(STACKED, enc), rows)["layers"]
    assert _drawn(layer, "item", None) == _oracle(rows, ["item"])
    assert layer["measured"] == ["size", "value"]


def test_a_stack_coloured_by_its_own_value_field_is_summed_by_its_slot(rows):
    enc = {"x": ITEM, "y": VALUE, "color": {"field": "value", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc), rows)["layers"]
    assert _drawn(layer, "item", None) == _oracle(rows, ["item"])


def test_a_highlight_on_a_stack_is_on_its_sums(rows):
    enc = {"x": ITEM, "y": VALUE, "color": GROUP}
    [layer] = build(_spec(STACKED, enc, highlight={"where": "value > 10"}), rows)["layers"]
    assert layer["lit"] == 1  # q·a: 7 + 6


def test_the_stack_corpus_is_what_query_and_pandas_say():
    # The renderer's echarts.stacksandbox.test.ts draws each stored answer and
    # holds its stack tops to the stored pandas tops: both must be current.
    here = Path(__file__).resolve()
    script = importlib.util.spec_from_file_location(
        "write_stack_corpus", here.parents[1] / "scripts" / "write_stack_corpus.py"
    )
    assert script is not None and script.loader is not None
    writer = importlib.util.module_from_spec(script)
    script.loader.exec_module(writer)
    doc = json.loads((here.parents[2] / "wire-corpus" / "stack-sums.json").read_text())
    assert [c["name"] for c in doc["cases"]] == [c[0] for c in writer.CASES]
    for c in doc["cases"]:
        frame = writer.frame_of(c["data"])
        assert build(parse_spec(json.dumps(c["spec"])), frame) == c["answer"], c["name"]
        # the oracle, restated in plain Python: per colour, the running sum
        # of per-slot sums (a missing value adds nothing, a missing slot last)
        enc = c["spec"]["encoding"]
        horizontal = enc["y"]["type"] == "nominal"
        if enc["x" if horizontal else "y"].get("scale"):
            assert c["tops"] is None, c["name"]
            continue
        slots = c["data"][enc["y" if horizontal else "x"]["field"]]
        colours = c["data"][enc["color"]["field"]] if "color" in enc else ["all"] * len(slots)
        sums: dict[tuple, float] = {}
        for s, k, v in zip(slots, colours, c["data"]["value"], strict=True):
            sums[k, s] = sums.get((k, s), 0) + (v or 0)
        order = sorted({s for s in slots if s is not None}) + ([None] if None in slots else [])
        running = [0.0] * len(order)
        want = []
        for k in sorted(set(colours)):
            running = [r + sums.get((k, s), 0) for r, s in zip(running, order, strict=True)]
            want.append(running)
        assert want == c["tops"], c["name"]


# #847/#848 PR 5 P41 row 22: a parquet file keeps a category column as a
# pandas Categorical. `aggregate` grouped every combination of categories
# (pandas' default, observed=False) while `_shared` numbered only the groups
# that occur, so a segment took another's kept value and a phantom 0 row
# appeared. One grouping, the observed one, for both. The oracle is pandas'
# own groupby over the same rows as plain text.


def _as_categories(df: pd.DataFrame, tmp_path: Path) -> pd.DataFrame:
    """`df` written to a parquet file with its text columns as categories and
    read back the way a chart's source is."""
    text = [c for c in df.columns if df[c].dtype == object]
    df.astype(dict.fromkeys(text, "category")).to_parquet(tmp_path / "a.parquet", index=False)
    read = read_source(tmp_path, "a.parquet")
    assert all(isinstance(read[c].dtype, pd.CategoricalDtype) for c in text)
    return read


def _one_or_none(s: pd.Series):
    return s.iloc[0] if s.nunique(dropna=False) == 1 else None


def test_a_stack_over_categories_has_one_row_per_group_that_occurs(rows, tmp_path):
    enc = {"x": ITEM, "y": VALUE, "color": GROUP, "tooltip": {"field": "region", "type": "nominal"}}
    [layer] = build(_spec(STACKED, enc), _as_categories(rows, tmp_path))["layers"]
    assert _drawn(layer, "item") == _oracle(rows, ["item", "group"])
    slots = list(zip(_cat(layer["columns"]["item"]), _cat(layer["columns"]["group"]), strict=True))
    got = dict(zip(slots, _cat(layer["columns"]["region"]), strict=True))
    assert got == rows.groupby(["item", "group"])["region"].agg(_one_or_none).to_dict()


@pytest.mark.parametrize("op", ["sum", "count"])
def test_an_aggregated_layer_over_categories_has_no_phantom_rows(rows, tmp_path, op):
    # a sum and a count are grouped apart (`transforms._grouped`): both, once
    enc = {"x": ITEM, "y": {**VALUE, "aggregate": op}, "color": GROUP}
    [layer] = build(_spec("bar", enc), _as_categories(rows, tmp_path))["layers"]
    want = rows.groupby(["item", "group"])["value"].agg(op)
    assert _drawn(layer, "item") == {k: float(v) for k, v in want.items()}


def test_an_errorbar_over_categories_has_no_phantom_groups(rows, tmp_path):
    # the same grouping bug, where a mark summarises its groups itself
    enc = {"x": ITEM, "y": VALUE, "color": GROUP}
    mark = {"type": "errorbar", "extent": "stdev"}
    [layer] = build(_spec(mark, enc), _as_categories(rows, tmp_path))["layers"]
    assert layer["rows"] == rows.groupby(["item", "group"]).ngroups


def test_a_boxplot_over_categories_summarises_the_groups_that_occur(rows, tmp_path):
    # (pandas' iteration already skipped an empty group; its FutureWarning,
    # an error under this suite's settings, is what pins the explicit rule)
    [layer] = build(
        _spec("boxplot", {"x": ITEM, "y": VALUE, "color": GROUP}), _as_categories(rows, tmp_path)
    )["layers"]
    assert layer["rows"] == rows.groupby(["item", "group"]).ngroups


def test_a_binned_scatter_over_categories_has_no_phantom_cells(rows, tmp_path):
    enc = {"x": VALUE, "y": VALUE, "color": GROUP}
    spec = _spec("scatter", enc, bin_threshold=1)
    [plain] = build(spec, rows)["layers"]
    [layer] = build(spec, _as_categories(rows, tmp_path))["layers"]
    assert layer["binned"] == plain["binned"] == {"points": 8, "bins": 8}
