"""#377 DocQuestion — the per-doc clarification questions the digest raises when
it can't confidently define a term (→ card) or follow a passage (→ wiki). Tests
exercise the ``kb.doc_questions`` helper surface, not specstar internals."""

from workspace_app.kb.doc_questions import (
    add_description_question,
    answer_question,
    discard_question,
    open_or_merge_term_question,
    open_questions_for_collections,
)
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, DocQuestion


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _get(spec, qid: str) -> DocQuestion:
    got = spec.get_resource_manager(DocQuestion).get(qid).data
    assert isinstance(got, DocQuestion)  # narrow Struct|Unset for ty
    return got


def test_open_term_question_creates_open_with_derived_norm_key():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec,
        collection_id=cid,
        term="M4",
        source_doc_id="doc1",
        question_text="What does M4 mean?",
    )
    got = _get(spec, qid)
    assert got.kind == "term"
    assert got.status == "open"
    assert got.term == "M4"
    assert got.norm_key == "m4"  # derived via the shared context-card norm()
    assert got.source_doc_ids == ["doc1"]
    assert got.question_text == "What does M4 mean?"
    assert got.collection_id == cid


def test_term_question_dedupes_and_merges_source_docs():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    q1 = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="doc1", question_text="What is M4?"
    )
    # A different doc raises the SAME term (full-width Ｍ４ normalises to "m4").
    q2 = open_or_merge_term_question(
        spec, collection_id=cid, term="Ｍ４", source_doc_id="doc2", question_text="M4 again?"
    )
    assert q2 == q1  # merged into the open question, not duplicated
    got = _get(spec, q1)
    assert got.source_doc_ids == ["doc1", "doc2"]


def test_term_question_merge_is_idempotent_for_same_doc():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="doc1", question_text="q"
    )
    # The same doc re-raising the same term (a digest re-run) must not duplicate it.
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="doc1", question_text="q"
    )
    assert _get(spec, qid).source_doc_ids == ["doc1"]


def test_description_question_carries_quote_and_does_not_dedupe():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    q1 = add_description_question(
        spec,
        collection_id=cid,
        source_doc_id="doc1",
        quote="uses M4 then CMP",
        question_text="Why skip the clean before CMP?",
    )
    # An identical description from the same doc is a DISTINCT question (no dedupe).
    q2 = add_description_question(
        spec,
        collection_id=cid,
        source_doc_id="doc1",
        quote="uses M4 then CMP",
        question_text="Why skip the clean before CMP?",
    )
    assert q1 != q2
    got = _get(spec, q1)
    assert got.kind == "description"
    assert got.status == "open"
    assert got.source_doc_id == "doc1"
    assert got.quote == "uses M4 then CMP"
    assert got.question_text == "Why skip the clean before CMP?"


def test_answer_question_sets_answered_with_answer_and_result_ref():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="doc1", question_text="q"
    )
    answer_question(spec, qid, answer="The fourth metal mask layer.", result_ref="context-card:abc")
    got = _get(spec, qid)
    assert got.status == "answered"
    assert got.answer == "The fourth metal mask layer."
    assert got.result_ref == "context-card:abc"


def test_discard_question_sets_discarded():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="doc1", question_text="q"
    )
    discard_question(spec, qid)
    assert _get(spec, qid).status == "discarded"


def test_inbox_lists_only_open_questions_in_the_given_collections():
    spec = make_spec(default_user="u")
    a, b = _collection(spec, "a"), _collection(spec, "b")
    q_open = open_or_merge_term_question(
        spec, collection_id=a, term="M4", source_doc_id="d1", question_text="q"
    )
    q_ans = open_or_merge_term_question(
        spec, collection_id=a, term="R7", source_doc_id="d1", question_text="q"
    )
    answer_question(spec, q_ans, answer="x", result_ref="context-card:z")
    q_dis = open_or_merge_term_question(
        spec, collection_id=a, term="CMP", source_doc_id="d1", question_text="q"
    )
    discard_question(spec, q_dis)
    # another collection's open question is excluded when we scope to [a]
    open_or_merge_term_question(
        spec, collection_id=b, term="ELF", source_doc_id="d1", question_text="q"
    )
    ids = [qid for qid, _ in open_questions_for_collections(spec, [a])]
    assert ids == [q_open]  # answered / discarded / other-collection all excluded
