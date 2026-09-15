"""plan-rag-context P2 — neighbouring context through the real `Retriever.search`."""

from collections.abc import Iterator

import msgspec
from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.llm import ILlm
from workspace_app.kb.retriever import Enhancements, LocationFilter, Retriever
from workspace_app.resources.kb import Collection, DocChunk

# With the conftest chunker (3 tokens, overlap 1) this is four chunks:
# [0,8) "w1 w2 w3" · [6,14) "w3 w4 w5" · [12,20) "w5 w6 w7" · [18,26) "w7 w8 w9".
_NINE = "w1 w2 w3 w4 w5 w6 w7 w8 w9"


def _collection(spec, chunker, embedder, docs: dict[str, str]) -> str:
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for name, text in docs.items():
        ing.ingest(collection_id=cid, user="u", filename=name, data=text.encode())
    return cid


def test_hit_is_widened_to_whole_neighbouring_chunks_and_the_hit_span_is_kept(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(spec, chunker, embedder, {"nine.md": _NINE})
    # The query IS the second chunk's text, so BOTH arms rank it first (the hash
    # embedder gives identical text distance 0) — no RRF tie for a random id to break.
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=8).search(
        "w3 w4 w5", [cid]
    )
    assert p.text == "w3 w4 w5"
    assert (p.start, p.end) == (6, 14)
    # Before the hit only one chunk exists (6 chars < 8, then the document
    # edge); after it two are needed (6, then 12 ≥ 8) — the whole text.
    assert p.context_text == _NINE
    assert (p.context_start, p.context_end) == (0, 26)


def test_zero_leaves_the_passages_exactly_as_before(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(spec, chunker, embedder, {"nine.md": _NINE})
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=0).search(
        "w3 w4 w5", [cid]
    )
    assert p.text == "w3 w4 w5"
    assert p.context_text == ""


def test_context_continues_into_the_next_document_in_tree_order(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # Three one-chunk files; the hit is in 01.md, which has nothing after the
    # hit, so the context walks into 02.md — named on a boundary line — and
    # NOT into 10.md first (natural order: 02 before 10).
    cid = _collection(
        spec,
        chunker,
        embedder,
        {"01.md": "a1 a2 a3", "10.md": "z1 z2 z3", "02.md": "b1 b2 b3"},
    )
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=2).search(
        "a1 a2 a3", [cid]
    )
    assert p.document_id == encode_doc_id(cid, "01.md")
    assert p.text == "a1 a2 a3"
    assert p.context_text == "a1 a2 a3\n\n── 02.md ──\n\nb1 b2 b3"
    assert (p.context_start, p.context_end) == (0, 8)  # the spill has no offset here


def test_a_neighbour_the_speaker_cannot_read_is_skipped_and_the_walk_continues(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(
        spec,
        chunker,
        embedder,
        {"01.md": "a1 a2 a3", "02.md": "b1 b2 b3", "03.md": "c1 c2 c3"},
    )
    denied = encode_doc_id(cid, "02.md")
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=2).search(
        "a1 a2 a3", [cid], exclude_doc_ids=frozenset({denied})
    )
    # 02.md is neither read nor named; the walk goes on to 03.md.
    assert "b1" not in p.context_text
    assert "02.md" not in p.context_text
    assert p.context_text == "a1 a2 a3\n\n── 03.md ──\n\nc1 c2 c3"


def test_context_never_leaves_a_positive_document_scope(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #518 restriction (a card's links today, a folder in P3): "search only
    # inside these documents". The walk must not spill outside it — the first
    # version did, and the card-scope test in test_kb_search caught it naming
    # the out-of-scope neighbour.
    cid = _collection(
        spec,
        chunker,
        embedder,
        {"01.md": "a1 a2 a3", "02.md": "b1 b2 b3", "03.md": "c1 c2 c3"},
    )
    within = frozenset({encode_doc_id(cid, "01.md"), encode_doc_id(cid, "03.md")})
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=2).search(
        "a1 a2 a3", [cid], restrict_to_doc_ids=within
    )
    assert "02.md" not in p.context_text and "b1" not in p.context_text
    assert p.context_text == "a1 a2 a3\n\n── 03.md ──\n\nc1 c2 c3"


class _RecordingLlm(ILlm):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield "1", False


def test_the_reranker_is_shown_the_expanded_context_not_the_bare_hit(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # The plan's central Phase 2 argument: the reranker ranks what will be
    # DELIVERED, so expansion runs before rerank, on the candidates. Moving
    # `_expand` after the rerank block left every test green (review round 3);
    # this one reads the prompt the reranker actually received.
    cid = _collection(spec, chunker, embedder, {"nine.md": _NINE})
    llm = _RecordingLlm()
    Retriever(spec, embedder=embedder, llm=llm, candidates=1, top_k=1).search(
        "w3 w4 w5", [cid], enhancements=Enhancements(expand=0, hyde=0, rerank=True)
    )
    [prompt] = [p for p in llm.prompts if "w3 w4 w5" in p]
    # w9 is not in the hit chunk ("w3 w4 w5"); only the walk brings it.
    assert "w9" in prompt


def _stamp_pages(spec, cid, page_of_seq):
    """Give the chunks of the only doc in `cid` a page each, by seq."""
    rm = spec.get_resource_manager(DocChunk)
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        rm.update(
            r.info.resource_id,
            msgspec.structs.replace(ch, provenance={"page": page_of_seq[ch.seq]}),
        )


def test_a_page_scoped_search_keeps_its_context_inside_the_page_range(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #263 location scope ("pages 2-2 of nine.md") is a scope like any other:
    # the hit is confined to it, and so is the context — the first version
    # widened into pages the user explicitly scoped out.
    cid = _collection(spec, chunker, embedder, {"nine.md": _NINE, "next.md": "z1 z2 z3"})
    # chunks 0..3 of nine.md → pages 1, 2, 2, 3
    rm = spec.get_resource_manager(DocChunk)
    nine = encode_doc_id(cid, "nine.md")
    for r in rm.list_resources((QB["source_doc_id"] == nine).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        rm.update(
            r.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(ch, provenance={"page": [1, 2, 2, 3][ch.seq]}),
        )
    loc = LocationFilter(source_doc_id=nine, page_from=2, page_to=2)
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=100).search(
        "w3 w4 w5", [cid], location=loc
    )
    # Page 2 = chunks 1 and 2 = "w3 w4 w5 w6 w7"; pages 1 and 3 must not appear,
    # and the walk must not leave the document either.
    assert p.context_text == "w3 w4 w5 w6 w7"
    assert "next.md" not in p.context_text and "w9" not in p.context_text


def test_a_document_scoped_search_never_walks_into_another_document(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(spec, chunker, embedder, {"01.md": "a1 a2 a3", "02.md": "b1 b2 b3"})
    loc = LocationFilter(source_doc_id=encode_doc_id(cid, "01.md"))
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=50).search(
        "a1 a2 a3", [cid], location=loc
    )
    assert p.context_text == "a1 a2 a3"


def test_a_boundary_line_names_the_neighbour_by_path_not_basename(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # P19: the walk's boundary line is the coordinate the model hands to
    # `read_lines` next. Two folders can hold a `notes.md`; a basename there is
    # exactly what `read_lines("notes.md")` refuses as ambiguous, so the line
    # names the path — the same coordinate `kb_grep` prints.
    cid = _collection(
        spec,
        chunker,
        embedder,
        {"a/notes.md": "a1 a2 a3", "b/notes.md": "b1 b2 b3"},
    )
    [p] = Retriever(spec, embedder=embedder, candidates=1, top_k=1, context_chars=2).search(
        "a1 a2 a3", [cid]
    )
    assert p.document_id == encode_doc_id(cid, "a/notes.md")
    assert p.context_text == "a1 a2 a3\n\n── b/notes.md ──\n\nb1 b2 b3"


def test_the_seams_read_an_unknown_document_as_empty_and_edgeless(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    from workspace_app.kb.retriever import LocationFilter, _ContextSeams, _DocJoin

    cid = _collection(spec, chunker, embedder, {"nine.md": _NINE})
    doc_id = encode_doc_id(cid, "nine.md")
    r = Retriever(spec, embedder=embedder)
    seams = _ContextSeams(
        spec, _DocJoin(spec, [], frozenset()), {}, r._canonical_text, None, None, None, None, []
    )
    assert seams.chunks_of("collection:x∕gone.md") == []
    assert seams.neighbours("collection:x∕gone.md") == (None, None)
    # asked twice, the second read is served from what the first loaded
    first = seams.chunks_of(doc_id)
    assert first and seams.chunks_of(doc_id) == first
    # a page-scoped location filter admits nothing without a page to compare
    assert LocationFilter(source_doc_id=doc_id, page_from=1, page_to=2).admits({}) is False
