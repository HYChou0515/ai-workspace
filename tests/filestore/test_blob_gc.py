"""Blob GC is a job (#245 → coordinator): the API asks for one reconcile per
window and a worker runs it. specstar's `SpecStar.gc` is the engine; this
coordinator queues it, coalesces asks, records the telemetry, bounds its rows.

The reconcile itself (`collect_all_referenced_file_ids`) materialises every
ResourceMeta of every blob-capable model at once and streams every revision —
the whole-table read that killed an API pod right after `blob-gc: won lease`.
Hence a job, not a sweep."""

import datetime as dt

import pytest
from specstar import QB, BackendBinding, BackendConfig, ConnectionProfile
from specstar.types import TaskStatus

from workspace_app.filestore.blob_gc import BlobGcCoordinator, BlobGcJob, BlobGcPayload
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.monitor import InMemoryMonitor
from workspace_app.resources import make_spec

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DONE = [TaskStatus.COMPLETED, TaskStatus.FAILED]


def _disk_backend(root) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


def _rows(spec, statuses):
    rm = spec.get_resource_manager(BlobGcJob)
    return list(rm.list_resources(QB["status"].in_(statuses).build()))


async def _drain(coordinator) -> None:
    # `aclose()` starts a consumer if none is running, drains, then stops it.
    await coordinator.aclose()


def test_enqueue_creates_one_job_and_coalesces_a_second_ask():
    """Two asks while one is still queued leave ONE row — the fleet-wide "one
    asker per window" is the API's ScanLease; this covers an ask that overlaps a
    reconcile in flight."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    c.enqueue_reconcile()
    c.enqueue_reconcile()
    assert len(_rows(spec, _ACTIVE)) == 1


async def test_handling_a_job_runs_the_reconcile_and_records_both_signals():
    """#407: the pod that RUNS the reconcile emits the `blob_gc` GcStats event and
    the `ws_census` WorkspaceFile snapshot — the two durable-store signals that
    used to come from the API sweeper's tick."""
    spec = make_spec()
    store = SpecstarFileStore(spec)
    mon = InMemoryMonitor()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h", monitor=mon, filestore=store)
    c.enqueue_reconcile()
    await _drain(c)
    events = [e for e in mon.recent() if e.get("kind") == "blob_gc"]
    assert len(events) == 1
    ev = events[0]
    assert ev["mode"] == "reconcile"
    assert {"deleted", "quarantined", "restored", "live", "scan_complete"} <= set(ev)
    assert ev["elapsed_ms"] >= 0
    census = next(e for e in mon.recent() if e.get("kind") == "ws_census")
    assert census["total_workspacefile_rows"] == 0  # nothing written this run
    assert "t" in census  # a timestamp for the trend axis
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.COMPLETED]


async def test_without_a_filestore_only_the_gc_event_is_recorded():
    spec = make_spec()
    mon = InMemoryMonitor()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h", monitor=mon)
    c.enqueue_reconcile()
    await _drain(c)
    kinds = [e.get("kind") for e in mon.recent()]
    assert kinds.count("blob_gc") == 1
    assert "ws_census" not in kinds


async def test_a_finished_job_prunes_the_earlier_finished_ones():
    """A reconcile is asked for on a timer, forever, and every ask is a durable
    row; nothing else reclaims job rows. Between windows at most ONE finished
    row remains."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    for _ in range(3):
        c.enqueue_reconcile()
        await _drain(c)
    assert len(_rows(spec, _DONE)) == 1


async def test_reconcile_reclaims_a_deleted_files_blob_but_keeps_referenced(tmp_path):
    """End-to-end through the queue: deleting a workspace file orphans its blob;
    a later reconcile (past t1 then past t2) physically reclaims it, while a
    still-referenced file's blob survives. Needs the disk blob store (the
    in-memory backend has none). `now` is the coordinator's clock seam."""
    spec = make_spec(backend=_disk_backend(tmp_path))
    store = SpecstarFileStore(spec)
    await store.write("ws", "/keep", b"k" * 100)
    await store.write("ws", "/gone", b"g" * 200)
    await store.delete("ws", "/gone")  # its blob is now an orphan

    clock = {"now": dt.datetime(2030, 1, 1, tzinfo=dt.UTC)}
    mon = InMemoryMonitor()
    c = BlobGcCoordinator(
        spec, t1="1h", t2="24h", monitor=mon, filestore=store, now=lambda: clock["now"]
    )
    # pass 1 quarantines the orphan (now ≫ t1 past the blob's write time)
    c.enqueue_reconcile()
    await _drain(c)
    # pass 2, two days on (dwell ≥ t2) → the orphan is deleted
    clock["now"] += dt.timedelta(days=2)
    c.enqueue_reconcile()
    await _drain(c)
    passes = [e for e in mon.recent() if e.get("kind") == "blob_gc"]
    # pass 1: the orphan is quarantined; pass 2: it is deleted. Pass 2's own
    # `quarantined` is deliberately not pinned: on the pinned specstar it is 1
    # — pass 1's job log (`__job_log__/…`), which the queue puts under an
    # explicit key and the reconcile lists as an orphan although `gc`'s
    # docstring says keyed blobs are not tracked. That is upstream's
    # contradiction to settle, not a number to write into a spec.
    # The live set is the one referenced blob both times; both scans complete.
    assert [e["deleted"] for e in passes] == [0, 1]
    assert passes[0]["quarantined"] == 1
    assert [e["live"] for e in passes] == [1, 1]
    assert [e["restored"] for e in passes] == [0, 0]
    assert all(e["scan_complete"] for e in passes)
    assert await store.read("ws", "/keep") == b"k" * 100  # referenced blob survived


async def test_a_failing_reconcile_marks_the_job_failed_not_retried(monkeypatch):
    """`max_retries=0`: the next window asks again, which is the cadence the
    in-process sweep retried at; the queue's default would run a failing
    reconcile four times back to back on the worker."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("store down")

    monkeypatch.setattr(spec, "gc", _boom)
    c.enqueue_reconcile()
    await _drain(c)
    assert calls["n"] == 1
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.FAILED]


async def test_a_reconcile_that_keeps_failing_keeps_at_most_one_row_too(monkeypatch):
    """The bound must hold where it matters most: a reconcile that fails every
    window (store down) is asked again every window, and with no retry each ask
    ends FAILED. Pruning only after a SUCCESSFUL pass would leave one FAILED row
    per window for as long as the failure lasts — so the prune runs before the
    reconcile, whatever the reconcile does."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    monkeypatch.setattr(spec, "gc", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    for _ in range(3):
        c.enqueue_reconcile()
        await _drain(c)
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.FAILED]


async def test_start_consuming_is_idempotent_and_aclose_stops_it():
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    assert not c.consuming
    c.start_consuming()
    c.start_consuming()
    assert c.consuming
    await c.aclose()
    assert not c.consuming
    await c.aclose()  # idle + not consuming → a no-op


@pytest.mark.parametrize("bad", ["", "nope"])
async def test_an_unknown_job_kind_is_ignored(bad, monkeypatch):
    """Defensive: a payload kind this build doesn't know (an older/newer pod's
    row) is logged and skipped, not crashed on — the row completes without a
    pass, and the next pass prunes it."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    monkeypatch.setattr(
        spec, "gc", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no pass"))
    )
    spec.get_resource_manager(BlobGcJob).create(
        BlobGcJob(payload=BlobGcPayload(kind=bad), partition_key="blob-gc", max_retries=0)
    )
    await _drain(c)
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.COMPLETED]
    monkeypatch.undo()
    c.enqueue_reconcile()
    await _drain(c)
    (only,) = _rows(spec, _DONE)
    assert only.data.payload.kind == "reconcile"  # the unknown-kind row was pruned


# ── the registry claim: the asker names its models, the runner must hold them ──
#
# specstar's reconcile builds the live set from the REGISTERED models only, and
# any `list` / `dict` / union / `Optional` field gets a runtime blob collector
# (only an all-scalar struct is skipped) — so "which models" is nearly every
# model, registered all over `create_app`. A
# consumer holding fewer would read every blob the missing models reference as
# an orphan: quarantined after t1, deleted after t2, silently (#804 P4). The ask
# carries the asker's registry; a runner that lacks any of it refuses, loudly.


def test_the_ask_carries_the_askers_registered_models():
    spec = make_spec()
    SpecstarFileStore(spec)  # registers workspace-file, the model #804 P4 feared losing
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    c.enqueue_reconcile()
    (row,) = _rows(spec, _ACTIVE)
    assert row.data.payload.registry == sorted(spec.resource_managers)
    assert "workspace-file" in row.data.payload.registry


async def _consume_claiming(spec, registry: list[str], monkeypatch) -> int:
    """Run one pass whose ask claims ``registry``; return how often `gc` ran."""
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    calls = {"n": 0}
    real = spec.gc

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(spec, "gc", _counting)
    spec.get_resource_manager(BlobGcJob).create(
        BlobGcJob(
            payload=BlobGcPayload(kind="reconcile", registry=registry),
            partition_key="blob-gc",
            max_retries=0,
        )
    )
    await _drain(c)
    return calls["n"]


async def test_a_runner_missing_a_claimed_model_refuses_the_pass(monkeypatch, caplog):
    spec = make_spec()  # no filestore ⇒ no workspace-file here
    claimed = sorted(spec.resource_managers) + ["workspace-file"]
    with caplog.at_level("ERROR", logger="workspace_app.filestore.blob_gc"):
        ran = await _consume_claiming(spec, claimed, monkeypatch)
    assert ran == 0
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.FAILED]
    assert "workspace-file" in caplog.text  # the refusal names what is missing


async def test_a_row_with_no_registry_claim_is_refused_too(monkeypatch):
    """A hand-made row (the auto route) carries no claim; running it would put
    the invariant on whoever happens to consume. Refuse — the sweeper's ask is
    the way in."""
    spec = make_spec()
    ran = await _consume_claiming(spec, [], monkeypatch)
    assert ran == 0
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.FAILED]


async def test_a_runner_holding_every_claimed_model_runs_the_pass(monkeypatch):
    spec = make_spec()
    ran = await _consume_claiming(spec, sorted(spec.resource_managers), monkeypatch)
    assert ran == 1
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.COMPLETED]


async def test_a_row_off_the_one_partition_is_refused(monkeypatch):
    """The auto-CRUD route can create a row with any `partition_key`, and only
    the one partition serialises passes — a second key would let two passes
    overlap. A row that is not on it is refused like a claimless one."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    calls = {"n": 0}

    def _counting(*a, **k):
        calls["n"] += 1
        raise AssertionError("must not run")

    monkeypatch.setattr(spec, "gc", _counting)
    spec.get_resource_manager(BlobGcJob).create(
        BlobGcJob(
            payload=BlobGcPayload(kind="reconcile", registry=sorted(spec.resource_managers)),
            partition_key="anything-else",
            max_retries=0,
        )
    )
    await _drain(c)
    assert calls["n"] == 0
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.FAILED]


async def test_a_telemetry_failure_after_the_pass_does_not_fail_the_pass():
    """`monitor.record` and the census run AFTER `spec.gc` — after the deletes.
    A failure there must not mark the row FAILED (the next window would re-run
    a pass whose work is done) — logged, and the pass still completes with
    its own `blob_gc` event recorded."""

    class _BrokenCensus(SpecstarFileStore):
        async def census(self) -> dict[str, int]:
            raise RuntimeError("census down")

    spec = make_spec()
    store = _BrokenCensus(spec)
    mon = InMemoryMonitor()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h", monitor=mon, filestore=store)
    c.enqueue_reconcile()
    await _drain(c)
    assert [r.data.status for r in _rows(spec, _DONE)] == [TaskStatus.COMPLETED]
    kinds = [e.get("kind") for e in mon.recent()]
    assert kinds.count("blob_gc") == 1
    assert "ws_census" not in kinds
