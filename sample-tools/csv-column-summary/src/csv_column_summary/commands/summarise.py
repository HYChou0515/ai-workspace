"""`summarise` command: per-column dtype/count/nulls/stats summary."""

from __future__ import annotations

import json
from dataclasses import asdict

import pandas as pd
from pydantic import BaseModel, Field

from csv_column_summary.core import progress, summarize


class Args(BaseModel):
    """Pydantic Args model — drives both the LLM-facing JSON schema and
    runtime validation. Single source of truth, no argparse parallel."""

    csv: str = Field(description="Path to the CSV file in the workspace.")


DESCRIPTION = (
    "Summarise each column of a CSV: dtype, count, nulls, uniques, numeric "
    "stats, top categorical values. Output is a JSON object with `rows` and "
    "`columns` keys."
)


def run(args: Args) -> None:
    """Execute with validated args. Stdout = JSON payload; stderr =
    progress lines (sandbox relays them live to the chat)."""
    progress(f"reading {args.csv} …")
    df = _read(args.csv)
    progress(f"summarising {len(df.columns)} columns over {len(df)} rows …")
    cols = summarize(df)
    print(
        json.dumps(
            {"rows": len(df), "columns": [asdict(c) for c in cols]},
            indent=2,
        )
    )


def _read(csv: str) -> pd.DataFrame:
    """Read with the friendly errors the agent recovers from (file not
    found / unparseable CSV) — pd.errors get repackaged as ``ValueError``
    so the dispatcher's exit-2 path covers them."""
    try:
        return pd.read_csv(csv)
    except FileNotFoundError as e:
        raise ValueError(f"file not found: {csv}") from e
    except Exception as e:  # noqa: BLE001 — any parse failure is a usage error
        raise ValueError(f"could not read CSV: {e}") from e
