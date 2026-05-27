"""CLI entry point: summarise each column of a CSV.

    csv-column-summary data.csv            # human-readable table
    csv-column-summary data.csv --json     # machine-readable (for the agent)

Exit code 0 on success, 2 on a usage / file error — so the calling agent can
tell "the tool failed" from "the file had no columns".
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass
class ColumnSummary:
    column: str
    dtype: str
    count: int  # non-null values
    nulls: int
    unique: int
    # numeric columns
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None
    # non-numeric columns
    top_values: list[dict[str, Any]] | None = None


def _f(value: Any) -> float | None:
    """A finite float or None (NaN/inf → None so JSON stays valid)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def summarize(df: pd.DataFrame) -> list[ColumnSummary]:
    out: list[ColumnSummary] = []
    for col in df.columns:
        s = df[col]
        summary = ColumnSummary(
            column=str(col),
            dtype=str(s.dtype),
            count=int(s.count()),
            nulls=int(s.isna().sum()),
            unique=int(s.nunique(dropna=True)),
        )
        if pd.api.types.is_numeric_dtype(s) and summary.count > 0:
            summary.min = _f(s.min())
            summary.max = _f(s.max())
            summary.mean = _f(s.mean())
            summary.std = _f(s.std())
        elif summary.count > 0:
            top = s.value_counts(dropna=True).head(5)
            summary.top_values = [
                {"value": str(k), "count": int(v)} for k, v in top.items()
            ]
        out.append(summary)
    return out


def _render_text(df: pd.DataFrame, cols: list[ColumnSummary]) -> str:
    lines = [f"{len(df)} rows · {len(cols)} columns", ""]
    for c in cols:
        head = f"• {c.column}  [{c.dtype}]  count={c.count} nulls={c.nulls} unique={c.unique}"
        lines.append(head)
        if c.min is not None or c.max is not None:
            lines.append(
                f"    min={c.min:g} max={c.max:g} mean={c.mean:g} std={c.std:g}"
                if c.mean is not None and c.std is not None
                else f"    min={c.min} max={c.max}"
            )
        elif c.top_values:
            top = ", ".join(f"{t['value']}×{t['count']}" for t in c.top_values)
            lines.append(f"    top: {top}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="csv-column-summary",
        description="Summarise each column of a CSV (type, counts, nulls, stats).",
    )
    parser.add_argument("csv", help="path to the CSV file")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        df = pd.read_csv(args.csv)
    except FileNotFoundError:
        print(f"error: file not found: {args.csv}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any parse failure → usage error for the caller
        print(f"error: could not read CSV: {exc}", file=sys.stderr)
        return 2

    cols = summarize(df)
    if args.json:
        print(json.dumps({"rows": len(df), "columns": [asdict(c) for c in cols]}, indent=2))
    else:
        print(_render_text(df, cols))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
