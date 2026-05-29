"""Core data plumbing shared by `summarise` and `plot` commands.

Kept separate from the CLI so the two commands can import the same
``summarize()`` / ``plot()`` functions without circular dependencies.
The CLI module is the one that calls these — the multi-command dispatcher
is the only place that touches argv.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


def progress(msg: str) -> None:
    """Live progress on stderr — the sandbox relays it to the chat as it
    runs, so a long summarise doesn't look frozen. Stdout stays clean
    for the command's JSON result."""
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
    """Per-column summary: dtype + count + nulls + unique + stats /
    top-values depending on whether the column is numeric."""
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


def plot(df: pd.DataFrame, csv_path: str) -> list[str]:
    """Write two PNGs next to the CSV and return their paths:
    ``<name>.distributions.png`` (a grid: histogram per numeric column,
    top-10 bar per categorical) and, when there are ≥2 numeric columns,
    ``<name>.correlations.png`` (a Pearson correlation heatmap). Uses
    the headless Agg backend so it works in the sandbox with no display."""
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
