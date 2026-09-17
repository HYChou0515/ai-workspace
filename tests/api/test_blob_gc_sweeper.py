"""The blob-GC sweeper is a pure producer (#245 → coordinator): when
`gc_interval` is set, the lifespan ticks on schedule, takes the fleet-wide
per-window ``ScanLease`` and asks the ``BlobGcCoordinator`` for ONE reconcile;
the reconcile itself runs wherever the ``blob-gc`` JobType is consumed — a
worker pod, or this process in the all-in-one deploy. The reclaim behaviour is
covered in tests/filestore/test_blob_gc.py — here we assert the wiring."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from specstar import QB
from specstar.types import TaskStatus

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.blob_gc import BlobGcJob
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.monitor import InMemoryMonitor
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.triggers import ScanLease, SpecstarTriggerStore, register_trigger_store

from ._client import TestClient

_LEASE_KEY = "__scan__:blob-gc"


def _app(spec, *, gc_interval, monitor=None, **kw):
    return TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=SpecstarFileStore(spec),
            runner=ScriptedAgentRunner([]),
            gc_interval=gc_interval,
            monitor=monitor,
            **kw,
        )
    )


def _jobs(spec) -> list[BlobGcJob]:
    rm = spec.get_resource_manager(BlobGcJob)
    return [r.data for r in rm.list_resources(QB.all().build()) if isinstance(r.data, BlobGcJob)]


async def _until(pred, budget_s: float = 3.0):
    for _ in range(int(budget_s / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


def _spy_gc(spec) -> dict[str, int]:
    calls = {"n": 0}
    real = spec.gc

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    spec.gc = _counting
    return calls


def test_a_pure_producer_asks_for_the_reconcile_and_runs_none_of_it() -> None:
    """`run_consumers=False`: the tick leaves a PENDING `BlobGcJob` row behind
    the window lease and never calls `SpecStar.gc` in this process — the
    whole-table rescan is what killed the API pod that won the old lease."""
    spec = make_spec(default_user="u")
    calls = _spy_gc(spec)
    with _app(spec, gc_interval=timedelta(seconds=0.05), run_consumers=False):
        assert asyncio.run(_until(lambda: bool(_jobs(spec))))
        assert [j.status for j in _jobs(spec)] == [TaskStatus.PENDING]
        assert SpecstarTriggerStore(spec).last_window(_LEASE_KEY) != ""
        # Several more windows pass; the ask coalesces onto the pending job.
        time.sleep(0.3)
        assert len(_jobs(spec)) == 1
    assert calls["n"] == 0


def test_a_pod_that_loses_the_window_lease_asks_for_nothing() -> None:
    """The row existing proves the lease is taken, not that losing it does
    anything: another pod already holds a window an hour ahead — this app boots,
    ticks, and must enqueue nothing."""
    spec = make_spec(default_user="u")
    register_trigger_store(spec)
    ahead = ScanLease(
        SpecstarTriggerStore(spec), "blob-gc", interval_s=0.05, now=lambda: time.time() + 3600
    )
    assert ahead.claim()
    with _app(spec, gc_interval=timedelta(seconds=0.05), run_consumers=False):
        assert not asyncio.run(_until(lambda: bool(_jobs(spec)), budget_s=1.0))


def test_all_in_one_runs_the_reconcile_in_process_and_emits_both_signals() -> None:
    """#407: with consumers on (the default) the same process drains the job, so
    the generic blob_gc GC stats AND the WorkspaceFile ws_census snapshot land
    in the monitor — both durable-store signals, end to end."""
    spec = make_spec(default_user="u")
    mon = InMemoryMonitor()
    with _app(spec, gc_interval=timedelta(seconds=0.05), monitor=mon):
        assert asyncio.run(
            _until(lambda: {"blob_gc", "ws_census"} <= {e.get("kind") for e in mon.recent()})
        )
    census = next(e for e in mon.recent() if e.get("kind") == "ws_census")
    assert census["total_workspacefile_rows"] == 0  # nothing written this run
    assert "t" in census  # a timestamp for the trend axis


def test_no_sweeper_when_interval_none() -> None:
    spec = make_spec(default_user="u")
    calls = _spy_gc(spec)
    with _app(spec, gc_interval=None):
        time.sleep(0.2)
        assert _jobs(spec) == []
    assert calls["n"] == 0
