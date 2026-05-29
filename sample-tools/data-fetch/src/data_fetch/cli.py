"""CLI: materialise a *named* dataset into the workspace as CSV.

Single-command package — a reference example of the simplest tool
shape under the 3-stage contract (see docs/plan-skills-and-tools.md
§B.2). The host runs the launch wrapper one of three ways:

    ./launch                       → list commands as JSON
    ./launch data-fetch            → that command's metadata + JSON schema
    ./launch data-fetch '<json>'   → pydantic-validate + execute

For multi-command examples (one venv, several commands), see
``sample-tools/csv-column-summary``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, ValidationError
from sklearn import datasets as skd

# name → (bundled sklearn loader, column-name prefix, optional base-col cap).
# The URLs/schemas live HERE, not in the model's output; the agent picks a name.
_CATALOG: dict[str, dict] = {
    "sensor-telemetry": {"base": "breast_cancer", "prefix": "sensor"},
    "alloy-batches": {"base": "wine", "prefix": "alloy"},
    "process-readings": {"base": "diabetes", "prefix": "reading"},
    "panel-inspection": {"base": "digits", "prefix": "pixel", "max_base_cols": 20},
}

_LOADERS = {
    "breast_cancer": skd.load_breast_cancer,
    "wine": skd.load_wine,
    "diabetes": skd.load_diabetes,
    "digits": skd.load_digits,
}

_MIN_NUMERIC_COLS = 18  # + 6 synthetic (id/line/shift/operator/timestamp/label) ⇒ 20+ total


# ─── tool contract ───────────────────────────────────────────────────


_DATASET_NAMES = Literal[
    "sensor-telemetry", "alloy-batches", "process-readings", "panel-inspection"
]


class FetchArgs(BaseModel):
    """The agent's input. ``name`` is constrained by an enum so the model
    can never invent a bad value; ``out`` defaults to ``<name>.csv``."""

    name: _DATASET_NAMES = Field(description="which dataset to materialise")
    rows: int = Field(default=25_000, ge=1, description="row count")
    out: str | None = Field(default=None, description="output CSV path (default: <name>.csv)")
    seed: int = Field(default=0, description="random seed for the synthesis")


DESCRIPTION = (
    "Materialise a named (sklearn-augmented) dataset into the workspace as a CSV. "
    "The dataset is chosen by NAME from a fixed catalog — you cannot pass a URL."
)


# ─── synthesis ───────────────────────────────────────────────────────


@dataclass
class Result:
    name: str
    path: str
    rows: int
    columns: int


def synthesize(name: str, *, rows: int = 25_000, seed: int = 0) -> pd.DataFrame:
    """Augment the catalog's base sklearn dataset into a `rows`×(20+) frame.
    Deterministic for a given (name, rows, seed)."""
    if name not in _CATALOG:
        raise KeyError(name)
    spec = _CATALOG[name]
    rng = np.random.default_rng(seed)

    ds = _LOADERS[spec["base"]]()
    base = np.asarray(ds.data, dtype=float)
    cap = spec.get("max_base_cols")
    if cap:
        base = base[:, :cap]
    target = np.asarray(ds.target)
    n_base, n_feat = base.shape

    # Bootstrap-resample to `rows`, then jitter each column by ~5% of its std so
    # the rows aren't bare duplicates.
    idx = rng.integers(0, n_base, size=rows)
    x = base[idx].astype(float)
    std = base.std(axis=0)
    std[std == 0] = 1.0
    x += rng.normal(0.0, 0.05, x.shape) * std

    prefix = spec["prefix"]
    df = pd.DataFrame({f"{prefix}_{i:02d}": x[:, i] for i in range(n_feat)})

    # Top up numeric columns with engineered ratios until we have enough.
    while df.shape[1] < _MIN_NUMERIC_COLS:
        a, b = rng.integers(0, n_feat, size=2)
        df[f"{prefix}_d{df.shape[1]:02d}"] = x[:, a] / (np.abs(x[:, b]) + 1e-6)

    # Synthetic non-numeric columns → mixed dtypes (id / categorical / datetime / label).
    df.insert(0, "record_id", [f"R{seed:02d}-{n:07d}" for n in range(rows)])
    df["line"] = rng.choice(["L1", "L2", "L3", "L4"], size=rows)
    df["shift"] = rng.choice(["day", "swing", "night"], size=rows)
    df["operator"] = rng.choice([f"op{n:02d}" for n in range(12)], size=rows)
    start = np.datetime64("2025-01-01T00:00:00")
    df["timestamp"] = start + rng.integers(0, 120 * 24 * 3600, size=rows).astype("timedelta64[s]")
    df["label"] = target[idx]

    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def run(args: FetchArgs) -> Result:
    """Execute the fetch with already-validated args; prints stderr
    progress (sandbox relays it live to the chat — issue #23) and the
    written CSV summary on stdout."""
    print(f"synthesizing '{args.name}' ({args.rows} rows) …", file=sys.stderr, flush=True)
    df = synthesize(args.name, rows=args.rows, seed=args.seed)
    out = args.out or f"{args.name}.csv"
    print(
        f"writing {df.shape[0]} rows × {df.shape[1]} cols → {out} …",
        file=sys.stderr,
        flush=True,
    )
    df.to_csv(out, index=False)
    result = Result(name=args.name, path=out, rows=df.shape[0], columns=df.shape[1])
    print(json.dumps(asdict(result)))
    return result


# ─── 3-stage dispatcher (hand-written so the contract stays visible) ──


def main(argv: list[str] | None = None) -> int:
    a = argv if argv is not None else sys.argv[1:]
    # Stage 1: bare → list commands.
    if not a:
        print(json.dumps([{"name": "data-fetch", "description": DESCRIPTION}]))
        return 0
    cmd = a[0]
    if cmd != "data-fetch":
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    # Stage 2: command only → metadata + JSON schema.
    if len(a) == 1:
        print(
            json.dumps(
                {
                    "name": "data-fetch",
                    "description": DESCRIPTION,
                    "params_json_schema": FetchArgs.model_json_schema(),
                }
            )
        )
        return 0
    # Stage 3: command + JSON args → pydantic validate + run.
    try:
        args = FetchArgs.model_validate_json(a[1])
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        return 2
    run(args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
