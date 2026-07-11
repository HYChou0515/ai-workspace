"""#506 P8 — the background cluster sweeper.

Two idempotent maintenance passes over a collection's ClusterMembers:

  - **backfill**: pending proposals / open questions written BEFORE P6 (or by a
    build with no embedder) have no cluster member, so the grouped inbox shows them
    as singletons. Backfill embeds + clusters them so they join their concept.
  - **merge**: two clusters whose centroids are within τ (a parallel-race split the
    same concept into two keys) are union-found into one.
"""

from __future__ import annotations

from specstar import QB

from workspace_app.kb.card_gen import ProposedCard
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.kb.doc_questions import add_description_question, open_or_merge_term_question
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.reconcile import backfill_collection, merge_near_clusters, sweep_clusters
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _done_run(spec, cid: str, proposals: list[ProposedCard]) -> str:
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    store.set_proposals(run_id, proposals)
    store.finish(run_id, status="done")
    return run_id


def _members(spec, cid: str) -> list[ClusterMember]:
    rm = spec.get_resource_manager(ClusterMember)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ClusterMember)
        out.append(r.data)
    return out


def _onehot(i: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[i] = 1.0
    return v


def _member(spec, cid: str, member_id: str, *, cluster_key: str, vec: list[float] | None) -> None:
    """Write a ClusterMember with a controlled embedding + cluster_key so a merge test
    can build two clusters whose centroids are (or aren't) near without an embedder."""
    spec.get_resource_manager(ClusterMember).create_or_update(
        member_id,
        ClusterMember(
            collection_id=cid,
            kind="proposal",
            ref_id=member_id,
            cluster_key=cluster_key,
            norm_key=cluster_key,
            embedding=vec,
        ),
    )


def test_backfill_projects_a_pending_proposal_that_has_no_member() -> None:
    """A pre-P6 proposal (a done run, but no ClusterMember) gets an active member
    with a cluster_key so the grouped inbox can cluster it; the pass is idempotent."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    run_id = _done_run(spec, cid, [ProposedCard(id="0", keys=["RZ3"], title="RZ3")])
    emb = HashEmbedder(dim=EMBED_DIM)

    n = backfill_collection(spec, emb, cid, cluster_tau=0.9)

    assert n == 1
    (m,) = [x for x in _members(spec, cid) if x.kind == "proposal"]
    assert m.ref_id == "0"
    assert m.run_id == run_id
    assert m.state == "active"
    assert m.cluster_key  # assigned
    # idempotent — a second pass re-projects nothing (the member already exists).
    assert backfill_collection(spec, emb, cid, cluster_tau=0.9) == 0


def test_backfill_projects_open_term_questions() -> None:
    """An open term question with no member is backfilled too (⑤ groups questions
    with proposals)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="Widget", source_doc_id="d1", question_text="What?"
    )
    emb = HashEmbedder(dim=EMBED_DIM)

    n = backfill_collection(spec, emb, cid, cluster_tau=0.9)

    assert n == 1
    (m,) = [x for x in _members(spec, cid) if x.kind == "term_question"]
    assert m.ref_id == qid
    assert m.cluster_key == "widget"  # norm(Widget)


def test_merge_folds_near_clusters_onto_the_larger_key() -> None:
    """Two cluster_keys whose centroids are within τ (a parallel-race split) are
    union-found into one; the key carrying the most members wins, and every member is
    rewritten to it. The pass is idempotent."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    # "zeta" (2 members) and "alpha" (1 member) share one centroid direction → merge.
    _member(spec, cid, "z1", cluster_key="zeta", vec=_onehot(0))
    _member(spec, cid, "z2", cluster_key="zeta", vec=_onehot(0))
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))

    absorbed = merge_near_clusters(spec, cid, merge_tau=0.99)

    assert absorbed == 1  # "alpha" folded into "zeta"
    keys = {m.cluster_key for m in _members(spec, cid)}
    assert keys == {"zeta"}  # larger cluster's key wins despite "alpha" < "zeta"
    # idempotent — nothing left to merge.
    assert merge_near_clusters(spec, cid, merge_tau=0.99) == 0


def test_merge_leaves_distant_clusters_apart() -> None:
    """Clusters whose centroids are below τ stay separate (no over-merge)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))
    _member(spec, cid, "b1", cluster_key="beta", vec=_onehot(1))  # orthogonal → sim 0

    absorbed = merge_near_clusters(spec, cid, merge_tau=0.9)

    assert absorbed == 0
    assert {m.cluster_key for m in _members(spec, cid)} == {"alpha", "beta"}


def test_sweep_backfills_and_merges_across_every_collection() -> None:
    """One sweep runs backfill + merge over EVERY collection — a pending proposal in
    each is projected, and a race-split pair is folded — so the periodic API sweeper
    heals the whole store in one tick."""
    spec = make_spec(default_user="u")
    c1 = _collection(spec, "c1")
    c2 = _collection(spec, "c2")
    _done_run(spec, c1, [ProposedCard(id="0", keys=["A"], title="A")])
    _done_run(spec, c2, [ProposedCard(id="0", keys=["B"], title="B")])
    # c2 additionally carries a race-split pair that merge should fold.
    _member(spec, c2, "z1", cluster_key="zeta", vec=_onehot(0))
    _member(spec, c2, "z2", cluster_key="zeta", vec=_onehot(0))
    _member(spec, c2, "x1", cluster_key="alpha", vec=_onehot(0))
    emb = HashEmbedder(dim=EMBED_DIM)

    report = sweep_clusters(spec, emb, cluster_tau=0.9, merge_tau=0.99)

    assert report.backfilled == 2  # one pending proposal per collection
    assert report.merged == 1  # "alpha" folded into "zeta" in c2
    assert [m.kind for m in _members(spec, c1) if m.kind == "proposal"]
    assert "zeta" in {m.cluster_key for m in _members(spec, c2)}
    assert "alpha" not in {m.cluster_key for m in _members(spec, c2)}


def test_sweep_is_idempotent() -> None:
    """A converged store sweeps to a zero report."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _done_run(spec, cid, [ProposedCard(id="0", keys=["A"], title="A")])
    emb = HashEmbedder(dim=EMBED_DIM)

    sweep_clusters(spec, emb, cluster_tau=0.9, merge_tau=0.99)
    again = sweep_clusters(spec, emb, cluster_tau=0.9, merge_tau=0.99)

    assert again.backfilled == 0
    assert again.merged == 0


def test_backfill_is_batched_by_limit() -> None:
    """One pass projects at most `limit` members; the rest are picked up next pass."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _done_run(
        spec,
        cid,
        [ProposedCard(id="0", keys=["A"], title="A"), ProposedCard(id="1", keys=["B"], title="B")],
    )
    emb = HashEmbedder(dim=EMBED_DIM)

    assert backfill_collection(spec, emb, cid, cluster_tau=0.9, limit=1) == 1
    assert len([m for m in _members(spec, cid) if m.kind == "proposal"]) == 1
    assert backfill_collection(spec, emb, cid, cluster_tau=0.9, limit=1) == 1
    assert len([m for m in _members(spec, cid) if m.kind == "proposal"]) == 2


def test_merge_is_batched_by_limit() -> None:
    """A `limit` caps how many clusters one pass folds; the rest fold next pass."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    # two independent race-split pairs, orthogonal to each other
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))
    _member(spec, cid, "a2", cluster_key="alpha", vec=_onehot(0))
    _member(spec, cid, "a3", cluster_key="alpha2", vec=_onehot(0))
    _member(spec, cid, "g1", cluster_key="gamma", vec=_onehot(1))
    _member(spec, cid, "g2", cluster_key="gamma", vec=_onehot(1))
    _member(spec, cid, "g3", cluster_key="gamma2", vec=_onehot(1))

    assert merge_near_clusters(spec, cid, merge_tau=0.99, limit=1) == 1  # one pair this pass
    assert merge_near_clusters(spec, cid, merge_tau=0.99, limit=1) == 1  # the other next pass
    assert {m.cluster_key for m in _members(spec, cid)} == {"alpha", "gamma"}


def test_sweep_continues_past_a_failing_collection() -> None:
    """A per-collection error (here a transient embed failure) is swallowed so the
    sweep still heals every other collection."""

    class _BoomEmb:
        dim = EMBED_DIM
        identity = "boom"

        def __init__(self) -> None:
            self._h = HashEmbedder(dim=EMBED_DIM)

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            if any("BOOM" in t for t in texts):
                raise RuntimeError("embed exploded")
            return self._h.embed_documents(texts)

        def embed_query(self, text: str) -> list[float]:
            return self._h.embed_query(text)

    spec = make_spec(default_user="u")
    bad = _collection(spec, "bad")
    good = _collection(spec, "good")
    _done_run(spec, bad, [ProposedCard(id="0", keys=["BOOM"], title="BOOM")])
    _done_run(spec, good, [ProposedCard(id="0", keys=["OK"], title="OK")])

    report = sweep_clusters(spec, _BoomEmb(), cluster_tau=0.9, merge_tau=0.99)

    assert report.backfilled == 1  # only the good collection
    assert [m for m in _members(spec, good) if m.kind == "proposal"]
    assert [m for m in _members(spec, bad) if m.kind == "proposal"] == []


def test_backfill_skips_inactive_proposals_and_non_term_questions() -> None:
    """Only ACTIVE proposals + open TERM questions are projected — a resolved proposal
    and a description question are skipped."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _done_run(spec, cid, [ProposedCard(id="0", keys=["A"], title="A", decision="rejected")])
    add_description_question(
        spec, collection_id=cid, source_doc_id="d1", quote="a passage", question_text="?"
    )
    emb = HashEmbedder(dim=EMBED_DIM)

    assert backfill_collection(spec, emb, cid, cluster_tau=0.9) == 0
    assert _members(spec, cid) == []


def test_backfill_batches_term_questions_and_skips_already_projected() -> None:
    """The open-term-question backfill honours `limit` and skips questions that already
    have a member (idempotent)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    open_or_merge_term_question(
        spec, collection_id=cid, term="Widget", source_doc_id="d1", question_text="?"
    )
    open_or_merge_term_question(
        spec, collection_id=cid, term="Gadget", source_doc_id="d1", question_text="?"
    )
    emb = HashEmbedder(dim=EMBED_DIM)

    assert backfill_collection(spec, emb, cid, cluster_tau=0.9, limit=1) == 1  # one this pass
    assert backfill_collection(spec, emb, cid, cluster_tau=0.9, limit=1) == 1  # the other next pass
    assert backfill_collection(spec, emb, cid, cluster_tau=0.9, limit=1) == 0  # both projected
    assert len([m for m in _members(spec, cid) if m.kind == "term_question"]) == 2


def test_merge_ignores_members_without_a_vector() -> None:
    """A member with no cluster_key or no embedding is skipped by the centroid pass
    (it can't participate in a cosine merge)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _member(spec, cid, "ghost", cluster_key="ghost", vec=None)  # vecless → skipped
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))

    assert merge_near_clusters(spec, cid, merge_tau=0.99) == 0
    assert {m.cluster_key for m in _members(spec, cid)} == {"ghost", "alpha"}
