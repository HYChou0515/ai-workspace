"""Reading a chart's `source:` — a table file in the workspace.

The command runs with the workspace as its root. A view file names a source the
way the file tree shows it, so `/data/a.csv` and `data/a.csv` are the same file.
(`source: {entity: …}` goes through the platform's reader, not this module.)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class SourceError(ValueError):
    """The source cannot be read."""


def read_table(root: Path, source: str) -> pd.DataFrame:
    """The table at `source` (.csv / .tsv / .parquet) under `root`."""
    path = root / source.lstrip("/")
    if not path.is_file():
        raise SourceError(f"source {source!r} is not a file in the workspace")
    try:
        if path.suffix == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path, sep="\t" if path.suffix == ".tsv" else ",")
    except Exception as e:  # pandas / pyarrow raise many types for a bad file
        raise SourceError(f"source {source!r} could not be read: {e}") from e
