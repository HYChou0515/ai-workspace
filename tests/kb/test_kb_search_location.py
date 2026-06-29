"""Issue #263: the location-filtered kb_search — the agent passes a filename +
page/sheet, the tool resolves the doc and scopes retrieval. Bad/missing document
is a recoverable error string (the model recovers), not an exception."""

from __future__ import annotations

import msgspec
from agents import RunContextWrapper
from specstar import QB, SpecStar

from workspace_app.agent import AgentToolContext, kb_search_impl
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection, DocChunk


def _ingest_with_pages(spec, chunker, embedder, cid, name, text):
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename=name, data=text.encode()
    )
    doc_id = encode_doc_id(cid, name)
    rm = spec.get_resource_manager(DocChunk)
    for r in rm.list_resources((QB["source_doc_id"] == doc_id).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        rm.update(r.info.resource_id, msgspec.structs.replace(ch, provenance={"page": ch.seq + 1}))
    return doc_id


def _ctx(spec, embedder, collection_ids):
    return RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=collection_ids,
            spec=spec,
        )
    )


def test_kb_search_scoped_to_page_range(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    _ingest_with_pages(
        spec,
        chunker,
        embedder,
        cid,
        "manual.pdf",
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
    )
    ctx = _ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "alpha", document="manual.pdf", page_from=3, page_to=5)

    assert "[1]" in out
    pages = {p for pa in ctx.context.kb_passages for p in pa.provenance.get("page", [])}
    assert pages, "expected scoped passages"
    assert pages <= {3, 4, 5}


def test_kb_search_page_without_document_is_a_recoverable_error(
    spec: SpecStar, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ctx = _ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "alpha", page_from=30)

    assert "document" in out.lower()
    assert ctx.context.kb_passages == []  # no search ran
    assert ctx.context.kb_search_budget.used == 0  # and no budget spent


def test_kb_search_unknown_document_is_a_recoverable_error(spec: SpecStar, embedder: HashEmbedder):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ctx = _ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "alpha", document="ghost.pdf", page_from=1)

    assert "ghost.pdf" in out
    assert ctx.context.kb_search_budget.used == 0


def test_kb_search_ambiguous_document_lists_candidates(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(collection_id=cid, user="u", filename="2023/report.pdf", data=b"alpha")
    ing.ingest(collection_id=cid, user="u", filename="2024/report.pdf", data=b"beta")
    ctx = _ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "alpha", document="report.pdf", page_from=1)

    assert "2023/report.pdf" in out and "2024/report.pdf" in out
    assert ctx.context.kb_search_budget.used == 0
