"""#506 P7 — the review inbox's cluster projection.

The flat proposals + questions are grouped by their reconcile ``cluster_key`` so a
card proposal and a question about the SAME concept collapse to ONE row (⑤). An
item with no cluster member (pre-P6 backlog, or a build with no embedder) falls
back to its own singleton cluster, so nothing ever vanishes from review.
"""

from __future__ import annotations

from workspace_app.kb.card_gen import ProposedCard
from workspace_app.kb.review_inbox import (
    ReviewCardItem,
    ReviewQuestionItem,
    cluster_key_map,
    group_by_cluster,
)
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import ClusterMember, DocQuestion


def _member(spec, cid, *, kind, ref_id, run_id="", cluster_key="", state="active") -> None:
    spec.get_resource_manager(ClusterMember).create(
        ClusterMember(
            collection_id=cid,
            kind=kind,
            ref_id=ref_id,
            run_id=run_id,
            cluster_key=cluster_key,
            state=state,
        )
    )


def _card(
    run_id: str, cid: str, card_id: str, *, title: str = "", t: float = 0.0
) -> ReviewCardItem:
    return ReviewCardItem(
        run_id=run_id,
        collection_id=cid,
        collection_name="C",
        can_act=True,
        created_time=t,
        card=ProposedCard(id=card_id, keys=[title or card_id], title=title),
    )


def _question(qid: str, cid: str, *, term: str = "", t: float = 0.0) -> ReviewQuestionItem:
    return ReviewQuestionItem(
        qid=qid,
        collection_id=cid,
        collection_name="C",
        can_act=True,
        created_time=t,
        question=DocQuestion(collection_id=cid, kind="term", term=term or qid),
    )


def test_group_by_cluster_merges_a_same_key_card_and_question() -> None:
    """Tracer: a proposal and a question sharing a cluster_key become ONE cluster row
    carrying both, keyed by the cluster and stamped with its newest member time."""
    card = _card("r1", "c", "0", title="Alpha", t=10.0)
    q = _question("q1", "c", term="alpha-syn", t=20.0)
    cluster_of = {("r1", "0"): "alpha", ("", "q1"): "alpha"}

    clusters = group_by_cluster([card, q], cluster_of)

    assert len(clusters) == 1
    cl = clusters[0]
    assert cl.cluster_key == "alpha"
    assert cl.collection_id == "c"
    assert len(cl.cards) == 1 and len(cl.questions) == 1
    assert cl.size == 2
    assert cl.created_time == 20.0  # newest member wins the sort key


def test_group_by_cluster_falls_back_to_singletons_for_unclustered_items() -> None:
    """An item with no cluster member (pre-P6 backlog) becomes its own row, so it
    never disappears from review."""
    a = _card("r1", "c", "0", title="Alpha", t=10.0)
    b = _card("r2", "c", "0", title="Beta", t=20.0)  # same card id, different run
    clusters = group_by_cluster([a, b], {})  # empty map → both singletons

    assert len(clusters) == 2  # not merged despite the shared card id "0"
    assert all(cl.size == 1 for cl in clusters)


def test_group_by_cluster_orders_newest_cluster_first() -> None:
    """Clusters come back newest-member-first, matching the flat inbox order."""
    old = _card("r1", "c", "0", title="Old", t=5.0)
    new = _question("q1", "c", term="New", t=50.0)
    clusters = group_by_cluster([old, new], {("r1", "0"): "old", ("", "q1"): "new"})

    assert [cl.cluster_key for cl in clusters] == ["new", "old"]


def test_group_by_cluster_can_act_is_true_if_any_member_is_actionable() -> None:
    """A cluster is actionable if the actor may write to ANY member's collection."""
    ro = _card("r1", "c", "0", title="RO", t=1.0)
    ro.can_act = False
    rw = _question("q1", "c", term="RW", t=2.0)
    rw.can_act = True
    [cluster] = group_by_cluster([ro, rw], {("r1", "0"): "k", ("", "q1"): "k"})
    assert cluster.can_act is True


def test_cluster_key_map_joins_active_members_by_run_and_ref() -> None:
    """The join primitive: active proposal + term_question members map their
    (run_id, ref_id) to their cluster_key; card members + suppressed members are
    excluded (only inbox-visible items participate)."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    _member(spec, cid, kind="proposal", ref_id="0", run_id="r1", cluster_key="alpha")
    _member(spec, cid, kind="term_question", ref_id="q1", run_id="", cluster_key="alpha")
    _member(spec, cid, kind="card", ref_id="card1", cluster_key="alpha")  # not an inbox item
    _member(
        spec, cid, kind="proposal", ref_id="9", run_id="r2", cluster_key="beta", state="suppressed"
    )

    m = cluster_key_map(spec, [cid])

    assert m[("r1", "0")] == "alpha"
    assert m[("", "q1")] == "alpha"
    assert ("r2", "9") not in m  # suppressed excluded
    assert not any(k[1] == "card1" for k in m)  # card members excluded
