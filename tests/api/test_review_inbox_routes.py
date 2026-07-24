"""Global 審核 inbox route (#481) — ``GET /kb/review-inbox``. Proven on a bare app
(a real spec, resources seeded directly) so the wiring + IO conversion + the
permission gate via ``get_user_id`` are exercised without spinning the LLM job."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.review_inbox_routes import register_review_inbox_routes
from workspace_app.kb.card_gen import CardGenRun, ProposedCard, ensure_proposal_ids
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.kb.card_proposal import CardProposalStore
from workspace_app.kb.doc_questions import open_or_merge_term_question
from workspace_app.perm.model import Permission
from workspace_app.resources import Collection, make_spec


def _collection(spec, name: str, *, owner: str = "u", permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(user=owner):
        return rm.create(Collection(name=name, permission=permission)).resource_id


def _seed_done_run(spec, cid: str, proposals: list[ProposedCard], *, owner: str = "u") -> str:
    """A finalized (``done``) run carrying ``proposals`` — a 待審核 queue row —
    created as ``owner`` (the run's created_by doesn't gate access; the collection's
    permission does)."""
    store = CardGenRunStore(spec)
    with spec.get_resource_manager(CardGenRun).using(user=owner):
        run_id = store.start(cid, ["d1"])
    pstore = CardProposalStore(spec)
    for p in ensure_proposal_ids(list(proposals)):
        pstore.create_from_proposal(cid, run_id, p)
    store.finish(run_id, status="done")
    return run_id


def _client(spec, *, user: str = "u") -> TestClient:
    app = FastAPI()
    register_review_inbox_routes(app, spec, get_user_id=lambda: user)
    return TestClient(app)


def test_review_inbox_lists_pending_cards_and_questions():
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    _seed_done_run(spec, cid, [ProposedCard(keys=["RZ3"], title="RZ3")])
    open_or_merge_term_question(
        spec, collection_id=cid, term="R7", source_doc_id="d1", question_text="What is R7?"
    )

    body = _client(spec).get("/kb/review-inbox").json()
    (card,) = body["cards"]
    assert card["collection_name"] == "Alpha"
    assert card["can_act"] is True
    assert card["card"]["keys"] == ["RZ3"]
    assert card["card"]["id"]  # addressable
    (q,) = body["questions"]
    assert q["question"]["term"] == "R7"
    assert q["can_act"] is True


def test_review_inbox_hides_unreadable_and_flags_readonly():
    """#481: a private collection's items are hidden; a read-only (no write) one
    shows with ``can_act`` false."""
    spec = make_spec(default_user="owner")
    secret = _collection(spec, "Secret", owner="owner", permission=Permission(visibility="private"))
    shared = _collection(
        spec,
        "Shared",
        owner="owner",
        permission=Permission(visibility="restricted", read_content=["user:reader"]),
    )
    _seed_done_run(spec, secret, [ProposedCard(keys=["X"])], owner="owner")
    _seed_done_run(spec, shared, [ProposedCard(keys=["Y"])], owner="owner")

    body = _client(spec, user="reader").get("/kb/review-inbox").json()
    assert [c["collection_name"] for c in body["cards"]] == ["Shared"]  # Secret hidden
    assert body["cards"][0]["can_act"] is False  # read but not write


def test_review_inbox_paginates_and_filters_server_side():
    """P1 (#506): the route pages (``limit``) and filters (``kind``/``q``) on the
    server, and reports ``total`` / ``total_actionable`` so the FE renders one page
    with "X of N" and the nav badge without loading every row."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    _seed_done_run(
        spec,
        cid,
        [
            ProposedCard(keys=["RZ3"], title="RZ3"),
            ProposedCard(keys=["RZ4"], title="RZ4"),
            ProposedCard(keys=["RZ5"], title="RZ5"),
        ],
    )
    client = _client(spec)

    page = client.get("/kb/review-inbox", params={"limit": 2}).json()
    assert len(page["cards"]) + len(page["questions"]) == 2  # a single capped page
    assert page["total"] == 3
    assert page["total_actionable"] == 3  # owner may act on all

    only_q = client.get("/kb/review-inbox", params={"kind": "questions"}).json()
    assert only_q["cards"] == [] and only_q["total"] == 0

    hit = client.get("/kb/review-inbox", params={"q": "rz4"}).json()
    assert [c["card"]["keys"][0] for c in hit["cards"]] == ["RZ4"]
    assert hit["total"] == 1


def test_review_inbox_resolved_view_and_collection_scope():
    """#481: ``resolved=true`` returns handled items; ``collection_id`` scopes it."""
    spec = make_spec(default_user="u")
    a = _collection(spec, "One")
    b = _collection(spec, "Two")
    _seed_done_run(spec, a, [ProposedCard(keys=["A"])])
    _seed_done_run(spec, b, [ProposedCard(keys=["B"])])
    client = _client(spec)

    scoped = client.get("/kb/review-inbox", params={"collection_id": a}).json()
    assert [c["collection_id"] for c in scoped["cards"]] == [a]
    # nothing resolved yet → history empty
    assert client.get("/kb/review-inbox", params={"resolved": "true"}).json()["cards"] == []


def test_review_inbox_grouped_returns_clusters():
    """#506 P7: grouped=true returns one cluster per concept (a proposal + a
    question the reconcile step grouped collapse into one row); the flat
    cards/questions lists are empty in that mode."""
    from workspace_app.resources.kb import ClusterMember

    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    run_id = _seed_done_run(spec, cid, [ProposedCard(id="0", keys=["RZ3"], title="RZ3")])
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="R7", source_doc_id="d1", question_text="What is R7?"
    )
    rm = spec.get_resource_manager(ClusterMember)
    # A proposal member is addressed by the SAME id as its CardProposal
    # (prop:{run}:{pid}), so the grouped view resolves it back by id (#511 P4).
    rm.create_or_update(
        f"prop:{run_id}:0",
        ClusterMember(
            collection_id=cid, kind="proposal", ref_id="0", run_id=run_id, cluster_key="rz3"
        ),
    )
    rm.create(ClusterMember(collection_id=cid, kind="term_question", ref_id=qid, cluster_key="rz3"))

    body = _client(spec).get("/kb/review-inbox", params={"grouped": "true"}).json()

    assert body["cards"] == [] and body["questions"] == []
    assert body["total"] == 1
    (cl,) = body["clusters"]
    assert cl["cluster_key"] == "rz3"
    assert len(cl["cards"]) == 1 and len(cl["questions"]) == 1
    assert cl["size"] == 2


def test_review_inbox_suppressed_lists_dropped_candidates():
    """#506 P7: suppressed=true returns the auto-dropped candidates with their
    reason/label for audit; the normal streams are empty in that mode."""
    from workspace_app.resources.kb import ClusterMember

    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    spec.get_resource_manager(ClusterMember).create(
        ClusterMember(
            collection_id=cid,
            kind="proposal",
            ref_id="0",
            run_id="r1",
            cluster_key="rz3",
            state="suppressed",
            reason="near-card",
            label="RZ3",
            target_label="Reflow Zone 3",
        )
    )
    body = _client(spec).get("/kb/review-inbox", params={"suppressed": "true"}).json()

    assert body["cards"] == [] and body["clusters"] == []
    (s,) = body["suppressed"]
    assert s["label"] == "RZ3"
    assert s["reason"] == "near-card"
    assert s["kind"] == "proposal"
    # #506/#577 follow-up: the near-card row names the existing card it duplicated.
    assert s["target_label"] == "Reflow Zone 3"
