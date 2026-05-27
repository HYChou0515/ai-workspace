"""CLI entry point: summarise each column of a CSV.

    csv-column-summary data.csv            # human-readable table
    csv-column-summary data.csv --json     # machine-readable (for the agent)
    csv-column-summary data.csv --plot      # + write distribution/correlation PNGs

Exit code 0 on success, 2 on a usage / file error — so the calling agent can
tell "the tool failed" from "the file had no columns".
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd


def _progress(msg: str) -> None:
    """A live progress line on stderr — the sandbox relays stderr live so it
    shows in the chat while the tool runs, and stdout stays clean for --json."""
    print(msg, file=sys.stderr, flush=True)


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


def plot(df: pd.DataFrame, csv_path: str) -> list[str]:
    """Write two PNGs next to the CSV and return their paths:
    `<name>.distributions.png` (a grid: histogram per numeric column, top-10
    bar per categorical) and, when there are ≥2 numeric columns,
    `<name>.correlations.png` (a Pearson correlation heatmap). Uses the headless
    Agg backend so it works in the sandbox with no display."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    stem = Path(csv_path).with_suffix("")
    written: list[str] = []

    cols = list(df.columns)
    ncols = min(4, len(cols)) or 1
    nrows = max(1, math.ceil(len(cols) / ncols))
    fig, raw_axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3 * nrows))
    axes = np.atleast_1d(raw_axes).ravel()
    for ax, col in zip(axes, cols):
        s = df[col]
        if pd.api.types.is_numeric_dtype(s):
            ax.hist(s.dropna(), bins=30)
        else:
            top = s.value_counts(dropna=True).head(10)
            ax.bar([str(k) for k in top.index], list(top.values))
            ax.tick_params(axis="x", rotation=45, labelsize=7)
        ax.set_title(str(col), fontsize=9)
    for ax in axes[len(cols) :]:
        ax.axis("off")
    fig.tight_layout()
    dist = f"{stem}.distributions.png"
    fig.savefig(dist, dpi=90)
    plt.close(fig)
    written.append(dist)

    num = df.select_dtypes(include="number")
    if num.shape[1] >= 2:
        corr = num.corr()
        n = len(corr)
        fig, ax = plt.subplots(figsize=(0.6 * n + 2, 0.6 * n + 2))
        im = ax.imshow(corr.values, vmin=-1, vmax=1, cmap="coolwarm")
        ax.set_xticks(range(n), labels=list(corr.columns), rotation=90, fontsize=7)
        ax.set_yticks(range(n), labels=list(corr.columns), fontsize=7)
        fig.colorbar(im, ax=ax, fraction=0.046)
        fig.tight_layout()
        heat = f"{stem}.correlations.png"
        fig.savefig(heat, dpi=90)
        plt.close(fig)
        written.append(heat)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="csv-column-summary",
        description="Summarise each column of a CSV (type, counts, nulls, stats).",
    )
    parser.add_argument("csv", help="path to the CSV file")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--plot", action="store_true", help="also write distribution/correlation PNGs"
    )
    args = parser.parse_args(argv)

    _progress(f"reading {args.csv} …")
    try:
        df = pd.read_csv(args.csv)
    except FileNotFoundError:
        print(f"error: file not found: {args.csv}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — any parse failure → usage error for the caller
        print(f"error: could not read CSV: {exc}", file=sys.stderr)
        return 2

    _progress(f"summarising {len(df.columns)} columns over {len(df)} rows …")
    cols = summarize(df)

    plots: list[str] = []
    if args.plot:
        _progress("plotting distributions + correlations …")
        plots = plot(df, args.csv)
        for p in plots:
            _progress(f"  wrote {p}")

    if args.json:
        payload = {"rows": len(df), "columns": [asdict(c) for c in cols], "plots": plots}
        print(json.dumps(payload, indent=2))
    else:
        text = _render_text(df, cols)
        if plots:
            text += "\n\nplots:\n" + "\n".join(f"  {p}" for p in plots)
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
