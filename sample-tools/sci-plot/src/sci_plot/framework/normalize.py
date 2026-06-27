"""Normalize ``data`` (a workspace file path OR inline JSON) into a DataFrame.

The first half of "don't over-demand input data types": accept many shapes,
read many file formats, fail with a clear, agent-recoverable message. Per-column
dtype coercion is the *role resolver*'s job (it knows which column plays which
role) — here we just materialize the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import pandas as pd

#: ``data`` accepts a workspace **file path** (str) or **inline JSON** — either a
#: list of row records (``[{"a": 1}, …]``) or a column dict (``{"a": [1, 2]}``).
DataInput = Union[str, list[dict[str, Any]], dict[str, list[Any]]]

_SUPPORTED = (".csv", ".tsv", ".tab", ".json", ".xlsx", ".xls", ".parquet")


def normalize(data: DataInput) -> pd.DataFrame:
    """Materialize ``data`` into a DataFrame. Raises ``ValueError`` (which the
    CLI turns into a friendly exit-2 message) on any unreadable input."""
    if isinstance(data, str):
        return _read_path(data)
    if isinstance(data, list):
        return _from_records(data)
    if isinstance(data, dict):
        return _from_columns(data)
    raise ValueError(
        "`data` must be a file path (str), a list of row records, or a "
        f"column dict; got {type(data).__name__}"
    )


def _read_path(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"file not found: {path}")
    suffix = p.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(p)
        if suffix in (".tsv", ".tab"):
            return pd.read_csv(p, sep="\t")
        if suffix == ".json":
            return pd.read_json(p)
        if suffix in (".xlsx", ".xls"):
            return pd.read_excel(p)
        if suffix == ".parquet":
            return pd.read_parquet(p)
    except Exception as e:  # noqa: BLE001 — any reader failure → friendly error
        # Includes pyarrow's ArrowInvalid (a ValueError subclass) for corrupt
        # parquet — wrap it too so the message names the offending path.
        raise ValueError(f"could not read {path}: {e}") from e
    raise ValueError(
        f"unsupported file type {suffix or '(none)'!r} for {path}; "
        f"supported: {', '.join(_SUPPORTED)}"
    )


def _from_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        raise ValueError("inline `data` is an empty list — nothing to plot")
    if not all(isinstance(r, dict) for r in records):
        raise ValueError(
            'inline list `data` must be a list of row records (objects), e.g. [{"x": 1, "y": 2}, …]'
        )
    return pd.DataFrame.from_records(records)


def _from_columns(columns: dict[str, list[Any]]) -> pd.DataFrame:
    if not columns:
        raise ValueError("inline `data` is an empty object — nothing to plot")
    try:
        return pd.DataFrame(columns)
    except ValueError as e:
        # Ragged columns (unequal lengths) is the common cause.
        raise ValueError(f"could not build a table from inline columns: {e}") from e
