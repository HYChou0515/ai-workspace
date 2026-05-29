"""data-fetch CLI tests — exercises both the synthesizer (pure data
plumbing) and the 3-stage contract dispatcher (stages 1/2/3)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from data_fetch.cli import _CATALOG, FetchArgs, main, synthesize


# ─── synthesizer ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(_CATALOG))
def test_every_dataset_is_large_and_wide_and_mixed(name: str):
    df = synthesize(name, rows=200, seed=1)
    assert df.shape[0] == 200
    assert df.shape[1] >= 20  # the "20+ columns" guarantee
    assert {"record_id", "line", "shift", "operator", "timestamp", "label"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert df.select_dtypes("number").shape[1] >= 18


def test_deterministic_for_same_seed():
    a = synthesize("alloy-batches", rows=100, seed=7)
    b = synthesize("alloy-batches", rows=100, seed=7)
    assert a.equals(b)


def test_unknown_name_raises():
    with pytest.raises(KeyError):
        synthesize("nope", rows=10)


# ─── 3-stage contract: stage 1 (list commands) ───────────────────────


def test_stage1_lists_data_fetch_command(capsys):
    """Bare invocation → JSON list containing this one command."""
    assert main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == [{"name": "data-fetch", "description": _description()}]


# ─── 3-stage contract: stage 2 (schema dump) ─────────────────────────


def test_stage2_prints_schema(capsys):
    """`data-fetch` alone → JSON object with the pydantic schema."""
    assert main(["data-fetch"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "data-fetch"
    assert out["description"] == _description()
    schema = out["params_json_schema"]
    # The enum constraint reaches the LLM — it can't invent a bad name.
    name_def = schema["properties"]["name"]
    assert "enum" in name_def
    assert set(name_def["enum"]) == set(_CATALOG)


def test_stage2_unknown_command_exits_2(capsys):
    assert main(["nope"]) == 2
    assert "unknown command" in capsys.readouterr().err


# ─── 3-stage contract: stage 3 (execute) ─────────────────────────────


def test_stage3_writes_csv_and_prints_summary(tmp_path: Path, capsys, monkeypatch):
    """JSON args → pydantic validate → run → wrote-summary on stdout."""
    out = tmp_path / "ds.csv"
    monkeypatch.chdir(tmp_path)
    args = json.dumps({"name": "sensor-telemetry", "rows": 150, "out": str(out)})
    assert main(["data-fetch", args]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["rows"] == 150
    assert meta["columns"] >= 20
    back = pd.read_csv(out)
    assert back.shape == (150, meta["columns"])


def test_stage3_default_out_path_uses_dataset_name(tmp_path: Path, monkeypatch):
    """`out` omitted → writes `<name>.csv` to cwd. Tests the FetchArgs
    default path."""
    monkeypatch.chdir(tmp_path)
    args = json.dumps({"name": "alloy-batches", "rows": 80})
    assert main(["data-fetch", args]) == 0
    assert (tmp_path / "alloy-batches.csv").is_file()


def test_stage3_invalid_args_exits_2_with_pydantic_message(capsys):
    """Bad enum → pydantic friendly error to stderr + exit 2 (matches
    the dispatcher contract from §B.10)."""
    args = json.dumps({"name": "not-a-real-dataset"})
    assert main(["data-fetch", args]) == 2
    err = capsys.readouterr().err
    assert "name" in err
    # Either pydantic 'Input should be' phrasing or the enum list — both prove
    # the schema kicked in and the LLM gets a clear correction signal.
    assert "Input should be" in err or "literal_error" in err


def test_stage3_negative_rows_rejected_by_pydantic_ge_constraint(capsys):
    args = json.dumps({"name": "alloy-batches", "rows": 0})
    assert main(["data-fetch", args]) == 2
    assert "rows" in capsys.readouterr().err


# ─── FetchArgs model ─────────────────────────────────────────────────


def test_fetch_args_defaults_match_old_behaviour():
    """Spot-check the pydantic defaults so a casual edit doesn't silently
    change the agent-visible defaults (rows=25000, seed=0)."""
    args = FetchArgs(name="alloy-batches")
    assert args.rows == 25_000
    assert args.seed == 0
    assert args.out is None


# ─── helpers ─────────────────────────────────────────────────────────


def _description() -> str:
    from data_fetch.cli import DESCRIPTION

    return DESCRIPTION
