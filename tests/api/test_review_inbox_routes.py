"""Global 審核 inbox route (#481) — ``GET /kb/review-inbox``. Proven on a bare app
(a real spec, resources seeded directly) so the wiring + IO conversion + the
permission gate via ``get_user_id`` are exercised without spinning the LLM job."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.review_inbox_routes import register_review_inbox_routes
from workspace_app.kb.card_gen import CardGenRun, ProposedCard
from workspace_app.kb.card_gen_run import CardGenRunStore
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
    store.set_proposals(run_id, proposals)
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
