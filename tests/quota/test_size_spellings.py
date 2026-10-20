"""The server reads every size spelling the client reads, as the same bytes.

An admin typed `123GB` into a person's override; the client's own size fields
take that spelling, the server's `parse_size` did not, and the value was stored
anyway — every later read of that person's limits then raised. One answer
sheet, `tests/fixtures/size_spellings.json`, graded here against the server
(the oracle) and in `web/tests/sizeSpellings.test.ts` against the client.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.quota.limits import ResourceLimitError, parse_size

SHEET = Path(__file__).resolve().parents[1] / "fixtures" / "size_spellings.json"


def test_the_server_reads_the_sheet():
    rows = json.loads(SHEET.read_text(encoding="utf-8"))["rows"]
    wrong: list[str] = []
    for row in rows:
        try:
            got: int | None = parse_size(row["typed"])
        except ResourceLimitError:
            got = None
        if got != row["bytes"]:
            wrong.append(f"{row['typed']!r}: server reads {got}, sheet says {row['bytes']}")
    assert wrong == [], "\n".join(wrong)
    # The sheet must be able to fail both ways.
    assert {row["bytes"] is None for row in rows} == {True, False}


def test_a_refusal_says_how_to_write_a_size():
    with pytest.raises(ResourceLimitError, match="2GB"):
        parse_size("12X")
