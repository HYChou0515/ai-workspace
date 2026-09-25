"""Reading a chart's `source:` — a table file in the workspace, or entity records.

The command runs with the workspace as its root. A view file names a source the
way the file tree shows it, so `/data/a.csv` and `data/a.csv` are the same file.
`{entity: …}` goes through the platform's own reader, vendored by aiws-view-sdk.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from workspace_app.entity.local import read_entity_records


class SourceError(ValueError):
    """The source cannot be read."""


def read_source(root: Path, source: str | dict[str, str]) -> pd.DataFrame:
    """A spec's `source:` as a frame: a table file, or `{entity: <type>}`'s
    records as the entity views project them (`number` plus each field)."""
    if isinstance(source, str):
        return read_table(root, source)
    try:
        records = read_entity_records(root, source["entity"])
    except LookupError as e:
        raise SourceError(str(e)) from e
    return pd.DataFrame.from_records(records)


def inside_workspace(root: Path, name: str) -> Path:
    """`name` as a path under `root`, refused when it resolves outside it.

    The file is named by a view file anyone who can read the item can open,
    and on a shared-dir backend a sibling item's workspace is one `../` away."""
    try:
        path = (root / name.lstrip("/")).resolve()
    # A NUL byte is ValueError; a loop of links is RuntimeError on 3.12 (the
    # interpreter the bundle carries) and OSError from 3.13.
    except (ValueError, OSError, RuntimeError) as e:
        raise SourceError(f"{name!r} is not a usable path ({e})") from e
    if not path.is_relative_to(root.resolve()):
        raise SourceError(f"{name!r} is outside the workspace")
    return path


def read_table(root: Path, source: str) -> pd.DataFrame:
    """The table at `source` (.csv / .tsv / .parquet) under `root`."""
    path = inside_workspace(root, source)
    if not path.is_file():
        raise SourceError(f"source {source!r} is not a file in the workspace")
    try:
        if path.suffix == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path, sep="\t" if path.suffix == ".tsv" else ",")
    except Exception as e:  # pandas / pyarrow raise many types for a bad file
        raise SourceError(f"source {source!r} could not be read: {e}") from e
