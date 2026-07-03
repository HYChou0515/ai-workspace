"""#377 DocQuestion — the per-doc clarification questions the digest raises when
it can't confidently define a term (→ card) or follow a passage (→ wiki). Tests
exercise the ``kb.doc_questions`` helper surface, not specstar internals."""

from specstar import QB

from workspace_app.kb.card_gen import DescriptionQuestionDraft, TermQuestionDraft
from workspace_app.kb.doc_questions import (
    add_description_question,
    answer_question,
    discard_question,
    land_description_answer,
    land_term_answer,
    list_open_questions,
    open_or_merge_term_question,
    open_questions_for_collections,
    plan_doc_questions,
)
from workspace_app.kb.wiki.store import (
    CLARIFICATIONS_DIR,
    WikiFileStore,
    clarification_page_path,
)
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard, DocQuestion


class _FakeFormatter:
    """Turns a term + the human's raw answer into a clean (title, body)."""

    def format(self, *, term: str, answer: str) -> tuple[str, str]:
        return (f"{term} (title)", f"def: {answer}")


def _cards(spec, cid: str) -> list[ContextCard]:
    rm = spec.get_resource_manager(ContextCard)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ContextCard)
        out.append(r.data)
    return out


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


def test_description_question_carries_its_quote_and_passage():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = add_description_question(
        spec,
        collection_id=cid,
        source_doc_id="doc1",
        quote="uses M4 then CMP",
        question_text="Why skip the clean before CMP?",
    )
    got = _get(spec, qid)
    assert got.kind == "description"
    assert got.status == "open"
    assert got.source_doc_id == "doc1"
    assert got.quote == "uses M4 then CMP"
    assert got.question_text == "Why skip the clean before CMP?"


def test_description_question_dedupes_within_the_same_source_doc():
    # #377 P7 re-run idempotency: re-indexing a doc re-raises the same passage; it
    # must NOT duplicate the question (else the inbox floods on every reindex).
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    q1 = add_description_question(
        spec, collection_id=cid, source_doc_id="doc1", quote="uses M4 then CMP", question_text="?"
    )
    q2 = add_description_question(
        spec, collection_id=cid, source_doc_id="doc1", quote="uses M4 then CMP", question_text="?"
    )
    assert q2 == q1  # same (doc, passage) → the same question, not a duplicate


def test_description_question_reopens_only_for_a_new_source_doc():
    # A DIFFERENT doc quoting the same passage is a distinct, doc-specific question.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    q1 = add_description_question(
        spec, collection_id=cid, source_doc_id="doc1", quote="uses M4 then CMP", question_text="?"
    )
    q2 = add_description_question(
        spec, collection_id=cid, source_doc_id="doc2", quote="uses M4 then CMP", question_text="?"
    )
    assert q2 != q1
    assert _get(spec, q2).source_doc_id == "doc2"


def test_discarded_description_stays_discarded_on_same_doc_rerun():
    # #377 Q11 scoped to descriptions: a discarded passage does NOT re-open when the
    # SAME doc is re-indexed — only a new source doc raises it afresh.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    q1 = add_description_question(
        spec, collection_id=cid, source_doc_id="doc1", quote="uses M4 then CMP", question_text="?"
    )
    discard_question(spec, q1)
    q2 = add_description_question(
        spec, collection_id=cid, source_doc_id="doc1", quote="uses M4 then CMP", question_text="?"
    )
    assert q2 == q1  # the discarded question, not a fresh one
    assert _get(spec, q1).status == "discarded"  # still discarded, not re-opened


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


def test_list_open_questions_spans_all_collections_and_excludes_resolved():
    # The global inbox: every open question, any collection; answered/discarded out.
    spec = make_spec(default_user="u")
    a, b = _collection(spec, "a"), _collection(spec, "b")
    qa = open_or_merge_term_question(
        spec, collection_id=a, term="M4", source_doc_id="d1", question_text="?"
    )
    qb = add_description_question(
        spec, collection_id=b, source_doc_id="d1", quote="q", question_text="?"
    )
    resolved = open_or_merge_term_question(
        spec, collection_id=a, term="R7", source_doc_id="d1", question_text="?"
    )
    answer_question(spec, resolved, answer="x", result_ref="context-card:z")
    ids = {qid for qid, _ in list_open_questions(spec)}
    assert ids == {qa, qb}  # both collections' open questions, resolved excluded
    # #415: scoped to one collection for the 待審核 tab's inbox.
    assert {qid for qid, _ in list_open_questions(spec, collection_id=a)} == {qa}
    assert {qid for qid, _ in list_open_questions(spec, collection_id=b)} == {qb}


def test_land_term_answer_creates_a_card_and_marks_answered():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="?"
    )
    card_id = land_term_answer(spec, qid, answer="fourth metal layer", formatter=_FakeFormatter())

    q = _get(spec, qid)
    assert q.status == "answered"
    assert q.answer == "fourth metal layer"
    assert q.result_ref == card_id  # provenance points at the produced card

    (card,) = _cards(spec, cid)
    assert card.keys == ["M4"]  # keyed by the question's term
    assert card.norm_keys == ["m4"]
    assert card.title == "M4 (title)"  # AI-formatted from the human's answer
    assert card.body == "def: fourth metal layer"


def test_land_term_answer_updates_an_existing_card_for_the_term():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    existing = (
        spec.get_resource_manager(ContextCard)
        .create(ContextCard(collection_id=cid, keys=["M4"], norm_keys=["m4"], body="old"))
        .resource_id
    )
    qid = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="?"
    )
    card_id = land_term_answer(spec, qid, answer="new def", formatter=_FakeFormatter())

    assert card_id == existing  # updated in place, not duplicated
    (card,) = _cards(spec, cid)
    assert card.body == "def: new def"


async def test_land_description_answer_appends_to_the_clarification_page():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    store = WikiFileStore(spec)
    qid = add_description_question(
        spec,
        collection_id=cid,
        source_doc_id="d1",
        quote="uses M4 then CMP",
        question_text="Why skip the clean before CMP?",
    )
    path = await land_description_answer(
        spec, qid, answer="The M4 stack is already clean.", wiki_store=store
    )
    # #397 Q14: one clarification page per question, under the reserved folder.
    assert path == clarification_page_path(qid)
    assert path.startswith(CLARIFICATIONS_DIR)

    q = _get(spec, qid)
    assert q.status == "answered"
    assert q.answer == "The M4 stack is already clean."
    assert q.result_ref == path

    page = (await store.read(cid, path)).decode()
    assert "uses M4 then CMP" in page  # the quoted passage, faithfully
    assert "The M4 stack is already clean." in page  # the human's answer


async def test_land_description_answer_keeps_each_question_on_its_own_page():
    # #397 Q14: distinct questions land on distinct pages (was one growing file), so
    # answering a second question never touches the first.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    store = WikiFileStore(spec)
    q1 = add_description_question(
        spec, collection_id=cid, source_doc_id="d1", quote="quote one", question_text="q1?"
    )
    q2 = add_description_question(
        spec, collection_id=cid, source_doc_id="d1", quote="quote two", question_text="q2?"
    )
    p1 = await land_description_answer(spec, q1, answer="answer one", wiki_store=store)
    p2 = await land_description_answer(spec, q2, answer="answer two", wiki_store=store)

    assert p1 != p2  # separate pages
    page1 = (await store.read(cid, p1)).decode()
    page2 = (await store.read(cid, p2)).decode()
    assert "answer one" in page1 and "quote one" in page1
    assert "answer two" in page2 and "quote two" in page2
    assert "answer two" not in page1  # first page untouched by the second answer


def test_discarded_term_is_re_asked_when_raised_again():
    # #377 Q11: discarding a term isn't permanent suppression — a later occurrence
    # opens a FRESH question (unlike descriptions, terms dedupe only among `open`).
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    q1 = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d1", question_text="q"
    )
    discard_question(spec, q1)
    q2 = open_or_merge_term_question(
        spec, collection_id=cid, term="M4", source_doc_id="d2", question_text="q"
    )
    assert q2 != q1  # a new open question, not the discarded one
    assert _get(spec, q2).status == "open"
    assert _get(spec, q1).status == "discarded"  # the old one is left as-is


# NOTE (edge — collection/doc delete): DocQuestion declares the same
# `Ref("collection"/"source-doc", on_delete=cascade)` as the rest of the KB
# (intent + real-DB enforcement), but collection/doc delete does NOT cascade to
# children in this specstar config — SourceDoc/DocChunk/WikiPage orphan the same
# way (see tests/kb/test_wiki_store.py). That's a pre-existing platform-wide
# lifecycle gap, not something #377 introduces or scopes. A question is
# self-contained anyway (its term/quote/question text are copied in, never
# re-fetched), so an orphaned question still answers correctly; no cleanup test.


async def test_clarification_entry_omits_a_blank_question_and_quote():
    # Defensive shape: a description question with no question text / no quote still
    # lands — the rendered entry is just the answer, without an empty heading or
    # blockquote (covers the "skip the blank part" branches of _render_clarification).
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    store = WikiFileStore(spec)
    qid = (
        spec.get_resource_manager(DocQuestion)
        .create(DocQuestion(collection_id=cid, kind="description", source_doc_id="d1"))
        .resource_id
    )
    path = await land_description_answer(spec, qid, answer="just the answer", wiki_store=store)
    page = (await store.read(cid, path)).decode()
    assert "just the answer" in page


def test_plan_drops_term_questions_already_carded():
    # Guardrail ①: a term the collection already has a card for is not re-asked.
    terms = [TermQuestionDraft(term="M4", question="?"), TermQuestionDraft(term="R7", question="?")]
    kept_terms, kept_desc = plan_doc_questions(terms, [], carded_norm_keys={"m4"}, cap=10)
    assert [t.term for t in kept_terms] == ["R7"]
    assert kept_desc == []


def test_plan_caps_total_questions_per_doc_with_terms_first():
    # Guardrail ③: at most `cap` questions per doc; terms (definitions) prioritised.
    terms = [TermQuestionDraft(term=f"T{i}", question="?") for i in range(4)]
    descs = [DescriptionQuestionDraft(quote=f"q{i}", question="?") for i in range(4)]
    kept_terms, kept_desc = plan_doc_questions(terms, descs, carded_norm_keys=set(), cap=5)
    assert len(kept_terms) == 4  # terms fill first
    assert len(kept_desc) == 1  # then descriptions up to the remaining budget
