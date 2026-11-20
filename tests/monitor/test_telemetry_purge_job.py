"""docs/plan-telemetry-retention.md P4 (D5): the purge runs as a job — a third
payload kind on the blob-gc coordinator, asked for by a pure producer behind a
`ScanLease`, run wherever the `blob-gc` JobType is consumed. Through
`create_app`, so the wiring is the one production uses."""

from __future__ import annotations

import asyncio
import datetime as dt

from specstar import QB
from specstar.types import TaskStatus

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.blob_gc import BlobGcCoordinator, BlobGcJob
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.monitor import SpecstarMonitor, TelemetryEvent
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import TestClient

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DONE = [TaskStatus.COMPLETED, TaskStatus.FAILED]


def _jobs(spec, statuses) -> list[BlobGcJob]:
    rm = spec.get_resource_manager(BlobGcJob)
    return [r.data for r in rm.list_resources(QB["status"].in_(statuses).build())]


async def _until(pred, budget_s: float = 5.0) -> bool:
    for _ in range(int(budget_s / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


def _app(spec, **kw):
    return TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=ScriptedAgentRunner([]),
            get_user_id=lambda: "alice",
            gc_interval=None,
            monitor=SpecstarMonitor(spec),
            **kw,
        )
    )


def _seqs(spec) -> list[int]:
    rm = spec.get_resource_manager(TelemetryEvent)
    return sorted(r.data.seq for r in rm.list_resources(QB.all().build()))


def test_the_job_removes_rows_older_than_keep_days_and_keeps_the_rest():
    spec = make_spec(default_user="alice")
    with _app(spec) as client:
        rm = spec.get_resource_manager(TelemetryEvent)
        now = dt.datetime.now(dt.UTC)
        for seq, age in ((1, 20), (2, 15), (3, 13), (4, 0)):
            rm.create(TelemetryEvent(kind="span_end", seq=seq), now=now - dt.timedelta(days=age))

        client.app.state.blob_gc_coordinator.enqueue_telemetry_purge(keep_days=14)
        assert asyncio.run(_until(lambda: len(_jobs(spec, _DONE)) == 1))
        assert [j.status for j in _jobs(spec, _DONE)] == [TaskStatus.COMPLETED]
        assert _seqs(spec) == [3, 4]


def test_the_producer_asks_at_its_first_tick_with_the_configured_days():
    """A rollout starts draining at once; a pure producer only leaves the ask."""
    spec = make_spec(default_user="alice")
    with _app(spec, telemetry_keep_days=14, run_consumers=False):
        assert asyncio.run(_until(lambda: bool(_jobs(spec, _ACTIVE))))
        (job,) = _jobs(spec, _ACTIVE)
        assert (job.payload.kind, job.payload.keep_days) == ("telemetry-purge", 14)


def test_a_pod_whose_window_another_pod_already_asked_in_does_not_ask():
    """One ask per window across pods: the `ScanLease` on the shared ledger."""
    from workspace_app.api.lifecycle import TELEMETRY_PURGE_INTERVAL_S
    from workspace_app.workflow.triggers import ScanLease, SpecstarTriggerStore

    spec = make_spec(default_user="alice")
    client = _app(spec, telemetry_keep_days=14, run_consumers=False)  # models registered,
    lease = ScanLease(  # lifespan (and so the sweeper) not started yet
        SpecstarTriggerStore(spec), "telemetry-purge", interval_s=TELEMETRY_PURGE_INTERVAL_S
    )
    assert lease.claim()  # another pod took this window
    with client:
        assert not asyncio.run(_until(lambda: bool(_jobs(spec, _ACTIVE)), budget_s=0.5))


def test_the_producer_is_off_at_zero():
    spec = make_spec(default_user="alice")
    with _app(spec, telemetry_keep_days=0, run_consumers=False):
        assert not asyncio.run(_until(lambda: bool(_jobs(spec, _ACTIVE)), budget_s=0.5))


async def test_a_job_that_says_zero_days_removes_nothing():
    """0 means keep everything — never "older than now", which is every row."""
    spec = make_spec(default_user="alice")
    SpecstarMonitor(spec).record({"kind": "span_end"})
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    c.enqueue_telemetry_purge(keep_days=0)
    await c.aclose()
    assert [j.status for j in _jobs(spec, _DONE)] == [TaskStatus.COMPLETED]
    assert _seqs(spec) == [1]


async def test_the_purge_line_reaches_stdout_although_the_worker_configures_no_logging(
    capsys, monkeypatch
):  # noqa: ANN001
    """Review round 1 (#1037): the migrations entry tells the operator to look
    for this line in the blob-gc worker's log. Nothing in the worker configures
    logging, so an INFO record fell to the stdlib's last-resort handler and was
    dropped — the check would find nothing on a healthy deploy
    (observability/stdout_log.py)."""
    import logging

    from workspace_app.filestore import blob_gc
    from workspace_app.observability.stdout_log import OWN_HANDLER

    def own() -> list[logging.Handler]:
        return [h for h in blob_gc.logger.handlers if getattr(h, OWN_HANDLER, False)]

    for h in own():
        blob_gc.logger.removeHandler(h)  # rebind to this test's stdout
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.WARNING)  # what production has
    try:
        spec = make_spec(default_user="alice")
        SpecstarMonitor(spec)
        c = BlobGcCoordinator(spec, t1="1h", t2="24h")
        c.enqueue_telemetry_purge(keep_days=14)
        await c.aclose()
        assert "blob-gc: telemetry purge removed 0 rows older than" in capsys.readouterr().out
    finally:
        # Review round 2: the handler is bound to capsys's stream, closed after
        # this test — left behind, every later blob-gc INFO line raised
        # "I/O operation on closed file". The next coordinator installs afresh.
        for h in own():
            blob_gc.logger.removeHandler(h)


def test_a_purge_ask_is_not_swallowed_by_another_kind_in_flight():
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    c.enqueue_reconcile()
    c.enqueue_telemetry_purge(keep_days=14)
    c.enqueue_telemetry_purge(keep_days=14)
    kinds = sorted(j.payload.kind for j in _jobs(spec, _ACTIVE))
    assert kinds == ["reconcile", "telemetry-purge"]
