"""Write `wire-corpus/instants.json`: text instants and the epoch ms the sandbox
reads them as (`wire.epoch_ms`, the oracle — it is what parses the data).

The renderer's `parseInstant` (a rule's datum, which never visits the sandbox)
is held to this file by `option.review2.test.ts`; `test_wire.py` holds
`epoch_ms` to it. Rerun after changing how instants are read:

    uv run python scripts/write_instant_corpus.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from chart_view.wire import epoch_ms

CORPUS = Path(__file__).resolve().parents[2] / "wire-corpus" / "instants.json"

TEXTS = [
    "2024-03-01T12:00:00",
    "2024-03-01 12:00:00",
    "2024-03-01T12:00",
    "2024-03-01T12:00:00.250",
    "2024/03/01",
    "2024-03-01",
    "2024-03-01T12:00:00+02:00",
    "2024-03-01T12:00:00-0530",
    "2024-03-01T12:00:00Z",
    "1709294400000",
]


def main() -> None:
    ms = epoch_ms(pd.Series(TEXTS, dtype=object)).tolist()
    cases = [{"text": t, "ms": m} for t, m in zip(TEXTS, ms, strict=True)]
    CORPUS.write_text(json.dumps({"kind": "instants", "cases": cases}, indent=2) + "\n")
    print(len(cases))


if __name__ == "__main__":
    main()
