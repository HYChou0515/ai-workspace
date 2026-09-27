"""`lit_rows` — the rows a marking lights, as CSV (plan-view-plugins-pr5-finish P7).

"Save as table" on a marking's header control or its chat chip runs this over
the view the action was taken in: its `source:` (a table file or entity
records) with its `transform:` applied, then lit by the platform's one rule
(the SPA's `isLit`): a row shares at least one column with the marking, and on
every shared column `canon(value)` is in that column's set.

The oracle is pandas' own filter of the same frame by that rule, and every lit
cell's `canon` is checked to be in the marking.
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
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "c.ai.yaml").write_text(CHART)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(capsys, args: dict) -> tuple[int, dict | None, str]:
    code = main(["lit_rows", json.dumps(args)])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else None), captured.err


def _oracle(frame: pd.DataFrame, columns: dict[str, list[str]]) -> pd.DataFrame:
    """pandas' filter of `frame` by the SPA's `isLit`, written out plainly."""
    shared = [c for c in columns if c in frame.columns]
    mask = pd.Series(bool(shared), index=frame.index)
    for c in shared:
        mask &= frame[c].map(canon).isin(set(columns[c]))
    return frame[mask].reset_index(drop=True)


def _read(csv: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv))


def _assert_parity(frame: pd.DataFrame, columns: dict[str, list[str]], csv: str) -> None:
    got = _read(csv)
    want = _read(_oracle(frame, columns).to_csv(index=False))
    pd.testing.assert_frame_equal(got, want)
    for c in columns:
        if c in frame.columns:
            lit = _oracle(frame, columns)[c]
            assert {canon(v) for v in lit} <= set(columns[c])


def test_every_column_of_the_rows_a_marking_lights(workspace, capsys):
    columns = {"lot": ["A", "C"]}
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "columns": columns})
    assert code == 0, err
    assert out is not None
    assert out["rows"] == 4
    assert list(_read(out["csv"]).columns) == ["lot", "wafer", "value", "day"]
    _assert_parity(read_source(workspace, "data/runs.csv"), columns, out["csv"])


def test_a_sent_marking_is_read_from_its_file(workspace, capsys):
    # The chat chip has no view at hand: the marking comes from the file the
    # send wrote, `.markings/<name>.json` (api/markings.py's shape).
    (workspace / ".markings").mkdir()
    doc = {"name": "fail", "sources": ["views/c.ai.yaml"], "columns": {"lot": ["B", "D"]}}
    (workspace / ".markings" / "fail.json").write_text(json.dumps(doc))
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "marking": "/.markings/fail.json"})
    assert code == 0, err
    assert out is not None and out["rows"] == 2
    _assert_parity(read_source(workspace, "data/runs.csv"), doc["columns"], out["csv"])
    # The answer says which marking it lit by — exactly the file's — so the
    # platform can check it is the one the chip sent, not a later send's.
    assert out["columns"] == doc["columns"]


def test_the_answer_names_the_values_it_lit_by(workspace, capsys):
    columns = {"lot": ["C", "A"], "wafer": ["1", "3"]}
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "columns": columns})
    assert code == 0, err
    assert out is not None and out["columns"] == columns


def test_the_views_transforms_apply_before_the_marking_lights(workspace, capsys):
    spec = CHART + 'transform:\n  - filter: "wafer > 1"\n'
    (workspace / "views" / "c.ai.yaml").write_text(spec)
    columns = {"lot": ["A", "C"]}
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "columns": columns})
    assert code == 0, err
    assert out is not None and out["rows"] == 3
    frame = apply_transforms(read_source(workspace, "data/runs.csv"), [{"filter": "wafer > 1"}])
    _assert_parity(frame, columns, out["csv"])


@pytest.mark.parametrize(
    "columns",
    [
        {"wafer": ["1"]},  # an integer column, as the browser prints it
        {"value": ["1", "2.5", "7"]},  # 1.0 and 7.0 print as "1" and "7"
        {"lot": ["A", "C"], "wafer": ["1", "3"]},  # every shared column must match
        {"lot": ["B"], "not-a-column": ["x"]},  # a column the view lacks is not shared
        {"day": ["2024-03-01"]},  # a date column is compared as its text
    ],
)
def test_marking_text_is_canon_on_every_shared_column(workspace, capsys, columns):
    code, out, err = _run(capsys, {"view": "views/c.ai.yaml", "columns": columns})
    assert code == 0, err
    assert out is not None and out["rows"] > 0
    _assert_parity(read_source(workspace, "data/runs.csv"), columns, out["csv"])


def test_a_table_file_is_its_own_view(workspace, capsys):
    code, out, err = _run(capsys, {"view": "/data/runs.csv", "columns": {"lot": ["D"]}})
    assert code == 0, err
    assert out is not None and out["rows"] == 1


def test_a_view_kind_with_a_source_reads_it_without_transforms(workspace, capsys):
    # csv-table's view file: `source:` is its own key, and `transform:` is a
    # chart's — another kind's key of that name is not applied.
    (workspace / "views" / "t.ai.yaml").write_text(
        'view: csv-table\nsource: data/runs.csv\ntransform:\n  - filter: "wafer > 99"\n'
    )
    code, out, err = _run(capsys, {"view": "views/t.ai.yaml", "columns": {"lot": ["A"]}})
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
    columns = {"number": ["2"]}
    code, out, err = _run(capsys, {"view": "views/i.ai.yaml", "columns": columns})
    assert code == 0, err
    assert out is not None and out["rows"] == 1
    _assert_parity(read_source(workspace, {"entity": "issue"}), columns, out["csv"])


def test_no_column_in_common_is_refused(workspace, capsys):
    code, _, err = _run(capsys, {"view": "views/c.ai.yaml", "columns": {"tool": ["x"]}})
    assert code == 2
    assert err.strip() == "the view has no column in common with the marking"


def test_nothing_lit_is_refused(workspace, capsys):
    code, _, err = _run(capsys, {"view": "views/c.ai.yaml", "columns": {"lot": ["Z"]}})
    assert code == 2
    assert err.strip() == "no row of the view is lit by the marking"


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        ("{", "not JSON"),
        (json.dumps([]), "exactly one of"),
        (json.dumps({"view": "v"}), "exactly one of"),
        (json.dumps({"view": "v", "columns": {}, "marking": "m"}), "exactly one of"),
        (json.dumps({"view": 1, "columns": {}}), "exactly one of"),
        (json.dumps({"view": "v", "marking": 3}), "path of a marking file"),
        (json.dumps({"view": "v", "columns": {"a": "x"}}), "list of text values"),
        (json.dumps({"view": "v", "columns": {"a": [1]}}), "list of text values"),
        (json.dumps({"view": "v", "columns": []}), "list of text values"),
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
    code, _, err = _run(capsys, {**args, "columns": {"lot": ["A"]}})
    assert code == 2
    assert said in err and err.strip()


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (None, "the marking is no longer in the workspace"),
        ("{", "the marking is no longer in the workspace"),
        (json.dumps([1]), "the marking's file does not hold a marking"),
        (json.dumps({"columns": {"lot": [1]}}), "the marking's file does not hold a marking"),
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
    assert set(meta["params_json_schema"]["properties"]) == {"view", "columns", "marking"}
