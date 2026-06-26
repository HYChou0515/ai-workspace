"""IndexRunStore — the CAS join state for the index fan-out (#227).

The fan-out splits one big index into many small process jobs; this store is
how they agree, race-free and queue-agnostic (CAS, not partition_key), on
"every batch is done → finalize exactly once". Behaviour pinned here:

  - a started run is `running` with empty done/failed,
  - recording a batch is idempotent (redelivery can't double-count),
  - the finalize gate opens only when done ∪ failed covers every batch, and
    exactly ONE caller wins it (the rest, and the sweep, see it already claimed),
  - a crash before finalize is recoverable: re-evaluating the gate can still win.
"""

from __future__ import annotations

from specstar.types import Binary, PreconditionFailedError

from workspace_app.kb.index_run import IndexRunStore
from workspace_app.resources import Collection, SourceDoc, make_spec


def _spec_with_doc():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    doc_id = (
        spec.get_resource_manager(SourceDoc)
        .create(
            SourceDoc(collection_id=cid, path="a.pdf", content=Binary(data=b"x"), status="indexing")
        )
        .resource_id
    )
    return spec, cid, doc_id


def test_start_seeds_a_running_run():
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=3)
    run = store.get(doc_id)
    assert run is not None
    assert (run.total, run.done, run.failed, run.finalized, run.status) == (
        3,
        [],
        [],
        False,
        "running",
    )
    assert store.is_active(doc_id) is True


def _units(store: IndexRunStore, doc_id: str) -> tuple[int, int]:
    run = store.get(doc_id)
    assert run is not None
    return run.units_done, run.units_total


def test_units_done_climbs_monotonically_as_batches_complete():
    """#248: the progress bar reads a real aggregate (units done across all
    batches / total units), seeded at start and bumped per completed batch — so
    it only ever moves forward, even as parallel batches finish out of order."""
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=3, units_total=24)  # 24-page PDF, 3 batches of 8
    assert _units(store, doc_id) == (0, 24)

    store.mark_done(doc_id, 0, batch_units=8)
    assert _units(store, doc_id) == (8, 24)
    store.mark_done(doc_id, 2, batch_units=8)  # out of order — still climbs
    assert _units(store, doc_id) == (16, 24)
    store.mark_done(doc_id, 0, batch_units=8)  # redelivery — idempotent, no double count
    assert _units(store, doc_id) == (16, 24)


def test_get_missing_run_is_none():
    spec, _cid, doc_id = _spec_with_doc()
    assert IndexRunStore(spec).get(doc_id) is None


def test_cas_on_a_vanished_run_is_a_noop():
    """A process job whose run was deleted mid-flight (doc removed) records
    nothing and does not raise — the CAS read just finds no run."""
    spec, _cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.mark_done(doc_id, 0)  # no run started → no-op
    store.mark_failed(doc_id, 0)
    assert store.claim_finalize(doc_id) is False
    assert store.get(doc_id) is None


def test_mark_done_is_idempotent():
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=3)
    store.mark_done(doc_id, 1)
    store.mark_done(doc_id, 1)  # redelivery of the same process job
    store.mark_done(doc_id, 0)
    run = store.get(doc_id)
    assert run is not None
    assert sorted(run.done) == [0, 1]  # 1 recorded once, not twice


def test_mark_failed_is_idempotent():
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=3)
    store.mark_failed(doc_id, 2)
    store.mark_failed(doc_id, 2)  # redelivery of the same dead-lettered batch
    run = store.get(doc_id)
    assert run is not None
    assert run.failed == [2]  # recorded once, not twice


def test_finalize_gate_opens_only_when_all_batches_accounted_for():
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=3)
    store.mark_done(doc_id, 0)
    store.mark_done(doc_id, 1)
    assert store.claim_finalize(doc_id) is False  # 2/3 — not yet
    store.mark_failed(doc_id, 2)  # the last batch gave up
    assert store.claim_finalize(doc_id) is True  # done ∪ failed == total


def test_finalize_is_claimed_exactly_once():
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=1)
    store.mark_done(doc_id, 0)
    # Two finishers (and later the sweep) all race the gate — exactly one wins.
    results = [
        store.claim_finalize(doc_id),
        store.claim_finalize(doc_id),
        store.claim_finalize(doc_id),
    ]
    assert results.count(True) == 1


def test_cas_retries_when_a_concurrent_writer_wins_the_race(monkeypatch):
    """A losing CAS write (etag moved on under us) re-reads and retries rather
    than dropping the update — the core of the queue-agnostic join."""
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=2)
    real_modify = store._rm.modify  # noqa: SLF001
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first attempt loses the race
            raise PreconditionFailedError(doc_id, "expected", "actual")
        return real_modify(*args, **kwargs)

    monkeypatch.setattr(store._rm, "modify", flaky)  # noqa: SLF001
    store.mark_done(doc_id, 0)
    assert calls["n"] == 2  # retried once, then succeeded
    run = store.get(doc_id)
    assert run is not None and run.done == [0]


def test_finish_sets_terminal_status_and_clears_active():
    spec, cid, doc_id = _spec_with_doc()
    store = IndexRunStore(spec)
    store.start(doc_id, cid, total=1)
    store.finish(doc_id, status="done")
    run = store.get(doc_id)
    assert run is not None and run.status == "done"
    assert store.is_active(doc_id) is False
