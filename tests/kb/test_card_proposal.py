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
from workspace_app.kb.card_proposal import CardProposalStore, backfill_card_proposals
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


def test_backfill_projects_existing_nested_proposals_preserving_decision():
    """The one-time #511 migration: a run finalized BEFORE P1 has proposals ONLY in
    the nested ``CardGenRun.proposals`` list (no CardProposal rows). ``backfill_card_
    proposals`` projects each into a first-class row addressed ``prop:{run}:{pid}``,
    preserving its review ``decision`` (a terminal one is projected too, just kept
    out of the active queue), and is idempotent — a re-run creates nothing."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    # simulate a pre-#511 finalize: nested proposals written, no CardProposal rows
    CardGenRunStore(spec).set_proposals(
        run_id,
        [
            ProposedCard(keys=["k0"], title="A", decision="pending"),
            ProposedCard(keys=["k1"], title="B", decision="accepted"),
            ProposedCard(keys=["k2"], title="C", decision="rejected"),
        ],
    )
    store = CardProposalStore(spec)
    assert store.count_active(cid) == 0  # nothing projected yet

    assert backfill_card_proposals(spec) == 3
    assert store.count_active(cid) == 2  # pending + accepted; rejected excluded
    rejected = store.get(f"prop:{run_id}:2")
    assert rejected is not None and rejected.decision == "rejected"
    # idempotent — a second pass over a converged store creates nothing
    assert backfill_card_proposals(spec) == 0


def test_get_returns_none_for_a_missing_proposal():
    """A read for an id that was never projected (or cascaded away) is a clean
    ``None`` — the callers treat absence as "fall back to the nested list" (P1)."""
    spec, _cid = _spec_with_collection()
    assert CardProposalStore(spec).get("prop:nope:0") is None


def test_backfill_respects_the_per_call_limit():
    """One backfill pass creates at most ``limit`` rows so a huge pre-#511 backlog
    can't stall a sweep tick; the remainder is picked up on the next pass."""
    spec, cid = _spec_with_collection()
    run_id = _run(spec, cid)
    CardGenRunStore(spec).set_proposals(
        run_id, [ProposedCard(keys=[f"k{i}"], title=str(i)) for i in range(5)]
    )
    store = CardProposalStore(spec)

    assert backfill_card_proposals(spec, limit=2) == 2  # capped this pass
    assert store.count_active(cid) == 2
    assert backfill_card_proposals(spec, limit=2) == 2  # next slice
    assert backfill_card_proposals(spec, limit=2) == 1  # tail
    assert store.count_active(cid) == 5
    assert backfill_card_proposals(spec, limit=2) == 0  # converged


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
