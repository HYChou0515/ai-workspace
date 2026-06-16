"""`plot` command: write per-column distribution + correlation PNGs."""

from __future__ import annotations

import json

import pandas as pd
from pydantic import BaseModel, Field

from csv_column_summary.core import plot as render_plots
from csv_column_summary.core import progress


class Args(BaseModel):
    """Same shape as summarise.Args — the agent picks which command to
    run based on its description; both take just a `csv` path."""

    csv: str = Field(description="Path to the CSV file in the workspace.")


DESCRIPTION = (
    "Write per-column distribution + numeric correlation PNGs next to the CSV. "
    "Output is a JSON object with a `plots` key listing the written paths."
)


def run(args: Args) -> None:
    """Read the CSV, plot, return the written paths as JSON."""
    progress(f"reading {args.csv} …")
    df = _read(args.csv)
    progress(f"plotting {len(df.columns)} columns over {len(df)} rows …")
    written = render_plots(df, args.csv)
    for p in written:
        progress(f"  wrote {p}")
    print(json.dumps({"plots": written}, indent=2))


def _read(csv: str) -> pd.DataFrame:
    """Shared error rewrap with summarise (kept local rather than a
    cross-command import — the two commands are independent)."""
    try:
        return pd.read_csv(csv)
    except FileNotFoundError as e:
        raise ValueError(f"file not found: {csv}") from e
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not read CSV: {e}") from e
