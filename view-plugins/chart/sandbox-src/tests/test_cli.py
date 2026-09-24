"""The bundle's command contract, as `launch` runs it (cwd = the workspace).

    launch                      → the command list
    launch <cmd>                → its metadata + JSON schema
    launch <cmd> '<json args>'  → run it

`validate {"path"}` is what #854's show_file hook runs: exit 0 with ONE summary
line on stdout, or non-zero with the refusal lines on stderr. `query {"spec"}`
is what the renderer runs through `useSandboxRun`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chart_view.cli import main

SPEC = """\
view: chart
source: data/a.csv
mark: scatter
encoding:
  x: {field: a, type: quantitative}
  y: {field: b, type: quantitative}
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.csv").write_text("a,b\n1,2\n3,5\n")
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "c.ai.yaml").write_text(SPEC)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bare_launch_lists_the_commands(capsys):
    assert main([]) == 0
    names = [c["name"] for c in json.loads(capsys.readouterr().out)]
    # the facet pager's commands (#857) follow; tests/facet/test_facet_cli.py pins them
    assert names == [
        "validate",
        "query",
        "facet_build",
        "facet_index",
        "facet_page",
        "facet_exact",
    ]


@pytest.mark.parametrize("cmd", ["validate", "query"])
def test_a_command_alone_describes_its_arguments(capsys, cmd):
    assert main([cmd]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["name"] == cmd and meta["description"]
    assert meta["params_json_schema"]["type"] == "object"


def test_an_unknown_command_is_refused(capsys):
    assert main(["draw"]) == 2
    assert "validate, query" in capsys.readouterr().err


def test_validate_prints_one_summary_line(workspace, capsys):
    assert main(["validate", json.dumps({"path": "views/c.ai.yaml"})]) == 0
    out = capsys.readouterr().out
    assert out == "2 rows; b 2–5\n"


def test_validate_refuses_on_stderr(workspace, capsys):
    (workspace / "views" / "c.ai.yaml").write_text(SPEC.replace("field: b", "field: z"))
    assert main(["validate", json.dumps({"path": "views/c.ai.yaml"})]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "'z'" in captured.err


def test_validate_names_a_view_file_that_is_not_there(workspace, capsys):
    assert main(["validate", json.dumps({"path": "views/nope.ai.yaml"})]) == 2
    assert "views/nope.ai.yaml" in capsys.readouterr().err


def test_query_answers_in_json(workspace, capsys):
    assert main(["query", json.dumps({"spec": SPEC})]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["format"] == 1 and answer["layers"][0]["rows"] == 2


def test_query_refuses_a_spec_the_schema_refuses(workspace, capsys):
    assert main(["query", json.dumps({"spec": SPEC.replace("scatter", "tick")})]) == 2
    assert "mark" in capsys.readouterr().err


def test_query_refuses_yaml_that_does_not_parse(workspace, capsys):
    assert main(["query", json.dumps({"spec": "view: [chart"})]) == 2
    assert "not valid YAML" in capsys.readouterr().err


def test_query_refuses_a_source_it_cannot_read(workspace, capsys):
    assert main(["query", json.dumps({"spec": SPEC.replace("data/a.csv", "x.csv")})]) == 2
    assert "x.csv" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("cmd", "args"),
    [("validate", "{}"), ("validate", '{"path": 3}'), ("query", "[]"), ("query", "not json")],
)
def test_bad_arguments_are_refused_by_name(capsys, cmd, args):
    assert main([cmd, args]) == 2
    assert "argument" in capsys.readouterr().err
