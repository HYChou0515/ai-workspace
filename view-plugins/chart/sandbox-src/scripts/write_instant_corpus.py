"""Write `wire-corpus/instants.json`: text instants and the epoch ms the sandbox
reads them as (`wire.epoch_ms`, the oracle — it is what parses the data).

The renderer's `parseInstant` (a rule's datum, which never visits the sandbox)
is held to this file by `option.review2.test.ts`; `test_wire.py` holds
`epoch_ms` and the schema's `$defs.instant` to it. A `null` ms is a text the
pattern refuses — validate names it, the renderer draws nothing for it. Rerun
after changing how instants are read:

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
    "2024-03-01T12:00:00.5",
    "2024-03-01T12:00:00.25Z",
    "2024/03/01",
    "2024/03/01 12:00",
    "2024-03-01",
    "2024-03-01T12:00:00+02:00",
    "2024-03-01T12:00:00-0530",
    "2024-03-01T12:00:00Z",
    "2024-03-01T23:59:59.999+14:00",
    "2300-01-01",
    "9999-12-31T23:59:59Z",
    "0999-01-01",
    "1709294400000",
    "2024",
    "20240301",
]

# Texts the schema's pattern refuses, each a form one reader placed and the
# other did not (review round 3).
REFUSED = [
    "2024-3-1",
    "2024-03-01t12:00:00",
    "2024-03-01T12:00:00+08",
    "2024-03-01T12:00:00 +08:00",
    "2024-03-01T12:00:00 Z",
    "2024-03-01T12:00:00 UTC",
    "2024-03-01  12:00:00",
    "01/03/2024",
    "2024-03-01T24:00:00",
    "2024-03/01",
    "2024-03-01Z",
    "2024-03-01T12:00:00.123456",
]


def main() -> None:
    ms = epoch_ms(pd.Series(TEXTS, dtype=object)).tolist()
    cases = [{"text": t, "ms": m} for t, m in zip(TEXTS, ms, strict=True)]
    cases += [{"text": t, "ms": None} for t in REFUSED]
    CORPUS.write_text(json.dumps({"kind": "instants", "cases": cases}, indent=2) + "\n")
    print(len(cases))


if __name__ == "__main__":
    main()
