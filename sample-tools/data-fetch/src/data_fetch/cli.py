"""CLI: materialise a *named* dataset into the workspace as CSV.

The agent never supplies a URL or a schema — it picks a NAME from a fixed
catalog. Each name maps to a bundled scikit-learn dataset that we augment
(bootstrap-resample + jitter + synthetic id / categorical / datetime columns)
into a large, mixed-dtype table disguised as a domain dataset. Fully offline.

    data-fetch --list                       # available dataset names
    data-fetch sensor-telemetry             # → sensor-telemetry.csv (25k+ rows, 20+ cols)
    data-fetch alloy-batches --rows 50000 --out /data/alloy.csv
    data-fetch sensor-telemetry --json

Exit 0 on success, 2 on a usage error (unknown name).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="data-fetch",
        description="Materialise a named (sklearn-augmented) dataset into the workspace as CSV.",
    )
    parser.add_argument("name", nargs="?", help="dataset name (see --list)")
    parser.add_argument("--out", help="output CSV path (default: <name>.csv)")
    parser.add_argument("--rows", type=int, default=25_000, help="row count (default 25000)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--list", action="store_true", help="list available dataset names")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if args.list or not args.name:
        names = sorted(_CATALOG)
        if args.json:
            print(json.dumps({"datasets": names}, indent=2))
        else:
            print("available datasets:")
            for n in names:
                print(f"  - {n}")
        return 0

    if args.name not in _CATALOG:
        print(
            f"error: unknown dataset {args.name!r}. available: {', '.join(sorted(_CATALOG))}",
            file=sys.stderr,
        )
        return 2

    rows = max(1, args.rows)
    # Progress on stderr — the sandbox relays stderr live, so a long fetch shows
    # in the chat as it runs instead of a silent wait (issue #23).
    print(f"synthesizing '{args.name}' ({rows} rows) …", file=sys.stderr, flush=True)
    df = synthesize(args.name, rows=rows, seed=args.seed)
    out = args.out or f"{args.name}.csv"
    print(f"writing {df.shape[0]} rows × {df.shape[1]} cols → {out} …", file=sys.stderr, flush=True)
    df.to_csv(out, index=False)
    result = Result(name=args.name, path=out, rows=df.shape[0], columns=df.shape[1])

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"wrote {result.path} — {result.rows} rows × {result.columns} columns")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
