"""CardGenRunStore — the CAS join state for the card-gen fan-out (#414).

A card-generation run over N documents is fanned out into N small
``CardGenJob(kind="process")`` jobs (one per doc) so they parallelise across
worker pods. This store is how those independent jobs agree, race-free and
queue-agnostic (CAS, not partition_key), on "every doc is digested → finalize
exactly once". Mirrors :class:`workspace_app.kb.index_run.IndexRunStore`.
"""

from __future__ import annotations

from specstar.types import PreconditionFailedError

from workspace_app.kb.card_gen import CardGenRun
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.resources import Collection, make_spec


def _spec_with_collection():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    return spec, cid


def _get(store: CardGenRunStore, run_id: str) -> CardGenRun:
    """The run, asserted present (narrows ``CardGenRun | None`` for ty)."""
    run = store.get(run_id)
    assert run is not None
    return run


def test_start_seeds_a_pending_run_and_returns_its_id():
    """A fresh run is ``pending`` (the FE polls PENDING until a consumer picks up
    the split job) with the ordered doc set and its total, no batches recorded."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1", "d2", "d3"])
    run = store.get(run_id)
    assert run is not None
    assert run.collection_id == cid
    assert run.doc_ids == ["d1", "d2", "d3"]
    assert (run.total, run.done, run.failed, run.finalized, run.status) == (
        3,
        [],
        [],
        False,
        "pending",
    )


def test_get_missing_run_is_none():
    spec, _cid = _spec_with_collection()
    assert CardGenRunStore(spec).get("nope") is None


def test_begin_flips_pending_to_running_and_is_idempotent():
    """The split job flips the run to running (PROCESSING) when it picks it up; a
    redelivered split leaves an already-running run untouched (no progress reset)."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1", "d2"])
    store.begin(run_id)
    assert _get(store, run_id).status == "running"
    store.mark_done(run_id, 0)
    store.begin(run_id)  # redelivered split — must not reset
    run = _get(store, run_id)
    assert run.status == "running"
    assert run.done == [0]


def test_mark_done_is_idempotent():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1", "d2", "d3"])
    store.mark_done(run_id, 1)
    store.mark_done(run_id, 1)  # redelivery of the same process job
    store.mark_done(run_id, 0)
    run = _get(store, run_id)
    assert sorted(run.done) == [0, 1]  # 1 recorded once, not twice


def test_mark_failed_is_idempotent():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1", "d2", "d3"])
    store.mark_failed(run_id, 2)
    store.mark_failed(run_id, 2)  # redelivery of the same dead-lettered doc
    assert _get(store, run_id).failed == [2]  # recorded once, not twice


def test_finalize_gate_opens_only_when_all_docs_accounted_for():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1", "d2", "d3"])
    store.mark_done(run_id, 0)
    store.mark_done(run_id, 1)
    assert store.claim_finalize(run_id) is False  # 2/3 — not yet
    store.mark_failed(run_id, 2)  # the last doc gave up
    assert store.claim_finalize(run_id) is True  # done ∪ failed == total


def test_finalize_is_claimed_exactly_once():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    store.mark_done(run_id, 0)
    results = [store.claim_finalize(run_id) for _ in range(3)]  # racing finishers
    assert results.count(True) == 1


def test_finish_sets_terminal_status():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    store.finish(run_id, status="done")
    assert _get(store, run_id).status == "done"


def test_cas_on_a_vanished_run_is_a_noop():
    """A process job whose run was deleted mid-flight (collection removed) records
    nothing and does not raise — the CAS read just finds no run."""
    spec, _cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    store.mark_done("gone", 0)  # no run → no-op
    store.mark_failed("gone", 0)
    store.begin("gone")
    assert store.claim_finalize("gone") is False
    assert store.get("gone") is None


def test_cas_retries_when_a_concurrent_writer_wins_the_race(monkeypatch):
    """A losing CAS write (etag moved on under us) re-reads and retries rather
    than dropping the update — the core of the queue-agnostic join."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1", "d2"])
    real_modify = store._rm.modify  # noqa: SLF001
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first attempt loses the race
            raise PreconditionFailedError(run_id, "expected", "actual")
        return real_modify(*args, **kwargs)

    monkeypatch.setattr(store._rm, "modify", flaky)  # noqa: SLF001
    store.mark_done(run_id, 0)
    assert calls["n"] == 2  # retried once, then succeeded
    assert _get(store, run_id).done == [0]
