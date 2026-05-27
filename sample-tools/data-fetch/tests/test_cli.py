import json
from pathlib import Path

import pandas as pd
import pytest

from data_fetch.cli import _CATALOG, main, synthesize


@pytest.mark.parametrize("name", sorted(_CATALOG))
def test_every_dataset_is_large_and_wide_and_mixed(name: str):
    # Small row count for test speed; shape/columns rule is what matters.
    df = synthesize(name, rows=200, seed=1)
    assert df.shape[0] == 200
    assert df.shape[1] >= 20  # the "20+ columns" guarantee
    # mixed dtypes: id + categorical + datetime + numeric
    assert {"record_id", "line", "shift", "operator", "timestamp", "label"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert df.select_dtypes("number").shape[1] >= 18  # plenty of numeric cols


def test_deterministic_for_same_seed():
    a = synthesize("alloy-batches", rows=100, seed=7)
    b = synthesize("alloy-batches", rows=100, seed=7)
    assert a.equals(b)


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        synthesize("nope", rows=10)


def test_main_writes_csv(tmp_path: Path, capsys):
    out = tmp_path / "ds.csv"
    rc = main(["sensor-telemetry", "--rows", "150", "--out", str(out), "--json"])
    assert rc == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["rows"] == 150 and meta["columns"] >= 20
    # the CSV round-trips at the reported shape
    back = pd.read_csv(out)
    assert back.shape == (150, meta["columns"])


def test_main_unknown_is_usage_error(capsys):
    assert main(["definitely-not-a-dataset"]) == 2
    assert "unknown dataset" in capsys.readouterr().err


def test_main_list(capsys):
    assert main(["--list", "--json"]) == 0
    assert sorted(json.loads(capsys.readouterr().out)["datasets"]) == sorted(_CATALOG)
