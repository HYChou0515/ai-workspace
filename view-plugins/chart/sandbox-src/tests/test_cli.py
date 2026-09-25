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


# #847/#848 P9: the renderer names the view FILE, not its text. Every argument
# travels as ONE argv string, which the kernel caps at 128 KiB (MAX_ARG_STRLEN),
# so a spec over that passed show_file (whose hook sends only the path) and then
# got a 413 on every render. A path is the same size whatever the spec is.
ARGV_CAP = 128 * 1024


def _big(workspace: Path) -> str:
    """A view file whose text is over the argv cap: a long, legitimate filter."""
    keep = ", ".join(str(i) for i in range(40_000))
    text = SPEC + f"transform:\n  - filter: {{field: a, oneOf: [{keep}]}}\n"
    assert len(text.encode()) > ARGV_CAP
    (workspace / "views" / "big.ai.yaml").write_text(text)
    return "/views/big.ai.yaml"


def test_query_reads_the_view_file_it_is_named(workspace, capsys):
    path = _big(workspace)
    args = json.dumps({"path": path, "rev": "0" * 11})
    assert len(args.encode()) < 200  # the size of the call is not the size of the spec
    assert main(["query", args]) == 0
    answer = json.loads(capsys.readouterr().out)
    # the filter keeps a = 1 and a = 3: the answer is the FILE's spec, filter and all
    assert answer["layers"][0]["rows"] == 2


def test_query_by_path_answers_what_query_by_text_answers(workspace, capsys):
    # the oracle is the text form: one file, two ways of naming it, one answer
    path = _big(workspace)
    (workspace / "data" / "a.csv").write_text("a,b\n1,2\n3,5\n-1,9\n")  # -1 is filtered out
    assert main(["query", json.dumps({"spec": (workspace / path[1:]).read_text()})]) == 0
    by_text = capsys.readouterr().out
    assert main(["query", json.dumps({"path": path})]) == 0
    assert capsys.readouterr().out == by_text
    assert json.loads(by_text)["layers"][0]["rows"] == 2


def test_query_names_a_view_file_that_is_not_there(workspace, capsys):
    assert main(["query", json.dumps({"path": "views/nope.ai.yaml"})]) == 2
    assert "views/nope.ai.yaml" in capsys.readouterr().err


def test_query_refuses_a_view_file_outside_the_workspace(workspace, capsys):
    assert main(["query", json.dumps({"path": "../etc/passwd"})]) == 2
    assert "outside the workspace" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"spec": "x", "path": "views/c.ai.yaml"},  # which one?
        {"path": 3},
        {"path": "views/c.ai.yaml", "rev": 3},
        {"path": "views/c.ai.yaml", "extra": "x"},
    ],
)
def test_query_takes_a_spec_or_a_path_not_both_or_neither(workspace, capsys, args):
    assert main(["query", json.dumps(args)]) == 2
    assert "argument" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("cmd", "extra"), [("query", {}), ("facet_build", {"epoch": 1})], ids=["query", "facet_build"]
)
def test_the_described_arguments_are_the_accepted_ones(capsys, cmd, extra):
    """What `launch <cmd>` describes is what `launch <cmd> <args>` accepts: the
    oracle for "accepted" is the command itself (anything but an argument error)."""
    from jsonschema import Draft202012Validator

    assert main([cmd]) == 0
    schema = Draft202012Validator(json.loads(capsys.readouterr().out)["params_json_schema"])
    calls = [
        {"path": "views/none.ai.yaml", "rev": "r"},
        {"path": "views/none.ai.yaml"},
        {"spec": "view: [chart"},
        {"spec": "x", "path": "y"},
        {"rev": "r"},
        {"spec": "x", "rev": "r"},
        {},
    ]
    for call in calls:
        main([cmd, json.dumps({**call, **extra})])
        err = capsys.readouterr().err
        refused_as_a_call = "takes exactly" in err or "argument must be" in err
        assert schema.is_valid({**call, **extra}) is not refused_as_a_call, (call, err)
