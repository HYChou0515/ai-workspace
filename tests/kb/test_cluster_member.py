"""ClusterMember access layer (#511 P4) — native, paginated GROUP-BY over the
reconcile projection table, and the decision-driven state de-join.

These exercise ``page_clusters`` / ``count_clusters`` directly on seeded
ClusterMember rows (no proposal/question resolution — that's the review inbox's
job), so the aggregation contract is tested in isolation: one page per concept,
newest concept first, ACTIVE-only, distinct total independent of the page window.
"""

from __future__ import annotations

from workspace_app.kb.cluster_member import count_clusters, page_clusters
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember


def _spec_with_collection(name="c"):
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id
    return spec, cid


def _member(
    spec,
    cid,
    cluster_key,
    *,
    kind="proposal",
    ref="r",
    state="active",
    member_id=None,
    embedding=None,
):
    rm = spec.get_resource_manager(ClusterMember)
    m = ClusterMember(
        collection_id=cid,
        kind=kind,
        ref_id=ref,
        cluster_key=cluster_key,
        state=state,
        norm_key=cluster_key,
        embedding=embedding,
    )
    if member_id is None:
        return rm.create(m).resource_id
    rm.create_or_update(member_id, m)
    return member_id


def test_page_clusters_pages_concepts_by_recency_with_distinct_total():
    """One row per ``cluster_key``, newest concept first, paged natively; the total
    is the distinct-cluster count (independent of the page window); a multi-member
    concept is ONE row carrying both members."""
    spec, cid = _spec_with_collection()
    for ck in ["c0", "c1", "c2", "c3"]:
        _member(spec, cid, ck)
    _member(spec, cid, "c3")  # c3 gets a 2nd, newer member → still one concept row

    p1, total = page_clusters(spec, [cid], offset=0, limit=2)
    p2, _ = page_clusters(spec, [cid], offset=2, limit=2)
    assert total == 4
    assert [len(p) for p in (p1, p2)] == [2, 2]
    # newest concept first: c3 (has the newest member) leads
    assert p1[0][0] == "c3"
    assert len(p1[0][2]) == 2  # c3's two members grouped under one concept
    seen = {key for page in (p1, p2) for key, _latest, _members in page}
    assert seen == {"c0", "c1", "c2", "c3"}  # no overlap / gap


def test_page_clusters_never_loads_the_embedding_vector():
    """A page's members come back WITHOUT their ``embedding`` (#508). The 1024-dim
    vector is reconcile's nearest-neighbour input — the review inbox reads a member's
    scalars only — so the page projects every field EXCEPT the vector. Deserializing
    one vector per pending member is what made the grouped inbox take 60s+; this is
    the guard that the projection stays in place."""
    spec, cid = _spec_with_collection()
    _member(spec, cid, "k", embedding=[0.5] * EMBED_DIM)

    (page,), _total = page_clusters(spec, [cid])
    _key, _latest, members = page
    _member_id, _epoch, member = members[0]
    assert not hasattr(member, "embedding")  # projected out — never left the DB
    assert (member.cluster_key, member.kind) == ("k", "proposal")  # scalars still there


def test_page_clusters_filters_by_state_and_excludes_resolved():
    """Only members in the requested ``state`` count — a resolved (inactive) concept
    drops out of the active page + total (the whole point of the P4 state sync)."""
    spec, cid = _spec_with_collection()
    _member(spec, cid, "live")
    _member(spec, cid, "done", state="inactive")

    active, active_total = page_clusters(spec, [cid])
    hist, hist_total = page_clusters(spec, [cid], state="inactive")
    assert active_total == 1 and [k for k, _, _ in active] == ["live"]
    assert hist_total == 1 and [k for k, _, _ in hist] == ["done"]


def test_page_clusters_filters_by_kind():
    """``kinds`` narrows which member kinds form concepts — the grouped view's type
    filter (cards-only vs questions-only)."""
    spec, cid = _spec_with_collection()
    _member(spec, cid, "k1", kind="proposal")
    _member(spec, cid, "k2", kind="term_question")

    cards_only, ct = page_clusters(spec, [cid], kinds=["proposal"])
    qs_only, qt = page_clusters(spec, [cid], kinds=["term_question"])
    assert ct == 1 and [k for k, _, _ in cards_only] == ["k1"]
    assert qt == 1 and [k for k, _, _ in qs_only] == ["k2"]


def test_count_clusters_scopes_to_state_and_collections():
    """The pager/badge total counts distinct active concepts per collection set."""
    spec, cid_a = _spec_with_collection("a")
    cid_b = spec.get_resource_manager(Collection).create(Collection(name="b")).resource_id
    _member(spec, cid_a, "x")
    _member(spec, cid_b, "y")
    _member(spec, cid_b, "z")
    assert count_clusters(spec, [cid_a]) == 1
    assert count_clusters(spec, [cid_a, cid_b]) == 3
    assert count_clusters(spec, []) == 0


def test_page_clusters_empty_collections_short_circuits():
    """No readable collections → an empty page + zero total, no query."""
    spec, _cid = _spec_with_collection()
    assert page_clusters(spec, []) == ([], 0)


def test_page_clusters_offset_beyond_the_end_is_an_empty_page_with_the_full_total():
    """Paging past the last concept returns no rows (no member load) but still the
    full distinct-concept total — the pager can render "0 shown of N"."""
    spec, cid = _spec_with_collection()
    _member(spec, cid, "a")
    _member(spec, cid, "b")
    page, total = page_clusters(spec, [cid], offset=5, limit=2)
    assert page == [] and total == 2
