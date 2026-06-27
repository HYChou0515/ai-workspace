"""Tests for the 3-stage CLI dispatcher."""

from __future__ import annotations

import json
from pathlib import Path

from sci_plot.cli import main


def test_stage1_lists_chart_command(capsys):
    assert main([]) == 0
    cmds = json.loads(capsys.readouterr().out)
    assert [c["name"] for c in cmds] == ["chart"]


def test_stage2_chart_schema(capsys):
    assert main(["chart"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "chart"
    assert "$defs" in out["params_json_schema"]
    assert "box_scatter" in json.dumps(out["params_json_schema"])


def test_stage2_unknown_command_exits_2(capsys):
    assert main(["nope"]) == 2
    assert "chart" in capsys.readouterr().err


def test_stage3_renders_and_reports_image(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = json.dumps(
        {
            "chart": "box_scatter",
            "data": {"g": ["a", "a", "b"], "y": [1, 2, 3]},
            "group": "g",
            "y": "y",
        }
    )
    assert main(["chart", args]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["images"] and Path(out["images"][0]).exists()


def test_stage3_needs_input_is_exit_0(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = json.dumps({"chart": "box_scatter", "data": {"n1": [1, 2], "n2": [3, 4]}})
    assert main(["chart", args]) == 0  # guidance, not a crash
    out = json.loads(capsys.readouterr().out)
    assert "needs_input" in out


def test_stage3_invalid_args_exit_2(capsys):
    assert main(["chart", '{"chart": "nonexistent_chart"}']) == 2
    assert "chart" in capsys.readouterr().err


def test_stage3_missing_file_is_usage_error(capsys):
    args = json.dumps({"chart": "box_scatter", "data": "/no/file.csv", "group": "g", "y": "y"})
    assert main(["chart", args]) == 2
    assert "file not found" in capsys.readouterr().err
