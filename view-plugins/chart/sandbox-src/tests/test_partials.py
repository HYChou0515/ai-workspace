"""`partials`: each aggregated bar split by a marking's keys (#861 D3).

The value of a bar over its picked rows must fold from what this answers --
`wire-corpus/bar-partials.json` holds the fold to the sandbox's own
`aggregate` over the picked rows, for the browser as for this side.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from chart_view import partials as partials_module
from chart_view.cli import main
from chart_view.partials import layer_partials, partials
from chart_view.query import BarSource, build, layer_rows
from chart_view.spec import parse_spec

HERE = Path(__file__).resolve()
GROUP = {"field": "group", "type": "nominal"}
ITEM = {"field": "item", "type": "nominal"}


def _layers(frame: pd.DataFrame, mark: Any, encoding: dict, keys=("group", "item")):
    spec = {
        "view": "chart",
        "source": "a.csv",
        "keys": list(keys),
        "mark": mark,
        "encoding": encoding,
    }
    return layer_rows(parse_spec(json.dumps(spec)), frame)


@pytest.fixture
def rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "group": ["g1", "g1", "g1", "g2", "g2", "g3"],
            "item": ["1", "1", "2", "1", None, "3"],
            "value": [2.0, 4.0, 10.0, 5.0, 7.0, None],
            "region": ["n", "s", "n", "n", "s", "s"],
        }
    )


def _value(op: str, entries: list[list[Any]]) -> float | None:
    """The fold, as the browser's `partials.ts:foldPartials` has it."""
    if not entries:
        return None
    n = sum(e[1] for e in entries)
    total = sum(e[2] for e in entries)
    if op == "count":
        return n
    if op == "sum":
        return total
    if op in ("mean", "rate"):
        return total / n if n else None
    picked = [e[3 if op == "min" else 4] for e in entries if e[3 if op == "min" else 4] is not None]
    return (min if op == "min" else max)(picked) if picked else None


def test_a_count_bar_is_split_by_every_key_its_rows_carry(rows):
    enc = {"x": GROUP, "y": {"field": "item", "type": "quantitative", "aggregate": "count"}}
    [layer] = _layers(rows, "bar", enc)
    got = layer_partials(layer, ["group", "item"])
    assert got is not None and "whole" not in got
    assert got["by"] == ["group", "item"] and got["op"] == "count"
    # a row with no item can never be picked: it is in no partial
    assert got["keys"] == [["g1", "1"], ["g1", "2"], ["g2", "1"], ["g3", "3"]]
    # g1: (g1, 1) twice and (g1, 2) once; g2: (g2, 1); g3: (g3, 3)
    assert got["bars"] == [
        [[0, 2.0, 0.0, None, None], [1, 1.0, 0.0, None, None]],
        [[2, 1.0, 0.0, None, None]],
        [[3, 1.0, 0.0, None, None]],
    ]


def test_its_bars_are_the_drawn_rows_in_order(rows):
    # the order `build` draws them in: g1, g2, g3
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "mean"}}
    spec = {
        "view": "chart",
        "source": "a.csv",
        "keys": ["group", "item"],
        "mark": "bar",
        "encoding": enc,
    }
    [drawn] = build(parse_spec(json.dumps(spec)), rows)["layers"]
    [layer] = _layers(rows, "bar", enc)
    got = layer_partials(layer, ["group", "item"])
    assert got is not None and "bars" in got
    assert len(got["bars"]) == drawn["rows"] == 3
    # each bar's own partials hold its own group only
    for b, group in enumerate(["g1", "g2", "g3"]):
        assert {got["keys"][e[0]][0] for e in got["bars"][b]} == {group}


def test_a_mean_bar_carries_n_sum_min_max_of_its_numbers(rows):
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "mean"}}
    [layer] = _layers(rows, "bar", enc)
    got = layer_partials(layer, ["group", "item"])
    assert got is not None and "bars" in got
    assert got["bars"][0] == [[0, 2.0, 6.0, 2.0, 4.0], [1, 1.0, 10.0, 10.0, 10.0]]
    # (g3, 3) has no number: n 0, nothing to take a min or max of
    assert got["bars"][2] == [[3, 0.0, 0.0, None, None]]


def test_a_rate_bar_counts_every_row_and_the_ones_that_passed():
    frame = pd.DataFrame({"group": ["a", "a", "a"], "item": ["1", "2", "2"], "ok": [1, 0, None]})
    enc = {"x": GROUP, "y": {"field": "ok", "type": "quantitative", "aggregate": "rate"}}
    [layer] = _layers(frame, "bar", enc)
    got = layer_partials(layer, ["group", "item"])
    assert got is not None and "bars" in got
    # a missing value is a row that did not pass (transforms._rate)
    assert got["bars"] == [[[0, 1.0, 1.0, None, None], [1, 2.0, 0.0, None, None]]]


def test_a_stacked_bar_is_split_per_segment(rows):
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative"}, "color": ITEM}
    [layer] = _layers(rows, {"type": "bar", "stack": True}, enc, keys=("region",))
    got = layer_partials(layer, ["region"])
    assert got is not None and "bars" in got
    # segments (g1, 1) (g1, 2) (g2, 1) (g2, -) (g3, 3), each split by region
    assert got["keys"] == [["n"], ["s"]]
    assert got["bars"] == [
        [[0, 1.0, 2.0, 2.0, 2.0], [1, 1.0, 4.0, 4.0, 4.0]],
        [[0, 1.0, 10.0, 10.0, 10.0]],
        [[0, 1.0, 5.0, 5.0, 5.0]],
        [[1, 1.0, 7.0, 7.0, 7.0]],
        [[1, 0.0, 0.0, None, None]],
    ]


def test_a_bar_with_no_group_is_one_bar(rows):
    enc = {"y": {"field": "value", "type": "quantitative", "aggregate": "sum"}}
    [layer] = _layers(rows, "bar", enc)
    got = layer_partials(layer, ["region"])
    assert got is not None and "bars" in got
    assert got["bars"] == [[[0, 3.0, 17.0, 2.0, 10.0], [1, 2.0, 11.0, 4.0, 7.0]]]


def test_an_empty_frame_has_no_bars(rows):
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "sum"}}
    [layer] = _layers(rows.iloc[0:0], "bar", enc)
    assert layer_partials(layer, ["group", "item"]) == {
        "by": ["group", "item"],
        "op": "sum",
        "keys": [],
        "bars": [],
    }


@pytest.mark.parametrize(
    ("mark", "encoding", "by"),
    [
        # no aggregate: each bar is a row, lit exactly already
        ("bar", {"x": GROUP, "y": {"field": "value", "type": "quantitative"}}, ["group", "item"]),
        # not a bar
        (
            "line",
            {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "sum"}},
            ["item"],
        ),
        # its value channel is not the one aggregated
        (
            "bar",
            {
                "x": GROUP,
                "y": {"field": "value", "type": "quantitative"},
                "color": {"field": "item", "type": "quantitative", "aggregate": "count"},
            },
            ["region"],
        ),
        # a stacked area is no bar
        (
            {"type": "area", "stack": True},
            {"x": GROUP, "y": {"field": "value", "type": "quantitative"}},
            ["region"],
        ),
    ],
    ids=["unaggregated", "a line", "value not aggregated", "a stacked area"],
)
def test_a_layer_that_is_no_aggregated_bar_is_not_split(rows, mark, encoding, by):
    [layer] = _layers(rows, mark, encoding)
    assert layer_partials(layer, by) is None


def test_a_bar_whose_rows_lack_a_key_is_not_split(rows):
    # keyed on fewer than the marking's keys, a partial would light rows no
    # one picked: lit whole instead (D4)
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "sum"}}
    [layer] = _layers(rows, "bar", enc)
    assert layer_partials(layer, ["group", "unit"]) is None
    assert layer_partials(layer, []) is None


def test_a_bar_one_key_tuple_each_is_not_split(rows):
    # every bar is one pick or none: lit whole is exact
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "sum"}}
    [layer] = _layers(rows, "bar", enc)
    assert layer_partials(layer, ["group"]) is None


def test_an_op_that_does_not_fold_is_lit_whole(rows):
    layer = _layers(
        rows,
        "bar",
        {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "sum"}},
    )[0]
    layer.bar = BarSource(rows, ["group"], "value", "median")
    got = layer_partials(layer, ["group", "item"])
    assert got == {"whole": "a bar of a median lights whole: it cannot be split by the marking"}


def test_past_the_entry_cap_a_layer_is_lit_whole(rows, monkeypatch):
    enc = {"x": GROUP, "y": {"field": "value", "type": "quantitative", "aggregate": "sum"}}
    [layer] = _layers(rows, "bar", enc)
    monkeypatch.setattr(partials_module, "MAX_ENTRIES", 3)
    got = layer_partials(layer, ["group", "item"])
    assert got == {"whole": "lit whole: split by the marking it would be over 3 parts"}
    monkeypatch.setattr(partials_module, "MAX_ENTRIES", 4)
    assert "bars" in (layer_partials(layer, ["group", "item"]) or {})


def test_partials_answers_every_layer(rows):
    spec = {
        "view": "chart",
        "source": "a.csv",
        "keys": ["group", "item"],
        "layer": [
            {
                "mark": "bar",
                "encoding": {
                    "x": GROUP,
                    "y": {"field": "value", "type": "quantitative", "aggregate": "sum"},
                },
            },
            {
                "mark": "scatter",
                "encoding": {"x": GROUP, "y": {"field": "value", "type": "quantitative"}},
            },
        ],
    }
    out = partials(layer_rows(parse_spec(json.dumps(spec)), rows), ["group", "item"])
    assert out["format"] == 1
    assert out["layers"][1] is None
    assert out["layers"][0]["op"] == "sum"


# ── the command ──────────────────────────────────────────────────────────────

SPEC = """\
view: chart
source: data/a.csv
keys: [group, item]
mark: bar
encoding:
  x: {field: group, type: nominal}
  y: {field: value, type: quantitative, aggregate: sum}
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.csv").write_text("group,item,value\ng1,1,2\ng1,2,3\ng2,1,4\n")
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "c.ai.yaml").write_text(SPEC)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_command_answers_by_path_as_by_text(workspace, capsys):
    by = ["group", "item"]
    assert main(["partials", json.dumps({"path": "views/c.ai.yaml", "rev": "r", "by": by})]) == 0
    by_path = capsys.readouterr().out
    assert main(["partials", json.dumps({"spec": SPEC, "by": by})]) == 0
    assert capsys.readouterr().out == by_path
    [layer] = json.loads(by_path)["layers"]
    assert layer["keys"] == [["g1", "1"], ["g1", "2"], ["g2", "1"]]


def test_the_command_refuses_what_query_refuses(workspace, capsys):
    assert (
        main(
            [
                "partials",
                json.dumps({"spec": SPEC.replace("field: value", "field: z"), "by": ["group"]}),
            ]
        )
        == 2
    )
    assert "'z'" in capsys.readouterr().err
    assert (
        main(
            [
                "partials",
                json.dumps({"spec": SPEC.replace("mark: bar", "mark: nope"), "by": ["group"]}),
            ]
        )
        == 2
    )
    assert capsys.readouterr().err
    assert main(["partials", json.dumps({"spec": "view: [chart", "by": ["group"]})]) == 2
    assert capsys.readouterr().err
    assert main(["partials", json.dumps({"path": "views/nope.ai.yaml", "by": ["group"]})]) == 2
    assert "views/nope.ai.yaml" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        {"spec": SPEC},
        {"spec": SPEC, "by": "group"},
        {"spec": SPEC, "by": [1]},
        {"by": ["group"]},
        {"spec": SPEC, "path": "views/c.ai.yaml", "by": ["group"]},
        [],
    ],
)
def test_the_command_takes_a_view_and_by(workspace, capsys, args):
    assert main(["partials", json.dumps(args)]) == 2
    assert "argument must be" in capsys.readouterr().err


def test_the_command_describes_its_arguments(capsys):
    assert main(["partials"]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["name"] == "partials" and meta["description"]
    assert meta["params_json_schema"]["properties"]["by"]["type"] == "array"


# ── the corpus: the fold is the sandbox's aggregate over the picked rows ─────


def _writer():
    script = importlib.util.spec_from_file_location(
        "write_partials_corpus", HERE.parents[1] / "scripts" / "write_partials_corpus.py"
    )
    assert script is not None and script.loader is not None
    writer = importlib.util.module_from_spec(script)
    script.loader.exec_module(writer)
    return writer


def test_the_partials_corpus_is_current():
    # the renderer's partials.corpus.test.ts folds each stored answer in the
    # browser and holds it to the stored oracle: both must be what this says
    writer = _writer()
    doc = json.loads(writer.CORPUS.read_text())
    assert doc == {"kind": "bar-partials", "cases": writer.cases()}


def test_the_fold_is_the_sandbox_aggregate_over_the_picked_rows():
    for c in _writer().cases():
        for layer, oracle in zip(c["partials"]["layers"], c["oracle"], strict=True):
            if layer is None:
                assert oracle is None, c["name"]
                continue
            picks = {tuple(p) for p in c["picks"]}
            lit = [tuple(k) in picks for k in layer["keys"]]
            for bar, want in zip(layer["bars"], oracle, strict=True):
                got = _value(layer["op"], [e for e in bar if lit[e[0]]])
                if want is None or got is None:
                    assert got == want, c["name"]
                else:
                    assert math.isclose(got, want, rel_tol=1e-12), (c["name"], got, want)
