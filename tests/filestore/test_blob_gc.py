"""Blob GC scheduling (#245): a single CAS lease so only one pod runs the
(full, deleting) reconcile per window, plus the reconcile actually reclaiming an
orphaned workspace-file blob. specstar v0.11.10's `SpecStar.gc` is the engine;
this module only schedules + guards it."""

import datetime as dt

from specstar import BackendBinding, BackendConfig, ConnectionProfile
from specstar.types import PreconditionFailedError

from workspace_app.filestore.blob_gc import (
    _GcLease,
    register_gc_lease,
    run_blob_gc,
    try_claim_gc,
)
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.monitor import InMemoryMonitor
from workspace_app.resources import make_spec

_HOUR_MS = 3_600_000


def _disk_backend(root) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


def test_first_pod_claims_the_lease():
    spec = make_spec()
    register_gc_lease(spec)
    assert try_claim_gc(spec, now_ms=1_000, ttl_ms=_HOUR_MS) is True


def test_a_held_lease_blocks_a_second_claimer():
    spec = make_spec()
    register_gc_lease(spec)
    assert try_claim_gc(spec, now_ms=1_000, ttl_ms=_HOUR_MS) is True
    # same window, lease still live → the next pod no-ops
    assert try_claim_gc(spec, now_ms=2_000, ttl_ms=_HOUR_MS) is False


def test_an_expired_lease_can_be_reclaimed():
    spec = make_spec()
    register_gc_lease(spec)
    assert try_claim_gc(spec, now_ms=1_000, ttl_ms=_HOUR_MS) is True
    # a window later the lease has expired → a pod reclaims it
    assert try_claim_gc(spec, now_ms=1_000 + _HOUR_MS + 1, ttl_ms=_HOUR_MS) is True


def test_run_blob_gc_is_a_noop_without_the_lease():
    spec = make_spec()
    register_gc_lease(spec)
    now = dt.datetime(2026, 6, 27, 12, 0, tzinfo=dt.UTC)
    assert run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=now) is not None
    # second run in the same window can't claim → no-op
    assert run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=now) is None


def test_run_blob_gc_defaults_now_to_wall_clock():
    # No `now` given → it stamps the current UTC time and still runs (first claim).
    spec = make_spec()
    register_gc_lease(spec)
    assert run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS) is not None


def test_run_blob_gc_records_a_telemetry_event_when_it_runs():
    # #407: a pod that wins the lease and runs the reconcile emits one blob_gc
    # summary event (GcStats + elapsed) — the global GC-cost / cardinality signal.
    spec = make_spec()
    register_gc_lease(spec)
    mon = InMemoryMonitor()
    now = dt.datetime(2026, 6, 27, 12, 0, tzinfo=dt.UTC)
    stats = run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=now, monitor=mon)
    assert stats is not None
    events = [e for e in mon.recent() if e.get("kind") == "blob_gc"]
    assert len(events) == 1
    ev = events[0]
    assert ev["mode"] == "reconcile"
    assert ev["deleted"] == stats.deleted
    assert ev["quarantined"] == stats.quarantined
    assert ev["restored"] == stats.restored
    assert ev["live"] == stats.live
    assert ev["scan_complete"] == stats.scan_complete
    assert ev["elapsed_ms"] >= 0


def test_run_blob_gc_records_nothing_when_lease_lost():
    # The pod that loses the lease returns before the reconcile, so it emits no
    # event — only the one pod that actually ran does.
    spec = make_spec()
    register_gc_lease(spec)
    mon = InMemoryMonitor()
    now = dt.datetime(2026, 6, 27, 12, 0, tzinfo=dt.UTC)
    run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=now, monitor=mon)  # wins → 1
    run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=now, monitor=mon)  # lost lease
    assert len([e for e in mon.recent() if e.get("kind") == "blob_gc"]) == 1


def test_try_claim_loses_a_concurrent_cas(monkeypatch):
    # When another pod wins the CAS between our read and write, the modify raises
    # PreconditionFailedError and we back off (return False), not crash.
    spec = make_spec()
    register_gc_lease(spec)
    rm = spec.get_resource_manager(_GcLease)

    def _raced(*_a, **_k):
        raise PreconditionFailedError("blob-gc", "expected", "actual")

    monkeypatch.setattr(rm, "modify", _raced)
    assert try_claim_gc(spec, now_ms=1_000, ttl_ms=_HOUR_MS) is False


async def test_reconcile_reclaims_a_deleted_files_blob_but_keeps_referenced(tmp_path):
    """End-to-end: deleting a workspace file orphans its blob; a later reconcile
    (past t1 then past t2) physically reclaims it, while a still-referenced file's
    blob survives. Needs the disk blob store (the in-memory backend has none)."""
    spec = make_spec(backend=_disk_backend(tmp_path))
    register_gc_lease(spec)
    store = SpecstarFileStore(spec)
    await store.write("ws", "/keep", b"k" * 100)
    await store.write("ws", "/gone", b"g" * 200)
    await store.delete("ws", "/gone")  # its blob is now an orphan

    far = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    # pass 1 quarantines the orphan (now ≫ t1 past the blob's write time)
    run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=far)
    # pass 2, two days on (lease expired, dwell ≥ t2) → the orphan is deleted
    stats = run_blob_gc(spec, t1="1h", t2="24h", ttl_ms=_HOUR_MS, now=far + dt.timedelta(days=2))
    assert stats is not None
    assert stats.deleted == 1  # exactly the orphaned /gone blob
    assert await store.read("ws", "/keep") == b"k" * 100  # referenced blob survived
