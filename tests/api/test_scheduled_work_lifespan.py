"""#804: the scheduled-work sweeps are leased per window in the real app.

`ScanLease` is proven at the sweeper seams (`tests/workflow/`); this is the
wiring: an app that runs scheduled work claims BOTH scan leases — the profiles'
triggers and the pages' schedules — on the same ledger the per-trigger claims
use. A lease that is not wired here is a lease that N pods never take, and every
pod goes back to scanning.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.triggers import SpecstarTriggerStore

from ._client import TestClient


def test_the_app_claims_both_scan_leases_when_scheduled_work_is_on() -> None:
    spec = make_spec(default_user="u")
    application = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        trigger_check_interval=timedelta(milliseconds=50),
    )
    store = SpecstarTriggerStore(spec)
    with TestClient(application):

        async def _wait() -> tuple[str, str]:
            for _ in range(60):  # ~3s budget
                claimed = (
                    store.last_window("__scan__:triggers"),
                    store.last_window("__scan__:user-schedules"),
                )
                if all(claimed):
                    return claimed
                await asyncio.sleep(0.05)
            return claimed

        triggers, schedules = asyncio.run(_wait())

    assert triggers and schedules  # both scans elected a scanner for a window
