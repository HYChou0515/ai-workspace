"""CardGenRunStore — the CAS join state for the card-gen fan-out (#414).

A card-generation run over N documents is fanned out into N small
``CardGenJob(kind="process")`` jobs (one per doc) so they parallelise across
worker pods. This store is how those independent jobs agree, race-free and
queue-agnostic (CAS, not partition_key), on "every doc is digested → finalize
exactly once". Mirrors :class:`workspace_app.kb.index_run.IndexRunStore`.
"""

from __future__ import annotations

from specstar.types import PreconditionFailedError

from workspace_app.kb.card_gen import CardGenRun, ProposedCard
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
    assert run.proposals == []


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


def test_set_proposals_replaces_the_run_proposals():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    store.set_proposals(run_id, [ProposedCard(keys=["RZ3"], title="t")])
    (p,) = _get(store, run_id).proposals
    assert p.keys == ["RZ3"] and p.title == "t"
    store.set_proposals(run_id, [ProposedCard(keys=["M4"], title="m")])  # wholesale replace
    (p,) = _get(store, run_id).proposals
    assert p.keys == ["M4"]


def test_set_proposals_assigns_stable_ids_to_proposals_missing_them():
    """#481: every proposal needs a stable id so the review table can address one
    card at a time. ``set_proposals`` fills a blank id with the card's position;
    an id already present is preserved (so a re-saved list keeps its identities)."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    store.set_proposals(run_id, [ProposedCard(keys=["A"]), ProposedCard(id="keep", keys=["B"])])
    a, b = _get(store, run_id).proposals
    assert a.id == "0"  # blank → positional id
    assert b.id == "keep"  # existing id preserved


def _done_run(store: CardGenRunStore, cid: str, proposals: list[ProposedCard]) -> str:
    """A finalized (``done``) run carrying ``proposals`` — the state a run is in
    while it sits in the 待審核 queue."""
    run_id = store.start(cid, ["d1"])
    store.set_proposals(run_id, proposals)
    store.finish(run_id, status="done")
    return run_id


def test_decide_sets_one_proposals_decision_by_id():
    """#481 inline accept/reject: ``decide`` flips exactly the addressed card's
    decision and leaves its siblings — and the list order — untouched."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"]), ProposedCard(keys=["B"])])
    store.decide(run_id, "1", "accepted")
    a, b = _get(store, run_id).proposals
    assert (a.id, a.decision) == ("0", "pending")
    assert (b.id, b.decision) == ("1", "accepted")


def test_deciding_the_last_active_card_rejected_settles_the_run_out_of_the_queue():
    """A run drops out of the queue once no proposal is active. Rejecting the last
    undecided card with none committed resolves the run ``dismissed`` (#481)."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"])])
    store.decide(run_id, "0", "rejected")
    assert _get(store, run_id).status == "dismissed"


def test_update_proposal_replaces_a_card_by_id_keeping_its_identity():
    """#481 drawer edit: ``update_proposal`` swaps in the reviewer's edited card
    (new body + decision) for the matching id, and the id stays authoritative."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"], body="old")])
    store.update_proposal(run_id, "0", ProposedCard(keys=["A"], body="new", decision="accepted"))
    (p,) = _get(store, run_id).proposals
    assert (p.id, p.body, p.decision) == ("0", "new", "accepted")


def test_deciding_an_unknown_card_is_a_noop():
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"])])
    assert store.decide(run_id, "nope", "accepted") is None
    assert _get(store, run_id).proposals[0].decision == "pending"


def test_mark_proposals_committed_transitions_active_cards_and_settles_when_done():
    """Committing the referenced active cards flips them ``committed``; when the
    run has no active proposal left it resolves ``committed`` (some card written)
    and drops out of the queue (#481)."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"]), ProposedCard(keys=["B"])])
    store.decide(run_id, "1", "rejected")  # B rejected
    store.mark_proposals_committed(run_id, ["0"])  # A written → committed
    run = _get(store, run_id)
    assert run.status == "committed"  # A committed, B rejected → all terminal
    assert [p.decision for p in run.proposals] == ["committed", "rejected"]


def test_partial_commit_leaves_the_run_in_the_queue():
    """Committing some of a run's cards leaves the rest in the queue (#481): the
    run stays ``done`` while any proposal is still active."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"]), ProposedCard(keys=["B"])])
    store.mark_proposals_committed(run_id, ["0"])  # only A
    run = _get(store, run_id)
    assert run.status == "done"  # B still pending → run stays
    assert [p.decision for p in run.proposals] == ["committed", "pending"]


def test_committing_a_rejected_card_ref_is_skipped():
    """A ref to a non-active card (already rejected/committed) doesn't transition —
    ``mark_proposals_committed`` only advances active proposals (#481)."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = _done_run(store, cid, [ProposedCard(keys=["A"], decision="rejected")])
    assert store.mark_proposals_committed(run_id, ["0"]) is None  # nothing to advance


def test_decide_backfills_ids_on_a_legacy_run():
    """A run finalized before ids existed (blank ids) is still addressable: the
    positional id is backfilled on the first decide, then persists (#481)."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    # a run written the pre-#481 way — proposals with blank ids
    store._cas(  # noqa: SLF001
        run_id,
        lambda run: __import__("msgspec").structs.replace(
            run, proposals=[ProposedCard(keys=["A"]), ProposedCard(keys=["B"])], status="done"
        ),
    )
    store.decide(run_id, "1", "accepted")
    a, b = _get(store, run_id).proposals
    assert a.id == "0" and b.id == "1"
    assert b.decision == "accepted"


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
    store.set_proposals("gone", [ProposedCard(keys=["X"])])
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


def test_runs_by_status_scopes_to_one_collection_via_the_index():
    """P1: the per-collection review path queries CardGenRun by the indexed
    ``(collection_id, status)`` — only that collection's runs surface, so the inbox
    no longer scans every collection's runs to serve one collection's 待審核 tab."""
    spec = make_spec(default_user="u")
    store = CardGenRunStore(spec)
    r1 = _done_run(store, "c1", [ProposedCard(keys=["A"])])
    _done_run(store, "c2", [ProposedCard(keys=["B"])])
    scoped = store.runs_by_status(["done"], collection_id="c1")
    assert [rid for rid, _created, _run in scoped] == [r1]
    assert all(run.collection_id == "c1" for _rid, _created, run in scoped)


def _empty_done_run(store: CardGenRunStore, cid: str) -> str:
    """A finalized run with 0 proposals stuck in ``done`` — the pre-#506 backlog the
    drain has to clear (a run that digested to nothing but was stamped ``done``)."""
    run_id = store.start(cid, ["d1"])
    store.finish(run_id, status="done")
    return run_id


def test_sweep_empty_runs_drains_an_empty_done_run_and_leaves_real_ones():
    """#506 flat inbox: an already-finalized run with 0 proposals has nothing to
    review, so the drain re-stamps it ``empty`` (out of the 待審核 queue); a ``done`` run
    that DOES carry proposals is a real pending item and is left untouched."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    empty = _empty_done_run(store, cid)
    real = _done_run(store, cid, [ProposedCard(keys=["A"])])

    assert store.sweep_empty_runs() == 1  # only the empty one drained

    assert _get(store, empty).status == "empty"
    assert _get(store, real).status == "done"  # a real pending run is preserved
    assert [rid for rid, _r in store.pending_for_collection(cid)] == [real]  # queue = real only


def test_sweep_empty_runs_is_idempotent():
    """Once the backlog is drained the pass is a no-op — it converges, so the periodic
    sweeper never keeps rewriting the same runs."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    _empty_done_run(store, cid)

    assert store.sweep_empty_runs() == 1
    assert store.sweep_empty_runs() == 0  # nothing left to drain


def test_sweep_empty_runs_scopes_to_one_collection():
    """The drain honours ``collection_id`` (the indexed query), so a per-collection
    maintenance tick only touches its own runs and leaves other collections' backlog."""
    spec = make_spec(default_user="u")
    store = CardGenRunStore(spec)
    a = _empty_done_run(store, "c1")
    b = _empty_done_run(store, "c2")

    assert store.sweep_empty_runs(collection_id="c1") == 1

    assert _get(store, a).status == "empty"
    assert _get(store, b).status == "done"  # c2 untouched by a c1-scoped drain


def test_sweep_empty_runs_honours_limit():
    """``limit`` caps how many runs one pass drains; the rest wait for the next tick."""
    spec, cid = _spec_with_collection()
    store = CardGenRunStore(spec)
    _empty_done_run(store, cid)
    _empty_done_run(store, cid)

    assert store.sweep_empty_runs(limit=1) == 1  # one this pass
    assert store.sweep_empty_runs(limit=1) == 1  # the other next pass
    assert store.sweep_empty_runs(limit=1) == 0  # drained
