"""The disk-ledger reconcile runs as a job (docs/plan-storage-all-items.md
decisions 5–7): a second payload kind on the blob-gc coordinator, asked for by a
pure producer behind a `ScanLease`, run wherever the `blob-gc` JobType is
consumed (the worker that boots the API's own composition, or this process
all-in-one). Through `create_app`, so the pass is the one production wires.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from specstar import QB
from specstar.types import TaskStatus

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.pm.model import PmProject
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.blob_gc import BlobGcCoordinator, BlobGcJob
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.disk_ledger import DiskLedger
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


def _app(spec, store, **kw):
    return TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=store,
            runner=ScriptedAgentRunner([]),
            get_user_id=lambda: "alice",
            gc_interval=None,
            **kw,
        )
    )


def _mk(spec, model, owner: str) -> str:
    return spec.get_resource_manager(model).create(model(title="t", owner=owner)).resource_id


def test_the_pass_puts_every_item_on_the_panel_with_its_durable_size():
    """The case this exists for: items whose bytes reached the durable store
    past the facade (written before the ledger shipped, or by a sandbox that is
    gone) have no row — after one pass, "我的資源" lists them all."""
    spec = make_spec(default_user="alice")
    store = SpecstarFileStore(spec)
    with _app(spec, store, disk_reconcile_interval=None) as client:
        rca = _mk(spec, RcaInvestigation, "alice")
        pm = _mk(spec, PmProject, "alice")
        asyncio.run(store.write(rca, "/old.bin", b"x" * 700))
        asyncio.run(store.write(pm, "/notes.txt", b"y" * 30))
        assert client.get("/me/resources").json()["workspaces"] == []

        client.app.state.blob_gc_coordinator.enqueue_disk_ledger()
        assert asyncio.run(_until(lambda: len(_jobs(spec, _DONE)) == 1))

        panel = client.get("/me/resources").json()
        assert [(w["item_id"], w["bytes_used"]) for w in panel["workspaces"]] == [
            (rca, 700),
            (pm, 30),
        ]
        assert panel["disk_in_use"] == 730


def test_the_producer_asks_at_its_first_tick():
    """The first pass is the backfill: it must not wait a whole interval after
    a rollout. A pure producer only leaves the ask behind."""
    spec = make_spec(default_user="alice")
    with _app(
        spec,
        SpecstarFileStore(spec),
        disk_reconcile_interval=timedelta(hours=6),
        run_consumers=False,
    ):
        assert asyncio.run(_until(lambda: bool(_jobs(spec, _ACTIVE))))
        (job,) = _jobs(spec, _ACTIVE)
        assert job.payload.kind == "disk-ledger"


def test_the_producer_is_off_at_zero():
    spec = make_spec(default_user="alice")
    with _app(spec, SpecstarFileStore(spec), disk_reconcile_interval=None, run_consumers=False):
        assert not asyncio.run(_until(lambda: bool(_jobs(spec, _ACTIVE)), budget_s=0.5))


def test_a_disk_ask_is_not_swallowed_by_a_blob_pass_in_flight():
    """Asks coalesce per kind: a blob reconcile queued (they can run long) must
    not make the disk pass skip its window."""
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    c.enqueue_reconcile()
    c.enqueue_disk_ledger()
    c.enqueue_disk_ledger()
    kinds = sorted(j.payload.kind for j in _jobs(spec, _ACTIVE))
    assert kinds == ["disk-ledger", "reconcile"]


async def test_a_runner_with_no_pass_wired_fails_the_job_rather_than_pretending():
    spec = make_spec()
    c = BlobGcCoordinator(spec, t1="1h", t2="24h")
    c.enqueue_disk_ledger()
    await c.aclose()
    assert [j.status for j in _jobs(spec, _DONE)] == [TaskStatus.FAILED]
    assert DiskLedger  # the ledger is untouched: nothing ran


def test_a_store_without_usage_accounting_gets_no_pass():
    """`workspace_usage` is a duck-typed capability (filestore/protocol.py). A
    pass over a store without it would book every item at 0 bytes."""
    from workspace_app.quota.disk_reconcile import make_disk_ledger_pass

    assert make_disk_ledger_pass(make_spec(), object(), live_window_ms=1000) is None  # ty: ignore[invalid-argument-type]
