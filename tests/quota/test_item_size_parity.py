"""The client's size refusals are the server's, on the same inputs (#830).

`cpuFault` / `memoryFault` in `web/src/components/ItemEnvironmentSize.ts`
claim to refuse, on the keystroke, exactly what `PUT /resources` would refuse
with a 422 — including a value past `_MAX_CORES` / `_MAX_BYTES`, which the
client learns from the record rather than holding a copy of. That claim is
only worth anything as a comparison against the thing that actually refuses,
and the two halves run under different toolchains (the python job has no
`node_modules`, the web job has no `uv`), so the comparison goes through an
answer sheet: `tests/fixtures/item_size_parity.json`.

THIS test grades the sheet against the server — the oracle. Every ceiling on
it must be the one the record reports, and every row's verdict must be the
PUT's, on the spelling the client would send. `web/tests/itemSizeParity.test.ts`
then grades the client against the sheet. Move a constant and this test fails
on the ceiling first; correct that on the sheet and the next run names every
row whose verdict moved; the web test then says whether the client followed.
"""

from __future__ import annotations

import json
from pathlib import Path

from workspace_app.config.schema import PerUserResources
from workspace_app.quota.limits import ResourceLimits

from .test_item_resources import _app, _mk

SHEET = Path(__file__).resolve().parents[1] / "fixtures" / "item_size_parity.json"


def test_the_parity_sheet_is_the_servers_own_answer():
    sheet = json.loads(SHEET.read_text(encoding="utf-8"))
    # Nothing but the hard ceilings in the way: no App ceiling and no owner
    # budget, so the only refusal a PUT can meet is the one under test. (The
    # PUT stores the STATED size regardless — a budget would only clamp what
    # is applied — but the control should not rest on that.)
    uncapped = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=0)
    with _app(PerUserResources(cpu=0.0), app_resources={"rca": uncapped}) as (
        client,
        spec,
        _sandbox,
    ):
        item = _mk(spec, "alice")

        env = client.get(f"/a/rca/items/{item}/environment").json()
        stale_sheet = "stale sheet: re-read the server"
        assert sheet["max_cpu_cores"] == env["max_cpu_cores"], stale_sheet
        assert sheet["max_memory_bytes"] == env["max_memory_bytes"], stale_sheet

        put = f"/a/rca/items/{item}/resources"
        stale: list[str] = []
        for row in sheet["cpu"]:
            # The client sends `Number(typed)`; the sheet carries that number.
            got = client.put(put, json={"cpu_cores": row["sent"]})
            if (got.status_code == 200) != row["accepted"]:
                stale.append(f"cpu {row['typed']!r} -> {got.status_code} {got.text}")
        for row in sheet["memory"]:
            # `sent: null` is a spelling the client could not read and would
            # never send; the server must refuse the TYPED text too, or the
            # client is refusing something the server would have taken.
            got = client.put(put, json={"memory": row["sent"] or row["typed"]})
            if (got.status_code == 200) != row["accepted"]:
                stale.append(f"memory {row['typed']!r} -> {got.status_code} {got.text}")
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
        # `sent` is a number on every cpu row today; a null one (a text the
        # client could not read) is compared like the memory rows are, not
        # ordered.
        assert any(
            r["sent"] is not None and r["sent"] > 0 and not r["accepted"] for r in sheet["cpu"]
        ), "no cpu row past the ceiling"
