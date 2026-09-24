"""Write `wire-corpus/*.json`: a column's values and the wire the encoder makes.

The renderer's `wire.test.ts` decodes each `wire` with its own reader and must
get `values` back, so a byte order, width or bit order that disagrees between
the halves fails there. Rerun after changing the encoder on purpose:

    uv run python scripts/write_wire_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from chart_view.wire import bitset, encode_column

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus"

CASES: dict[str, tuple[str, list]] = {
    "f64-numbers": ("f64", [1.5, -2.0, 0.0, None, 1e300, -1e-300]),
    "time-instants": (
        "time",
        ["2024-01-01T00:00:00Z", "2024-06-30T12:34:56.789Z", None, "1969-12-31T23:59:59Z"],
    ),
    "cat-strings": ("cat", ["b", "a", None, "b", "c"]),
    "cat-numbers": ("cat", [3, 1, 3, 2]),
    "cat-booleans": ("cat", [True, False, None, True]),
    "q8-range": ("q8", [0.0, 0.5, 1.0, None, 0.25, 0.75]),
    "q8-negative": ("q8", [-3.0, 1.0, -1.0]),
    "bits-nine-rows": ("bits", [True, False, True, True, False, False, False, False, True]),
}


def _series(kind: str, values: list) -> pd.Series:
    if kind in ("f64", "q8"):
        return pd.Series([np.nan if v is None else v for v in values], dtype=float)
    return pd.Series(values, dtype=object)


def main() -> None:
    CORPUS.mkdir(exist_ok=True)
    for name, (kind, values) in CASES.items():
        if kind == "bits":
            wire = bitset(pd.Series(values, dtype=bool))
        else:
            wire = encode_column(_series(kind, values), kind)
        case = {"kind": kind, "values": values, "wire": wire}
        (CORPUS / f"{name}.json").write_text(json.dumps(case, indent=2) + "\n")
        print(name)


if __name__ == "__main__":
    main()
