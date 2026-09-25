"""The client's size refusals are the server's, on the same inputs (#830).

`cpuFault` / `memoryFault` in `web/src/components/ItemEnvironmentSize.ts`
claim to refuse, on the keystroke, exactly what `PUT /resources` would refuse
with a 422 — including a value past `_MAX_CORES` / `_MAX_BYTES`, which the
client learns from the record rather than holding a copy of. That claim is
only worth anything as a comparison against the thing that actually refuses,
and the two halves run under different toolchains (the python job has no
`node_modules`, the web job has no `uv`), so the comparison goes through an
answer sheet: `tests/fixtures/item_size_parity.json`.

THIS test grades the sheet against the server — the oracle. The oracle is
`_validated_resources()` called directly: it is the one function in which the
route's 200/422 on a value is decided (the route's other refusals, 404 and
409, are about the item, not the value), and calling it costs nothing where
booting the app costs seconds per test. That GET /environment reports the
same constants the gate holds is pinned separately, through the route, by
`test_the_environment_reports_the_ceilings_the_put_refuses_against`.

Move a constant and this test fails on the ceiling first; correct that on the
sheet and the next run names every row whose verdict moved.
`web/tests/itemSizeParity.test.ts` then says whether the client followed.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from workspace_app.api.item_routes import (
    _MAX_BYTES,
    _MAX_CORES,
    _ResourcesBody,
    _validated_resources,
)

SHEET = Path(__file__).resolve().parents[1] / "fixtures" / "item_size_parity.json"


def _accepts(body: _ResourcesBody) -> bool:
    """What the PUT would answer for this body: 200 (True) or 422 (False)."""
    try:
        _validated_resources(body)
    except HTTPException as exc:
        assert exc.status_code == 422, exc.detail
        return False
    return True


def test_the_parity_sheet_is_the_servers_own_answer():
    sheet = json.loads(SHEET.read_text(encoding="utf-8"))

    stale_sheet = "stale sheet: re-read the server"
    assert sheet["max_cpu_cores"] == _MAX_CORES, stale_sheet
    assert sheet["max_memory_bytes"] == _MAX_BYTES, stale_sheet

    stale: list[str] = []
    for row in sheet["cpu"]:
        # The client sends `Number(typed)`; the sheet carries that number.
        if _accepts(_ResourcesBody(cpu_cores=row["sent"])) != row["accepted"]:
            stale.append(f"cpu {row['typed']!r}: server says {not row['accepted']}")
    for row in sheet["memory"]:
        # `sent: null` is a spelling the client could not read and would never
        # send; the server must refuse the TYPED text too, or the client is
        # refusing something the server would have taken.
        if _accepts(_ResourcesBody(memory=row["sent"] or row["typed"])) != row["accepted"]:
            stale.append(f"memory {row['typed']!r}: server says {not row['accepted']}")
    assert stale == [], "\n".join(stale)

    # The sheet must be able to fail: at least one row each way, per
    # dimension, and at least one refusal that is PAST THE CEILING rather
    # than unreadable — an empty or one-sided sheet grades nothing.
    for dim in ("cpu", "memory"):
        verdicts = {row["accepted"] for row in sheet[dim]}
        assert verdicts == {True, False}, f"{dim}: one-sided sheet"
    assert any(r["sent"] is not None and not r["accepted"] for r in sheet["memory"]), (
        "no memory row past the ceiling"
    )
    assert any(
        r["sent"] is not None and r["sent"] > 0 and not r["accepted"] for r in sheet["cpu"]
    ), "no cpu row past the ceiling"
