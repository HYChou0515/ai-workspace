"""CardProposalStore — the first-class CardProposal resource (#511 P1).

Card-gen proposals used to live nested in ``CardGenRun.proposals`` (a msgspec
list field), which can't be queried / sorted / paged as DB rows — so the review
inbox loaded every run into memory and sliced in Python (the #511 "fake
pagination"). P1 extracts each proposal into its own ``CardProposal`` resource,
keyed by the SAME ``prop:{run}:{pid}`` id the reconcile ClusterMember already
uses, so the three review views can page via specstar's native offset/limit.
"""

from __future__ import annotations

import msgspec

from workspace_app.kb.card_gen import ProposedCard
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.kb.card_proposal import CardProposalStore
from workspace_app.resources import Collection, make_spec


def _spec_with_collection():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    return spec, cid


def _run(spec, cid, docs=("d1",)):
    return CardGenRunStore(spec).start(cid, list(docs))


def test_create_from_proposal_uses_the_prop_run_pid_id_and_round_trips():
    """A kept proposal becomes a CardProposal row addressed by the deterministic
    ``prop:{run}:{pid}`` id (aligned with the reconcile ClusterMember), carrying
    the proposal's authoritative content + review decision."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)

    p = ProposedCard(keys=["k1", "k2"], id="c1", title="T", body="B", decision="pending")
    pid = store.create_from_proposal(cid, run_id, p)

    assert pid == f"prop:{run_id}:c1"
    got = store.get(pid)
    assert got is not None
    assert got.collection_id == cid
    assert got.run_id == run_id
    assert got.keys == ["k1", "k2"]
    assert got.title == "T"
    assert got.body == "B"
    assert got.decision == "pending"


def test_list_active_pages_the_collection_at_the_db_and_excludes_terminal():
    """The 待審核 flat view pages ACTIVE (pending/accepted) proposals via native
    offset/limit — a real DB query, not load-all-then-slice — newest first, and a
    TERMINAL (rejected/committed) proposal drops out of the queue."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)

    for i in range(5):
        store.create_from_proposal(
            cid, run_id, ProposedCard(keys=[f"k{i}"], id=f"c{i}", decision="pending")
        )
    # a resolved proposal must not show in the active queue
    store.create_from_proposal(
        cid, run_id, ProposedCard(keys=["kx"], id="cx", decision="rejected")
    )

    assert store.count_active(cid) == 5

    page1 = store.list_active(cid, offset=0, limit=2)
    page2 = store.list_active(cid, offset=2, limit=2)
    page3 = store.list_active(cid, offset=4, limit=2)
    assert [len(p) for p in (page1, page2, page3)] == [2, 2, 1]

    ids = [pid for page in (page1, page2, page3) for pid, _cp in page]
    assert len(set(ids)) == 5  # no overlap / gap across pages
    assert f"prop:{run_id}:cx" not in ids  # terminal excluded
    assert all(
        cp.decision == "pending" for page in (page1, page2, page3) for _pid, cp in page
    )


def test_create_from_proposal_is_idempotent_and_preserves_existing():
    """A re-driven finalize (at-least-once redelivery) re-creates the same
    proposal id — that must be a no-op, never an error or a duplicate, and it must
    NOT clobber the existing row (a reviewer's decision, once P2 lands, is safe)."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)

    p = ProposedCard(keys=["k"], id="c1", title="", decision="pending")
    pid1 = store.create_from_proposal(cid, run_id, p)
    # redelivered finalize: same id, different content — first write wins
    pid2 = store.create_from_proposal(
        cid, run_id, msgspec.structs.replace(p, title="RE-RUN")
    )

    assert pid1 == pid2
    assert store.count_active(cid) == 1  # not duplicated
    got = store.get(pid1)
    assert got is not None
    assert got.title == ""  # original preserved, not the re-run's "RE-RUN"


def test_get_returns_none_for_a_missing_proposal():
    """A read for an id that was never projected (or cascaded away) is a clean
    ``None`` — the callers treat absence as "fall back to the nested list" (P1)."""
    spec, _cid = _spec_with_collection()
    assert CardProposalStore(spec).get("prop:nope:0") is None


def test_set_decision_on_a_missing_proposal_is_a_noop():
    """A per-proposal write to an id that was never projected (or cascaded away) is
    a clean no-op, never a raise — the CAS read just finds no row."""
    spec, _cid = _spec_with_collection()
    assert CardProposalStore(spec).set_decision("prop:nope:0", "accepted") is None


def test_mark_committed_skips_an_already_terminal_ref():
    """A commit ref to a proposal that's already terminal advances nothing (the
    per-proposal CAS mutate returns None) — idempotent, so a redelivered commit
    writes no second transition."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)
    pid = store.create_from_proposal(
        cid, run_id, ProposedCard(keys=["k"], id="0", decision="rejected")
    )
    store.mark_committed([pid])  # no-op: already terminal
    got = store.get(pid)
    assert got is not None and got.decision == "rejected"


def test_active_runs_counts_multiple_active_proposals_per_run():
    """A run with several active proposals is ONE queue row carrying the active
    count (the queue groups a run's proposals)."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)
    for i in range(3):
        store.create_from_proposal(cid, run_id, ProposedCard(keys=[f"k{i}"], id=str(i)))
    assert store.active_runs(cid) == [(run_id, 3)]


def test_dismiss_run_leaves_an_already_terminal_proposal_untouched(monkeypatch):
    """Whole-run dismiss rejects only ACTIVE proposals; a ref that resolved after
    the active query (the resolve-after-query race) is left as it was."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)
    pid = store.create_from_proposal(
        cid, run_id, ProposedCard(keys=["k"], id="0", decision="committed")
    )
    # force the terminal id into the "active" set to simulate the race
    monkeypatch.setattr(store, "_active_proposal_ids_of_run", lambda _rid: [pid])
    assert store.dismiss_run(run_id) == 0  # committed proposal not flipped
    got = store.get(pid)
    assert got is not None and got.decision == "committed"


def test_cas_retries_when_a_concurrent_writer_wins_the_race(monkeypatch):
    """A losing per-proposal CAS write (etag moved on under us) re-reads and retries
    rather than dropping the reviewer's decision."""
    from specstar.types import PreconditionFailedError

    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    store = CardProposalStore(spec)
    pid = store.create_from_proposal(cid, run_id, ProposedCard(keys=["k"], id="0"))
    real_modify = store._rm.modify  # noqa: SLF001
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first attempt loses the race
            raise PreconditionFailedError(pid, "expected", "actual")
        return real_modify(*args, **kwargs)

    monkeypatch.setattr(store._rm, "modify", flaky)  # noqa: SLF001
    store.set_decision(pid, "accepted")
    assert calls["n"] == 2  # retried once, then succeeded
    got = store.get(pid)
    assert got is not None and got.decision == "accepted"


def test_count_active_is_scoped_per_collection():
    """A collection's pager total counts only its own active proposals."""
    spec, cid_a = _spec_with_collection()
    from workspace_app.resources import Collection

    cid_b = spec.get_resource_manager(Collection).create(Collection(name="b")).resource_id
    run_a, run_b = _run(spec, cid_a), _run(spec, cid_b)
    store = CardProposalStore(spec)
    store.create_from_proposal(cid_a, run_a, ProposedCard(keys=["k"], id="a1"))
    store.create_from_proposal(cid_b, run_b, ProposedCard(keys=["k"], id="b1"))
    store.create_from_proposal(cid_b, run_b, ProposedCard(keys=["k"], id="b2"))
    assert store.count_active(cid_a) == 1
    assert store.count_active(cid_b) == 2
