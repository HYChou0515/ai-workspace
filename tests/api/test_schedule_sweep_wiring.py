"""The schedule sweep reads the durable snapshot, never the live sandbox.

`files.read` routes warm-first. On the hosted backend that probe is also the
RECOVERY trigger: a handle the address store still resolves, plus a sandbox the
reaper has taken away, means the read REBUILDS the sandbox (create + restore +
mark_ready). That is correct for a person opening a file — they are about to use
it — and wrong for a sweep, which would then resurrect the sandbox of every item
that has a `schedules.json`, once per tick, on every pod. The idle reaper would
be permanently undone for exactly those items, and nothing would say so.

The durable store answers the same question without waking anything. It lags the
live sandbox by at most one mirror interval (5s by default) — which for a
declaration that fires on the hour is no lag at all.

A source-text check, deliberately: what it guards is a WIRING choice that looks
like a harmless simplification from up close ("read is read"), and the failure it
prevents shows up as sandbox capacity, not as a test.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "src" / "workspace_app" / "api" / "app.py"


def test_the_sweep_does_not_read_through_the_live_sandbox() -> None:
    source = _APP.read_text(encoding="utf-8")

    call = re.search(r"UserScheduleSweeper\((.*?)\n        \)", source, re.DOTALL)
    assert call is not None, "the sweeper is no longer built here — move this guard with it"

    read_arg = re.search(r"read=([\w.]+)", call.group(1))
    assert read_arg is not None, "the sweeper is built without a read"
    assert read_arg.group(1) != "files.read", (
        "the schedule sweep reads through the facade, which is warm-first — on the "
        "hosted backend that rebuilds any sandbox the reaper took away, every tick, "
        "for every item that has schedules"
    )
