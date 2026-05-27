import json
from pathlib import Path

import pandas as pd

from csv_column_summary.cli import main, plot, summarize


def test_summarize_numeric_and_categorical():
    df = pd.DataFrame(
        {"n": [1, 2, 3, None], "cat": ["a", "a", "b", "b"]}
    )
    by_col = {c.column: c for c in summarize(df)}

    n = by_col["n"]
    assert n.count == 3 and n.nulls == 1 and n.unique == 3
    assert n.min == 1.0 and n.max == 3.0 and n.mean == 2.0
    assert n.top_values is None  # numeric → stats, not top values

    cat = by_col["cat"]
    assert cat.min is None  # categorical → no numeric stats
    assert {t["value"]: t["count"] for t in cat.top_values} == {"a": 2, "b": 2}


def test_cli_json_output(tmp_path: Path, capsys):
    csv = tmp_path / "d.csv"
    csv.write_text("x,label\n1,foo\n2,bar\n2,bar\n")
    rc = main([str(csv), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 3
    cols = {c["column"]: c for c in out["columns"]}
    assert cols["x"]["mean"] == 5 / 3
    assert cols["label"]["unique"] == 2


def test_cli_missing_file_is_usage_error(capsys):
    assert main(["/no/such.csv"]) == 2
    assert "not found" in capsys.readouterr().err


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


def test_cli_plot_lists_png_paths_in_output(tmp_path: Path, capsys):
    csv = tmp_path / "d.csv"
    csv.write_text("x,y\n1,2\n2,4\n3,6\n")
    rc = main([str(csv), "--plot"])
    assert rc == 0
    assert "distributions.png" in capsys.readouterr().out  # paths reach the agent
