"""Disk on the usage record (docs/plan-capacity-usage.md §5.2 `disk`, §6.6): the
disk-ledger pass, which already visits every item on a worker, writes one row
per item (its owner as the quota charges it, its App) and one per KB collection
(the collection's bytes as the KB page sums them, its creator)."""

from __future__ import annotations

from specstar.types import Binary

from workspace_app.api.sandbox_activity import register_sandbox_activity
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.sink import UsageSink
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.disk_ledger import register_disk_ledger
from workspace_app.quota.disk_reconcile import make_disk_ledger_pass
from workspace_app.resources import Collection, SourceDoc, make_spec


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


async def test_the_pass_writes_a_row_per_item_and_per_collection():
    spec = make_spec(default_user="alice")
    store = SpecstarFileStore(spec)
    register_disk_ledger(spec)
    register_sandbox_activity(spec)
    item = (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="t", owner="alice"))
        .resource_id
    )
    await store.write(item, "/a.bin", b"x" * 700)
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(collection_id=cid, path="a.md", content=Binary(data=b"y" * 300), status="ready")
    )
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="worker", pod="w", window_s=300, queue_max=100)

    run = make_disk_ledger_pass(spec, store, live_window_ms=1000, usage_recorder=recorder)
    assert run is not None
    await run()
    recorder.close()

    rows = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "disk"]
    workspace = [r for r in rows if r["scope"] == "workspace"]
    kb = [r for r in rows if r["scope"] == "kb"]
    assert [(r["item"], r["user"], r["app"], r["bytes"]) for r in workspace] == [
        (item, "alice", "rca", 700)
    ]
    assert [(r["collection"], r["user"], r["bytes"]) for r in kb] == [(cid, "alice", 300)]


async def test_without_a_recorder_the_pass_writes_nothing_and_still_reconciles():
    spec = make_spec(default_user="alice")
    store = SpecstarFileStore(spec)
    register_disk_ledger(spec)
    register_sandbox_activity(spec)

    run = make_disk_ledger_pass(spec, store, live_window_ms=1000)
    assert run is not None
    report = await run()

    assert report.failed == 0


async def test_a_disk_ledger_job_through_the_real_queue_writes_the_rows():
    """`build_coordinators` hands the recorder to the pass the blob-gc
    coordinator runs — through its queue, as a worker runs it."""
    from ..test_coordinators import _build, _FakeIngestor

    spec = make_spec(default_user="alice")
    store = SpecstarFileStore(spec)
    register_disk_ledger(spec)
    register_sandbox_activity(spec)
    item = (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="t", owner="alice"))
        .resource_id
    )
    await store.write(item, "/a.bin", b"x" * 50)
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="worker", pod="w", window_s=300, queue_max=100)
    bundle = _build(
        spec,
        _FakeIngestor(),
        filestore=store,
        disk_live_window_ms=1000,
        usage_recorder=recorder,
    )

    bundle.blob_gc.enqueue_disk_ledger()
    bundle.blob_gc.start_consuming()
    await bundle.blob_gc.aclose()
    recorder.close()

    rows = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "disk"]
    assert [(r["item"], r["bytes"]) for r in rows if r["scope"] == "workspace"] == [(item, 50)]
    jobs = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "job"]
    assert [(j["jobtype"], j["outcome"]) for j in jobs] == [("blob-gc", "done")]
