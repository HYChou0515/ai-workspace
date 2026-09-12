"""plan-rag-context P2 — neighbouring context through the real `Retriever.search`."""

from specstar import SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection

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
