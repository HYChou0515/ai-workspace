"""Issue #263: a structural location filter scopes the retriever's candidate
set (page range / sheet) BEFORE dense+sparse ranking — "為什麼 XXX，據 30-90 頁"
= vector ranking within a page range, not a separate path."""

from __future__ import annotations

import msgspec
from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import LocationFilter, Retriever
from workspace_app.resources.kb import Collection, DocChunk


def _ingest_with_pages(spec, chunker, embedder, name, text):
    """Ingest a doc, then stamp page = seq+1 onto each chunk — the PDF-like
    provenance the plain-text Ingestor wouldn't otherwise produce."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename=name, data=text.encode()
    )
    doc_id = encode_doc_id(cid, name)
    rm = spec.get_resource_manager(DocChunk)
    for r in rm.list_resources((QB["source_doc_id"] == doc_id).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        rm.update(
            r.info.resource_id,
            msgspec.structs.replace(ch, provenance={"page": ch.seq + 1}),
        )
    return cid, doc_id


def test_search_scoped_to_page_range_returns_only_in_range_pages(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid, doc_id = _ingest_with_pages(
        spec,
        chunker,
        embedder,
        "manual.pdf",
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
    )
    # "alpha" lives on page 1, but the filter restricts to pages 3-5 — the
    # location WHERE wins over the semantic match.
    passages = Retriever(spec, embedder=embedder).search(
        "alpha",
        [cid],
        location=LocationFilter(source_doc_id=doc_id, page_from=3, page_to=5),
    )
    pages = {p for pa in passages for p in pa.provenance.get("page", [])}
    assert pages, "expected passages within the page range"
    assert pages <= {3, 4, 5}


def test_location_filter_predicate_shapes():
    assert LocationFilter().is_empty()
    assert not LocationFilter(sheet="S").is_empty()
    # both page bounds → one range predicate; plus the document → 2 total
    assert len(LocationFilter(source_doc_id="d", page_from=1, page_to=2).conditions()) == 2
    # exactly one bound → a single-page predicate (not an open range)
    assert len(LocationFilter(page_from=3).conditions()) == 1
    assert len(LocationFilter(page_to=9).conditions()) == 1
    assert len(LocationFilter(sheet="S").conditions()) == 1


def test_empty_location_filter_behaves_as_unscoped(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid, _ = _ingest_with_pages(spec, chunker, embedder, "m.pdf", "alpha beta gamma delta")
    r = Retriever(spec, embedder=embedder)
    assert r.search("alpha", [cid], location=LocationFilter()) == r.search("alpha", [cid])


def test_search_scoped_to_single_page(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid, doc_id = _ingest_with_pages(
        spec, chunker, embedder, "m.pdf", "alpha beta gamma delta epsilon zeta"
    )
    # page_from only ⇒ exactly that page, never page >= 2.
    passages = Retriever(spec, embedder=embedder).search(
        "alpha", [cid], location=LocationFilter(source_doc_id=doc_id, page_from=2)
    )
    pages = {p for pa in passages for p in pa.provenance.get("page", [])}
    assert pages == {2}


def _stamp(spec, doc_id, prov_of_seq):
    rm = spec.get_resource_manager(DocChunk)
    for r in rm.list_resources((QB["source_doc_id"] == doc_id).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        rm.update(r.info.resource_id, msgspec.structs.replace(ch, provenance=prov_of_seq(ch.seq)))


def test_search_scoped_to_sheet_and_document(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(collection_id=cid, user="u", filename="a.xlsx", data=b"alpha beta gamma delta")
    ing.ingest(collection_id=cid, user="u", filename="b.xlsx", data=b"alpha epsilon zeta eta")
    doc_a, doc_b = encode_doc_id(cid, "a.xlsx"), encode_doc_id(cid, "b.xlsx")
    # Doc A: even chunks on "Summary", odd on "Raw". Doc B all on "Summary".
    _stamp(spec, doc_a, lambda seq: {"sheet": "Summary" if seq % 2 == 0 else "Raw"})
    _stamp(spec, doc_b, lambda seq: {"sheet": "Summary"})

    passages = Retriever(spec, embedder=embedder).search(
        "alpha", [cid], location=LocationFilter(source_doc_id=doc_a, sheet="Summary")
    )
    assert passages, "expected passages from doc A's Summary sheet"
    # Stays in doc A (source_doc_id isolation) and on the Summary sheet only.
    assert {pa.document_id for pa in passages} == {doc_a}
    assert {s for pa in passages for s in pa.provenance.get("sheet", [])} <= {"Summary"}
