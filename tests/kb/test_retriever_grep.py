"""plan-rag-context P3 — exact-string search (`Retriever.grep`), the Ctrl+F arm.

Neither retrieval arm can find "Fig. 1": dense sees no semantic similarity to
a caption, BM25 tokenizes it to ["fig", "1"] with no phrase concept. This arm
is exact, case-insensitive, and returns LOCATIONS in document order — it does
not rank, and it is not citable (locate, then read)."""

from specstar import SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection

_PAPER = "intro line\nas shown in Fig. 1 the yield\nmore text\nFigure 1. Yield vs temperature\n"
_NOTES = "see fig. 1 in the paper\nunrelated\n"


def _collection(spec, chunker, embedder, docs: dict[str, str]) -> str:
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for name, text in docs.items():
        ing.ingest(collection_id=cid, user="u", filename=name, data=text.encode())
    return cid


def test_grep_finds_every_exact_occurrence_with_line_numbers_in_tree_order(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(spec, chunker, embedder, {"paper.md": _PAPER, "b/notes.md": _NOTES})
    r = Retriever(spec, embedder=embedder).grep("fig. 1", [cid])
    # Exact and case-insensitive: "Fig. 1" and "fig. 1" match; "Figure 1" does not.
    # Document-tree order (the folder first), then by line within a document.
    assert [(h.filename, h.line, h.text) for h in r.hits] == [
        ("notes.md", 1, "see fig. 1 in the paper"),
        ("paper.md", 2, "as shown in Fig. 1 the yield"),
    ]
    assert r.total == 2
    assert r.hits[1].document_id == encode_doc_id(cid, "paper.md")
    assert r.hits[0].path == "b/notes.md"


def test_grep_reports_a_line_once_however_many_chunks_overlap_it(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # The conftest chunker overlaps neighbours by a token, so the SAME line sits
    # in two chunks; a hit is a (document, line), not a chunk.
    cid = _collection(spec, chunker, embedder, {"p.md": "alpha beta gamma delta beta epsilon"})
    r = Retriever(spec, embedder=embedder).grep("beta", [cid])
    assert [(h.line, h.text) for h in r.hits] == [(1, "alpha beta gamma delta beta epsilon")]


def test_grep_honours_the_speakers_scope(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(spec, chunker, embedder, {"a.md": "needle here", "b.md": "needle there"})
    a, b = encode_doc_id(cid, "a.md"), encode_doc_id(cid, "b.md")
    r = Retriever(spec, embedder=embedder)
    assert [h.filename for h in r.grep("needle", [cid], exclude_doc_ids=frozenset({a})).hits] == [
        "b.md"
    ]
    assert [
        h.filename for h in r.grep("needle", [cid], restrict_to_doc_ids=frozenset({b})).hits
    ] == ["b.md"]


def test_grep_caps_the_hits_but_reports_the_true_total(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _collection(spec, chunker, embedder, {"p.md": "\n".join(f"x{i} tok" for i in range(9))})
    r = Retriever(spec, embedder=embedder).grep("tok", [cid], limit=3)
    assert len(r.hits) == 3 and r.total == 9
    assert [h.line for h in r.hits] == [1, 2, 3]  # the first ones in document order


def test_grep_finds_a_phrase_that_straddles_two_chunks(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # Windows of 3 tokens stepping by 2: "alpha beta gamma" / "gamma delta eps".
    # The three-token phrase "beta gamma delta" is inside NEITHER chunk, so a
    # store pre-filter on the whole phrase would never see it. The anchor is one
    # token of it (inside some chunk by construction) and the exact match is
    # verified on the canonical text with the chunk span widened.
    cid = _collection(spec, chunker, embedder, {"p.md": "alpha beta gamma delta eps"})
    r = Retriever(spec, embedder=embedder).grep("beta gamma delta", [cid])
    assert [(h.line, h.text) for h in r.hits] == [(1, "alpha beta gamma delta eps")]


def test_grep_refuses_an_anchor_too_short_to_narrow_on(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # A one-character anchor matches nearly every chunk and would pull every
    # document's text into memory; the retriever declines instead.
    cid = _collection(spec, chunker, embedder, {"p.md": "ab 1 c 1 d"})
    r = Retriever(spec, embedder=embedder)
    assert r.grep("1", [cid]).hits == []  # one-char anchor: refused
    assert r.grep("c 1", [cid]).hits == []  # longest token is one char: refused
    assert len(r.grep("ab 1", [cid]).hits) == 1  # a two-char anchor is enough (positive control)


def test_grep_marks_the_result_truncated_when_the_store_cap_is_hit(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder, monkeypatch
):
    import workspace_app.kb.retriever as retriever_mod

    cid = _collection(
        spec, chunker, embedder, {"p.md": "\n".join(f"tok line {i}" for i in range(6))}
    )
    monkeypatch.setattr(retriever_mod, "MAX_CHUNKS", 2)
    r = Retriever(spec, embedder=embedder).grep("tok", [cid])
    assert r.truncated is True
    assert 0 < r.total < 6  # a partial list, flagged as such


def test_line_index_agrees_with_the_one_off_lookup():
    from workspace_app.kb.grep import LineIndex, line_at

    text = "alpha\nbeta gamma\n\ndelta"
    idx = LineIndex(text)
    for off in range(len(text)):
        line_no, line = line_at(text, off)
        assert idx.line_of(off) == line_no
        assert idx.line_text(line_no) == line
