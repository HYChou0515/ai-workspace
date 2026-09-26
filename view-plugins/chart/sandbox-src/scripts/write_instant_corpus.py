"""Write `wire-corpus/instants.json`: text datums and the epoch ms the chart
places them at — `datums.instant_ms`, the oracle (a datum read the way the
data is, in the schema's `$defs.instant` forms), or null where it places none.

The renderer's `parseInstant` (a rule's datum never visits the sandbox) is
held to this file by `option.review2.test.ts` — NaN exactly where it says
null — and `test_wire.py` holds `instant_ms` to it. Besides the hand-picked
cases, every combination of these parts is written out: years at the
edges (0001, two-digit years an engine may read as 19xx, pandas' nanosecond
range), leap days and days a month lacks, both separators, fractions of 1–3
digits, and every zone form. Rerun after changing how instants are read:

    uv run python scripts/write_instant_corpus.py
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from chart_view.datums import instant_ms

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
    # Forms one reader placed and the other did not (review rounds 3 and 4).
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
    " 2024-03-01",
    "2024-03-01\n",
    "0000-01-01",
    "2024/03/01T12:00Z",
    "2024/03/01T12:00:00+08:00",
    "0050-06-01 12:00Z",
    "0001-02-28 12:34Z",
    "9999/12/31",
    "１７０９２９４４００００",
    "٢٠٢٤-03-01",
    "２０２４-03-01",
]

YEARS = [1, 50, 99, 1677, 1678, 1900, 1970, 2024, 2262, 2263, 9999]
DAYS = [(1, 1), (2, 29), (4, 31), (12, 31)]
TIMES = ["T12:34", " 12:34:56", "T23:59:59.9", " 00:00:00.12", "T01:02:03.123"]
ZONES = ["", "Z", "+08:00", "-0530"]


def generated() -> list[str]:
    dates = [
        f"{y:04d}{sep}{m:02d}{sep}{d:02d}"
        for y, (m, d), sep in itertools.product(YEARS, DAYS, ["-", "/"])
    ]
    stamps = ["", *(t + z for t, z in itertools.product(TIMES, ZONES))]
    return [d + s for d, s in itertools.product(dates, stamps)]


def main() -> None:
    texts = list(dict.fromkeys([*TEXTS, *generated()]))
    cases = [{"text": t, "ms": instant_ms(t)} for t in texts]
    lines = ",\n".join("    " + json.dumps(c, ensure_ascii=False) for c in cases)
    CORPUS.write_text(f'{{\n  "kind": "instants",\n  "cases": [\n{lines}\n  ]\n}}\n')
    print(len(cases), sum(c["ms"] is None for c in cases))


if __name__ == "__main__":
    main()
