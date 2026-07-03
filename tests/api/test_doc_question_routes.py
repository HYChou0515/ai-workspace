"""Doc-question inbox routes (#377) — thin HTTP adapters over the answer-landing
domain, proven on a bare app (a real spec + verbatim formatter + wiki store)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.doc_question_routes import register_doc_question_routes
from workspace_app.kb.answer_formatter import VerbatimAnswerFormatter
from workspace_app.kb.doc_questions import add_description_question, open_or_merge_term_question
from workspace_app.kb.wiki.store import CLARIFICATIONS_DIR, WikiFileStore
from workspace_app.resources import Collection, ContextCard, DocQuestion, make_spec


def _client(spec) -> TestClient:
    app = FastAPI()
    register_doc_question_routes(
        app, spec, formatter=VerbatimAnswerFormatter(), wiki_store=WikiFileStore(spec)
    )
    return TestClient(app)


def _collection(spec) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id


def test_list_returns_the_open_questions():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="What is M4?"
    )
    r = _client(spec).get("/kb/doc-questions")
    assert r.status_code == 200
    (item,) = r.json()
    assert item["id"] == qid
    assert item["kind"] == "term"
    assert item["term"] == "M4"
    assert item["question_text"] == "What is M4?"


def test_list_scopes_to_a_collection_with_the_query_param():
    """#415: `?collection_id=` narrows the inbox to one collection's questions."""
    spec = make_spec(default_user="u")
    a, b = _collection(spec), _collection(spec)
    qa = open_or_merge_term_question(
        spec, collection_id=a, term="M4", source_doc_id="d1", question_text="?"
    )
    open_or_merge_term_question(
        spec, collection_id=b, term="M5", source_doc_id="d1", question_text="?"
    )
    client = _client(spec)
    assert len(client.get("/kb/doc-questions").json()) == 2  # global inbox
    scoped = client.get("/kb/doc-questions", params={"collection_id": a}).json()
    assert [q["id"] for q in scoped] == [qa]


def test_answering_a_term_question_creates_a_card_and_resolves_it():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="?"
    )
    r = _client(spec).post(f"/kb/doc-questions/{qid}/answer", json={"answer": "fourth metal layer"})
    assert r.status_code == 200
    card_id = r.json()["result_ref"]
    card = spec.get_resource_manager(ContextCard).get(card_id).data
    assert card.body == "fourth metal layer"  # verbatim formatter keeps the words
    assert spec.get_resource_manager(DocQuestion).get(qid).data.status == "answered"


def test_answering_a_description_question_targets_the_clarification_page():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = add_description_question(
        spec, collection_id=cid, source_doc_id="d1", quote="uses M4 then CMP", question_text="why?"
    )
    r = _client(spec).post(f"/kb/doc-questions/{qid}/answer", json={"answer": "already clean"})
    assert r.status_code == 200
    assert r.json()["result_ref"].startswith(CLARIFICATIONS_DIR)  # #397: per-question page
    assert spec.get_resource_manager(DocQuestion).get(qid).data.status == "answered"


def test_discarding_a_question_marks_it_discarded():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="?"
    )
    r = _client(spec).post(f"/kb/doc-questions/{qid}/discard")
    assert r.status_code == 200
    assert r.json()["status"] == "discarded"
    # a discarded question drops out of the open inbox
    assert _client(spec).get("/kb/doc-questions").json() == []
