"""Blob GC scheduling (#245).

specstar v0.11.10 ships the ref-count blob GC (`SpecStar.gc`, issue #370): its
``reconcile`` pass rescans every model's revisions for the live ``file_id`` set,
quarantines newly-orphaned blobs (``t1`` grace), restores any still referenced,
and permanently deletes quarantined blobs past ``t2``. It's explicit + user
scheduled — the library never runs it on a background thread.

This module schedules it. ``reconcile`` is a full scan that *deletes*, so running
it on every pod would N× the scan and race the deletes. A one-row **CAS lease**
lets exactly one pod run it per window; the others no-op. The lease is registered
by this module (not ``make_spec``) so the memory-default app doesn't emit its
CRUD routes — same reason ``SpecstarFileStore`` self-registers its models.

GC must run where **all** blob-referencing models are registered (the main app
spec: KB models via ``make_spec`` + ``WorkspaceFile`` via ``SpecstarFileStore``),
so the live set is complete; a slim pod with a partial model set must not run it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
from typing import TYPE_CHECKING

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    RevisionStatus,
)

if TYPE_CHECKING:
    from ..monitor import IMonitor

_LEASE_ID = "blob-gc"


class _GcLease(Struct):
    """One-row CAS lease guarding the blob-GC sweep. ``lease_until_ms`` is when
    the current holder's claim expires (epoch ms); a pod claims by CAS-bumping
    it past ``now``."""

    name: str
    lease_until_ms: int = 0


def register_gc_lease(spec: SpecStar) -> None:
    """Idempotently register the lease model and seed its single row. Safe to
    call on every pod / multiple instances (suppresses the duplicate)."""
    with contextlib.suppress(ValueError):
        spec.add_model(_GcLease)
    rm = spec.get_resource_manager(_GcLease)
    with contextlib.suppress(DuplicateResourceError):
        rm.create(_GcLease(name=_LEASE_ID), resource_id=_LEASE_ID, status=RevisionStatus.draft)


def try_claim_gc(spec: SpecStar, *, now_ms: int, ttl_ms: int) -> bool:
    """Claim the GC lease for ``ttl_ms`` from ``now_ms``. Returns True for the
    one pod that wins; False if a live (unexpired) lease is held or a concurrent
    pod won the CAS. The lease row is seeded by `register_gc_lease`."""
    rm = spec.get_resource_manager(_GcLease)
    try:
        res = rm.get(_LEASE_ID)
    except ResourceIDNotFoundError:  # pragma: no cover - register_gc_lease seeds it
        return False
    lease = res.data
    assert isinstance(lease, _GcLease)
    if lease.lease_until_ms > now_ms:
        return False
    try:
        rm.modify(
            _LEASE_ID,
            _GcLease(name=_LEASE_ID, lease_until_ms=now_ms + ttl_ms),
            status=RevisionStatus.draft,
            expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
        )
        return True
    except PreconditionFailedError:
        return False


def run_blob_gc(
    spec: SpecStar,
    *,
    t1: str,
    t2: str,
    ttl_ms: int,
    now: dt.datetime | None = None,
    monitor: IMonitor | None = None,
):
    """Run one ``reconcile`` pass IFF this pod wins the lease, else a no-op
    (returns None). Returns specstar's ``GcStats`` on a run. ``now`` is
    injectable for deterministic tests + scheduling.

    #407: when a ``monitor`` is wired, the pod that actually runs the reconcile
    emits one ``blob_gc`` telemetry event (GcStats + wall-clock) — the global
    GC-cost / blob-cardinality signal. A pod that loses the lease records
    nothing (it did no work)."""
    if now is None:
        now = dt.datetime.now(dt.UTC)
    now_ms = int(now.timestamp() * 1000)
    if not try_claim_gc(spec, now_ms=now_ms, ttl_ms=ttl_ms):
        return None
    started = time.monotonic()
    stats = spec.gc(mode="reconcile", t1=t1, t2=t2, now=now)
    if monitor is not None:
        monitor.record(
            {
                "kind": "blob_gc",
                "mode": stats.mode,
                "quarantined": stats.quarantined,
                "restored": stats.restored,
                "deleted": stats.deleted,
                "live": stats.live,
                "scan_complete": stats.scan_complete,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
    return stats
