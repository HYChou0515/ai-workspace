import json
from pathlib import Path

import pandas as pd

from csv_column_summary.cli import main, summarize


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
