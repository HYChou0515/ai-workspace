"""csv-column-summary CLI tests — covers the core data plumbing
(``summarize`` / ``plot``) plus the 3-stage contract dispatcher for
both registered commands (`summarise` + `plot`)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from csv_column_summary.cli import main
from csv_column_summary.commands import summarise as summarise_mod
from csv_column_summary.core import plot, summarize


# ─── core ─────────────────────────────────────────────────────────────


def test_summarize_numeric_and_categorical():
    df = pd.DataFrame({"n": [1, 2, 3, None], "cat": ["a", "a", "b", "b"]})
    by_col = {c.column: c for c in summarize(df)}

    n = by_col["n"]
    assert n.count == 3 and n.nulls == 1 and n.unique == 3
    assert n.min == 1.0 and n.max == 3.0 and n.mean == 2.0
    assert n.top_values is None  # numeric → stats, not top values

    cat = by_col["cat"]
    assert cat.min is None  # categorical → no numeric stats
    assert {t["value"]: t["count"] for t in cat.top_values} == {"a": 2, "b": 2}


def test_plot_writes_distribution_and_correlation_pngs(tmp_path: Path):
    csv = tmp_path / "d.csv"
    csv.write_text("x,y,label\n1,2,a\n2,4,b\n3,6,a\n4,8,b\n")
    df = pd.read_csv(csv)
    written = plot(df, str(csv))
    dist = tmp_path / "d.distributions.png"
    corr = tmp_path / "d.correlations.png"
    assert dist.exists() and dist.stat().st_size > 0  # always a distributions grid
    assert corr.exists()  # 2 numeric columns → a correlation heatmap
    assert {str(dist), str(corr)} == set(written)


def test_plot_skips_heatmap_with_fewer_than_two_numeric_columns(tmp_path: Path):
    csv = tmp_path / "one.csv"
    csv.write_text("x,label\n1,a\n2,b\n")
    written = plot(pd.read_csv(csv), str(csv))
    assert written == [str(tmp_path / "one.distributions.png")]  # no heatmap


# ─── 3-stage dispatcher: stage 1 (list commands) ─────────────────────


def test_stage1_lists_both_commands(capsys):
    assert main([]) == 0
    cmds = json.loads(capsys.readouterr().out)
    by_name = {c["name"]: c for c in cmds}
    assert set(by_name) == {"summarise", "plot"}
    assert "Summarise" in by_name["summarise"]["description"]
    assert "PNG" in by_name["plot"]["description"]


# ─── 3-stage dispatcher: stage 2 (schema dump) ───────────────────────


def test_stage2_summarise_schema(capsys):
    assert main(["summarise"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "summarise"
    assert "csv" in out["params_json_schema"]["properties"]


def test_stage2_plot_schema(capsys):
    assert main(["plot"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "plot"
    assert "csv" in out["params_json_schema"]["properties"]


def test_stage2_unknown_command_exits_2_with_available_list(capsys):
    assert main(["nope"]) == 2
    err = capsys.readouterr().err
    assert "summarise" in err and "plot" in err  # available list reaches stderr


# ─── 3-stage dispatcher: stage 3 (execute) ───────────────────────────


def test_stage3_summarise_writes_json_summary(tmp_path: Path, capsys):
    csv = tmp_path / "d.csv"
    csv.write_text("x,label\n1,foo\n2,bar\n2,bar\n")
    args = json.dumps({"csv": str(csv)})
    assert main(["summarise", args]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 3
    cols = {c["column"]: c for c in out["columns"]}
    assert cols["x"]["mean"] == 5 / 3
    assert cols["label"]["unique"] == 2


def test_stage3_plot_writes_pngs_and_reports_paths(tmp_path: Path, capsys):
    csv = tmp_path / "d.csv"
    csv.write_text("x,y\n1,2\n2,4\n3,6\n")
    args = json.dumps({"csv": str(csv)})
    assert main(["plot", args]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(p.endswith(".distributions.png") for p in out["plots"])
    assert (tmp_path / "d.distributions.png").exists()


def test_stage3_invalid_args_exit_2(capsys):
    """Missing required `csv` → pydantic friendly error + exit 2."""
    assert main(["summarise", "{}"]) == 2
    err = capsys.readouterr().err
    assert "csv" in err


def test_stage3_missing_file_is_usage_error(capsys):
    args = json.dumps({"csv": "/no/such/file.csv"})
    assert main(["summarise", args]) == 2
    assert "file not found" in capsys.readouterr().err


# ─── command modules expose Args + run + DESCRIPTION ──────────────────


def test_command_modules_expose_contract_surface():
    """Each command module must expose `Args` (pydantic) + `DESCRIPTION`
    (str) + `run(args)` — that's what the dispatcher's COMMANDS dict
    relies on. Lock this in so a refactor can't silently break it."""
    from csv_column_summary.commands import COMMANDS

    for name, mod in COMMANDS.items():
        assert hasattr(mod, "Args"), name
        assert hasattr(mod, "DESCRIPTION"), name
        assert hasattr(mod, "run"), name


def test_summarise_args_required_csv():
    """`csv` has no default → required. Locks in the pydantic shape."""
    schema = summarise_mod.Args.model_json_schema()
    assert "csv" in schema["required"]
