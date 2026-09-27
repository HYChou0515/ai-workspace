"""`lit_rows` — the rows a marking lights, as CSV (plan-view-plugins-pr5-finish P7).

"Save as table" on a marking's header control or its chat chip runs this over
the view the action was taken in: its `source:` (a table file or entity
records) with its `transform:` applied, then lit by the platform's one rule
(the SPA's `isLit`, #861 D1/D2): the marking holds picked key TUPLES; a row
shares at least one key column with it, and its `canon` values on the keys it
shares are one of the picked tuples projected onto those keys — an exact tuple
match when the view has every key.

The oracle is that rule written out row by row, and every lit row's projected
tuple is checked to be one the marking picked.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest

from chart_view.cli import main
from chart_view.sources import read_source
from chart_view.transforms import apply_transforms
from chart_view.wire import canon

RUNS = """\
lot,wafer,value,day
A,1,0.5,2024-03-01
A,2,1.0,2024-03-01
B,1,2.5,2024-03-02
C,3,7,2024-03-03
C,4,,2024-03-04
D,1,3,2024-03-05
"""

#: A gallery's tiles: three groups by three items, and one more.
TILES = """\
group,item,v
g3,1,1
g3,2,2
g3,8,3
g4,1,4
g4,2,5
g4,8,6
g5,1,7
g5,2,8
g5,8,9
g6,1,10
"""

#: A per-group summary: `group` only.
GROUPS = """\
group,n
g1,1
g2,2
g3,3
g4,4
g5,5
g6,6
"""

NOT_A_MARKING = "the marking's file does not hold a marking"

#: The four tiles boxed in #861's report.
BOXED = [["g3", "8"], ["g4", "1"], ["g5", "1"], ["g5", "2"]]

CHART = """\
view: chart
source: data/runs.csv
keys: [lot]
marking: fail
mark: scatter
encoding:
  x: {field: wafer, type: quantitative}
  y: {field: value, type: quantitative}
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runs.csv").write_text(RUNS)
    (tmp_path / "data" / "tiles.csv").write_text(TILES)
    (tmp_path / "data" / "groups.csv").write_text(GROUPS)
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "c.ai.yaml").write_text(CHART)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(capsys, args: dict) -> tuple[int, dict | None, str]:
    code = main(["lit_rows", json.dumps(args)])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else None), captured.err


def _oracle(frame: pd.DataFrame, keys: list[str], rows: list[list[str]]) -> pd.DataFrame:
    """The SPA's `isLit`, written out row by row: project each picked tuple onto
    the keys the frame has, and keep the rows whose own tuple is one of them."""
    at = [i for i, k in enumerate(keys) if k in frame.columns]
    picked = {tuple(r[i] for i in at) for r in rows}
    keep = [
        bool(at) and tuple(canon(frame[keys[i]].iloc[j]) for i in at) in picked
        for j in range(len(frame))
    ]
    return frame[pd.Series(keep, index=frame.index, dtype=bool)].reset_index(drop=True)


def _read(csv: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv))


def _assert_parity(frame: pd.DataFrame, keys: list[str], rows: list[list[str]], csv: str) -> None:
    lit = _oracle(frame, keys, rows)
    pd.testing.assert_frame_equal(_read(csv), _read(lit.to_csv(index=False)))
    at = [i for i, k in enumerate(keys) if k in frame.columns]
    picked = {tuple(r[i] for i in at) for r in rows}
    for j in range(len(lit)):
        assert tuple(canon(lit[keys[i]].iloc[j]) for i in at) in picked


def test_every_column_of_the_rows_a_marking_lights(workspace, capsys):
    keys, rows = ["lot"], [["A"], ["C"]]
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "keys": keys, "rows": rows})
    assert code == 0, err
    assert out is not None
    assert out["rows"] == 4
    assert list(_read(out["csv"]).columns) == ["lot", "wafer", "value", "day"]
    _assert_parity(read_source(workspace, "data/runs.csv"), keys, rows, out["csv"])


def test_boxing_four_tiles_lights_those_four_rows_not_every_combination(workspace, capsys):
    # #861 D1: the marking remembers the picked (group, item) tuples. Tested
    # column by column, {g3, g4, g5} x {8, 1, 2} lit all nine tiles.
    keys = ["group", "item"]
    code, out, err = _run(capsys, {"view": "data/tiles.csv", "keys": keys, "rows": BOXED})
    assert code == 0, err
    assert out is not None and out["rows"] == 4
    got = _read(out["csv"])
    assert [[g, str(i)] for g, i in zip(got["group"], got["item"], strict=True)] == [
        ["g3", "8"],
        ["g4", "1"],
        ["g5", "1"],
        ["g5", "2"],
    ]
    _assert_parity(read_source(workspace, "data/tiles.csv"), keys, BOXED, out["csv"])


def test_a_view_with_only_some_keys_lights_what_contains_a_pick(workspace, capsys):
    # #861 D2: a per-group summary has `group` only; the picked tuples project
    # onto it, so the groups holding a pick light — g3, g4, g5, each once.
    keys = ["group", "item"]
    code, out, err = _run(capsys, {"view": "data/groups.csv", "keys": keys, "rows": BOXED})
    assert code == 0, err
    assert out is not None and out["rows"] == 3
    assert list(_read(out["csv"])["group"]) == ["g3", "g4", "g5"]
    _assert_parity(read_source(workspace, "data/groups.csv"), keys, BOXED, out["csv"])


def test_a_sent_marking_is_read_from_its_file(workspace, capsys):
    # The chat chip has no view at hand: the marking comes from the file the
    # send wrote, `.markings/<name>.json` (api/markings.py's shape).
    (workspace / ".markings").mkdir()
    doc = {"name": "fail", "sources": ["views/c.ai.yaml"], "keys": ["lot"], "rows": [["B"], ["D"]]}
    (workspace / ".markings" / "fail.json").write_text(json.dumps(doc))
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "marking": "/.markings/fail.json"})
    assert code == 0, err
    assert out is not None and out["rows"] == 2
    _assert_parity(read_source(workspace, "data/runs.csv"), doc["keys"], doc["rows"], out["csv"])
    # The answer says which marking it lit by — exactly the file's — so the
    # platform can check it is the one the chip sent, not a later send's.
    assert out["marking"] == {"keys": doc["keys"], "rows": doc["rows"]}


def test_the_answer_names_the_tuples_it_lit_by(workspace, capsys):
    keys, rows = ["wafer", "lot"], [["3", "C"], ["1", "A"]]
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "keys": keys, "rows": rows})
    assert code == 0, err
    # As given — keys in the caller's order, not sorted here.
    assert out is not None and out["marking"] == {"keys": keys, "rows": rows}
    assert out["rows"] == 2  # (A, 1) and (C, 3)


def test_the_views_transforms_apply_before_the_marking_lights(workspace, capsys):
    spec = CHART + 'transform:\n  - filter: "wafer > 1"\n'
    (workspace / "views" / "c.ai.yaml").write_text(spec)
    keys, rows = ["lot"], [["A"], ["C"]]
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "keys": keys, "rows": rows})
    assert code == 0, err
    assert out is not None and out["rows"] == 3
    frame = apply_transforms(read_source(workspace, "data/runs.csv"), [{"filter": "wafer > 1"}])
    _assert_parity(frame, keys, rows, out["csv"])


@pytest.mark.parametrize(
    ("keys", "rows"),
    [
        (["wafer"], [["1"]]),  # an integer column, as the browser prints it
        (["value"], [["1"], ["2.5"], ["7"]]),  # 1.0 and 7.0 print as "1" and "7"
        (["lot", "wafer"], [["A", "1"], ["C", "3"], ["C", "9"]]),  # the whole tuple matches
        (["lot", "not-a-column"], [["B", "x"], ["A", "y"]]),  # a key the view lacks projects away
        (["day"], [["2024-03-01"]]),  # a date column is compared as its text
    ],
)
def test_marking_text_is_canon_on_every_shared_key(workspace, capsys, keys, rows):
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "keys": keys, "rows": rows})
    assert code == 0, err
    assert out is not None and out["rows"] > 0
    _assert_parity(read_source(workspace, "data/runs.csv"), keys, rows, out["csv"])


def test_a_table_file_is_its_own_view(workspace, capsys):
    code, out, err = _run(capsys, {"view": "/data/runs.csv", "keys": ["lot"], "rows": [["D"]]})
    assert code == 0, err
    assert out is not None and out["rows"] == 1


def test_a_view_kind_with_a_source_reads_it_without_transforms(workspace, capsys):
    # csv-table's view file: `source:` is its own key, and `transform:` is a
    # chart's — another kind's key of that name is not applied.
    (workspace / "views" / "t.ai.yaml").write_text(
        'view: csv-table\nsource: data/runs.csv\ntransform:\n  - filter: "wafer > 99"\n'
    )
    code, out, err = _run(capsys, {"view": "views/t.ai.yaml", "keys": ["lot"], "rows": [["A"]]})
    assert code == 0, err
    assert out is not None and out["rows"] == 2


def test_an_entity_view_reads_its_records(workspace, capsys):
    (workspace / ".entity" / "issue").mkdir(parents=True)
    (workspace / ".entity" / "issue" / "schema.yaml").write_text(
        "path: issues\nfields:\n  title: { role: text }\n  points: { role: number }\n"
    )
    (workspace / "issues").mkdir()
    (workspace / "issues" / "1.md").write_text("---\ntitle: A\npoints: 3\n---\n")
    (workspace / "issues" / "2.md").write_text("---\ntitle: B\npoints: 5\n---\n")
    (workspace / "views" / "i.ai.yaml").write_text("view: table\nentity: issue\nkeys: [number]\n")
    keys, rows = ["number"], [["2"]]
    code, out, err = _run(capsys, {"view": "views/i.ai.yaml", "keys": keys, "rows": rows})
    assert code == 0, err
    assert out is not None and out["rows"] == 1
    _assert_parity(read_source(workspace, {"entity": "issue"}), keys, rows, out["csv"])


def test_no_column_in_common_is_refused(workspace, capsys):
    code, _, err = _run(capsys, {"view": "views/c.ai.yaml", "keys": ["tool"], "rows": [["x"]]})
    assert code == 2
    assert err.strip() == "the view has no column in common with the marking"


@pytest.mark.parametrize(
    ("keys", "rows"),
    [
        (["lot"], [["Z"]]),
        (["lot", "wafer"], [["A", "3"], ["C", "1"]]),  # each value is there, no tuple is
        (["lot"], []),
    ],
)
def test_nothing_lit_is_refused(workspace, capsys, keys, rows):
    code, _, err = _run(capsys, {"view": "views/c.ai.yaml", "keys": keys, "rows": rows})
    assert code == 2
    assert err.strip() == "no row of the view is lit by the marking"


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        ("{", "not JSON"),
        (json.dumps([]), "either"),
        (json.dumps({"view": "v"}), "either"),
        (json.dumps({"view": "v", "keys": ["a"]}), "either"),
        (json.dumps({"view": "v", "columns": {"a": ["x"]}}), "either"),  # #855's shape
        (json.dumps({"view": "v", "keys": [], "rows": [], "marking": "m"}), "either"),
        (json.dumps({"view": 1, "keys": [], "rows": []}), "either"),
        (json.dumps({"view": "v", "marking": 3}), "path of a marking file"),
        (json.dumps({"view": "v", "keys": "a", "rows": []}), "one text value per key"),
        (json.dumps({"view": "v", "keys": [1], "rows": []}), "one text value per key"),
        (json.dumps({"view": "v", "keys": ["a", "a"], "rows": []}), "one text value per key"),
        (json.dumps({"view": "v", "keys": ["a"], "rows": {}}), "one text value per key"),
        (json.dumps({"view": "v", "keys": ["a"], "rows": ["x"]}), "one text value per key"),
        (json.dumps({"view": "v", "keys": ["a"], "rows": [[1]]}), "one text value per key"),
        (json.dumps({"view": "v", "keys": ["a"], "rows": [["x", "y"]]}), "one text value per key"),
    ],
)
def test_a_wrong_call_is_refused(workspace, capsys, raw, said):
    assert main(["lit_rows", raw]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert said in captured.err


@pytest.mark.parametrize(
    ("files", "args", "said"),
    [
        ({}, {"view": "views/gone.ai.yaml"}, "views/gone.ai.yaml is not a readable view file"),
        ({"views/l.ai.yaml": "- a\n- b\n"}, {"view": "views/l.ai.yaml"}, "is not a view file"),
        ({"views/n.ai.yaml": "view: table\n"}, {"view": "views/n.ai.yaml"}, "names no source"),
        (
            {"views/s.ai.yaml": "view: csv-table\nsource: '  '\n"},
            {"view": "views/s.ai.yaml"},
            "names no source",
        ),
        (
            {"views/b.ai.yaml": CHART.replace("mark: scatter", "mark: nope")},
            {"view": "views/b.ai.yaml"},
            "is not a valid chart",
        ),
        (
            {"views/m.ai.yaml": CHART.replace("data/runs.csv", "data/missing.csv")},
            {"view": "views/m.ai.yaml"},
            "'data/missing.csv' is not a file",
        ),
        (
            {"views/f.ai.yaml": CHART + 'transform:\n  - filter: "nope > 1"\n'},
            {"view": "views/f.ai.yaml"},
            "nope",
        ),
        ({"views/y.ai.yaml": "a: [\n"}, {"view": "views/y.ai.yaml"}, "not valid YAML"),
        ({}, {"view": "../outside.csv"}, "outside the workspace"),
    ],
)
def test_a_view_with_nothing_to_read_is_refused(workspace, capsys, files, args, said):
    for name, text in files.items():
        (workspace / name).write_text(text)
    code, _, err = _run(capsys, {**args, "keys": ["lot"], "rows": [["A"]]})
    assert code == 2
    assert said in err and err.strip()


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (None, "the marking is no longer in the workspace"),
        ("{", "the marking is no longer in the workspace"),
        (json.dumps([1]), NOT_A_MARKING),
        (json.dumps({"keys": ["lot"], "rows": [[1]]}), NOT_A_MARKING),
        (json.dumps({"keys": ["lot"]}), NOT_A_MARKING),
        # #855's `column -> values` file: no reader accepts the old shape (#861).
        (json.dumps({"columns": {"lot": ["A"]}}), NOT_A_MARKING),
    ],
)
def test_a_marking_file_that_is_gone_or_wrong_is_refused(workspace, capsys, text, said):
    if text is not None:
        (workspace / "m.json").write_text(text)
    code, _, err = _run(capsys, {"view": "views/c.ai.yaml", "marking": "m.json"})
    assert code == 2
    assert err.strip() == said


def test_lit_rows_describes_its_arguments(capsys):
    assert main(["lit_rows"]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["name"] == "lit_rows" and meta["description"]
    assert set(meta["params_json_schema"]["properties"]) == {"view", "keys", "rows", "marking"}
