"""Doc-question inbox routes (#377) — thin HTTP adapters over the answer-landing
domain, proven on a bare app (a real spec + verbatim formatter + wiki store)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api.doc_question_routes import register_doc_question_routes
from workspace_app.kb.answer_formatter import VerbatimAnswerFormatter
from workspace_app.kb.doc_questions import add_description_question, open_or_merge_term_question
from workspace_app.kb.wiki.store import CLARIFICATIONS_DIR, WikiFileStore
from workspace_app.perm.model import Permission
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


def test_list_hides_questions_from_collections_the_user_cannot_read():
    """#481: the inbox is permission-filtered — a private collection's questions no
    longer leak to a user who can't read it (the list used to return everything)."""
    spec = make_spec(default_user="owner")
    rm = spec.get_resource_manager(Collection)
    with rm.using(user="owner"):
        cid = rm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id
    open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="?"
    )
    app = FastAPI()
    register_doc_question_routes(
        app,
        spec,
        formatter=VerbatimAnswerFormatter(),
        wiki_store=WikiFileStore(spec),
        get_user_id=lambda: "outsider",
    )
    assert TestClient(app).get("/kb/doc-questions").json() == []  # not leaked


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


async def test_answering_a_term_question_keeps_the_loop_free():
    """`land_term_answer` calls the answer-card formatter, an LLM when
    `card_drafter_llm` is wired — synchronously, from an `async def` route.
    The P1 sweep of plan-graceful-shutdown missed this member (round 1); a
    slow formatter held the event loop for the whole call, the same class as
    the `read_image` stall that failed the liveness probe."""
    import asyncio
    import time

    import httpx

    class _SlowFormatter(VerbatimAnswerFormatter):
        def format(self, *, term: str, answer: str) -> tuple[str, str]:
            time.sleep(0.3)  # a model call, from the loop's point of view
            return super().format(term=term, answer=answer)

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="?"
    )
    app = FastAPI()
    register_doc_question_routes(
        app, spec, formatter=_SlowFormatter(), wiki_store=WikiFileStore(spec)
    )
    worst = 0.0
    stop = False

    async def watch() -> None:
        nonlocal worst
        while not stop:
            t0 = time.monotonic()
            await asyncio.sleep(0.02)
            worst = max(worst, time.monotonic() - t0 - 0.02)

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0.02)  # the watcher is asleep before the request starts
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            r = await client.post(f"/kb/doc-questions/{qid}/answer", json={"answer": "metal 4"})
    finally:
        stop = True
        await watcher
    assert r.status_code == 200
    assert worst < 0.1, f"the loop was blocked for {worst * 1000:.0f} ms"
