"""Issue #254: parser provenance (page / section / sheet / …) survives the
ingest pipeline and lands on ``DocChunk.provenance`` so chunks keep their
big-picture location."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator, Sequence

import pandas as pd
import pypdf
from agents import RunContextWrapper
from specstar import QB, SpecStar

from workspace_app.agent import AgentToolContext, kb_search_impl
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.provenance import aggregate_provenance, format_location, page_of, pages_of
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import Collection, DocChunk, make_spec
from workspace_app.resources.kb import EMBED_DIM


def _spec_cid() -> tuple[SpecStar, str]:
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    return spec, cid


def _chunks(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    return [r.data for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())]  # ty: ignore[invalid-return-type]


def _xlsx(sheets: dict[str, list[dict[str, object]]]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:  # ty: ignore[invalid-argument-type]
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def test_excel_chunks_carry_sheet_provenance():
    """Generic wiring: a non-PDF parser's structural metadata (``sheet``)
    rides through the splitter onto each DocChunk's provenance."""
    spec, cid = _spec_cid()
    embedder = HashEmbedder(dim=EMBED_DIM)
    ing = Ingestor(spec, pipeline=build_doc_pipeline(embedder=embedder), embedder=embedder)
    data = _xlsx({"Alpha": [{"x": "a1"}, {"x": "a2"}], "Beta": [{"y": "b1"}]})
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="book.xlsx", data=data)

    by_sheet = sorted(c.provenance.get("sheet") for c in _chunks(spec, doc_id))
    assert by_sheet == ["Alpha", "Alpha", "Beta"]


class _FakeVlm(IVlm):
    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        yield "## Figure\n\ndescribed body", False


def _pdf_with_outline() -> bytes:
    w = pypdf.PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)
    c1 = w.add_outline_item("Chapter 1", 0)
    w.add_outline_item("1.1 Intro", 1, parent=c1)
    w.add_outline_item("Chapter 2", 2)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_chunks_carry_page_and_section_provenance():
    """#254 core: a PDF's per-page page+section reach every chunk's
    provenance through the real ingest pipeline."""
    spec, cid = _spec_cid()
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(PdfParser(VlmDescriber(_FakeVlm())))
    ing = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
    )
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="m.pdf", data=_pdf_with_outline())

    prov = {(c.provenance.get("page"), c.provenance.get("section")) for c in _chunks(spec, doc_id)}
    assert prov == {
        (1, "Chapter 1"),
        (2, "Chapter 1 > 1.1 Intro"),
        (3, "Chapter 2"),
    }


class _LongFakeVlm(IVlm):
    """A dense page: a description longer than the sentence window, so P6
    windows it — every VLM description of a real page is this shape."""

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        body = " ".join(
            f"Sentence number {i} describes the figure in some detail." for i in range(80)
        )
        yield f"## Figure\n\n{body}", False


def test_windowed_vlm_pages_keep_their_page_and_section(spec: SpecStar):
    """plan-rag-context P9 (review round 3, regression lens): P6's windows were
    built as bare TextNodes with EMPTY metadata, so a dense page — every real
    VLM description — lost its `page` / `section` on the way to `DocChunk`
    (`read_page` found no text layer, `kb_grep` printed no `(p.N)`, the
    reference card no page) and the #254 section fold. A window carries its
    section's metadata like the section node it replaces."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(PdfParser(VlmDescriber(_LongFakeVlm())))
    ing = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
    )
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="m.pdf", data=_pdf_with_outline())
    chunks = _chunks(spec, doc_id)
    assert len(chunks) > 3  # windowed: more than one chunk per page
    assert {c.provenance.get("page") for c in chunks} == {1, 2, 3}
    assert all(c.provenance.get("section") for c in chunks)
    # The #254 fold: the section breadcrumb opens every chunk's embedded text.
    assert all(c.text.startswith(str(c.provenance["section"])) for c in chunks)
    # …and the two consumers the regression lens saw fail: the page's text
    # layer is found again, and a grep hit on the page says so.
    from agents import ToolOutputText

    from workspace_app.agent.tools import kb_grep_impl, read_page_impl
    from workspace_app.resources import AgentConfig

    ctx = RunContextWrapper(
        AgentToolContext(
            spec=spec,
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=[cid],
            agent_config=AgentConfig(name="kb", model="x", vision=True),
        )
    )
    page = asyncio.run(read_page_impl(ctx, "m.pdf", 2))
    assert isinstance(page, list) and isinstance(page[0], ToolOutputText)
    assert "Sentence number 0 describes" in page[0].text
    assert "(p.2)" in kb_grep_impl(ctx, "Sentence number 79", document="m.pdf")


# ── pure aggregation / labelling ─────────────────────────────────────────


def test_aggregate_provenance_unions_distinct_values_in_order():
    agg = aggregate_provenance([{"page": 3, "section": "A"}, {"page": 4, "section": "A"}])
    assert agg == {"page": [3, 4], "section": ["A"]}


def test_format_location_renders_contiguous_pages_as_a_range_with_section():
    loc = format_location({"page": [3, 4], "section": ["Failure Analysis > Root Cause"]})
    assert loc == "p.3–4 · Failure Analysis > Root Cause"


def test_format_location_single_page_and_empty():
    assert format_location({"page": [3], "section": ["Intro"]}) == "p.3 · Intro"
    assert format_location({"sheet": ["Q3"]}) == "sheet Q3"
    assert format_location({}) == ""


def test_format_location_lists_non_contiguous_pages_and_unknown_keys():
    # Non-contiguous pages stay a list, not a range; an unknown future locator
    # still renders (raw key as prefix) rather than being silently dropped.
    assert format_location({"page": [3, 7], "para": [2]}) == "p.3, 7 · para 2"


def test_kb_search_line_carries_passage_location_end_to_end():
    """#254 vertical slice: a pipeline-ingested PDF, retrieved through the real
    Retriever, surfaces its page+section in the numbered kb_search line the LLM
    reads — proof the provenance survives ingest → store → retrieve → merge."""
    spec, cid = _spec_cid()
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(PdfParser(VlmDescriber(_FakeVlm())))
    ing = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
    )
    ing.ingest(collection_id=cid, user="u", filename="m.pdf", data=_pdf_with_outline())

    ctx = RunContextWrapper(
        AgentToolContext(retriever=Retriever(spec, embedder=embedder), collection_ids=[cid])
    )
    out = kb_search_impl(ctx, "described body figure")
    assert "m.pdf (p." in out
    assert "Chapter" in out


def test_page_of_reads_a_chunk_and_pages_of_reads_the_stored_aggregate():
    """Two accessors for two shapes: a chunk carries ``{"page": 3}``; a stored
    `RetrievedPassage` carries the aggregated ``{"page": [3, 4]}``. Reading the
    stored form through `page_of` yields None — which is how the read registry's
    dedup key never matched and every re-read of a page minted a new marker."""
    assert page_of({"page": 3}) == 3
    assert page_of({"slide": 7}) == 7
    assert page_of({"page": [3]}) is None  # the aggregated form is not a chunk's
    assert page_of({"section": "Intro"}) is None
    assert pages_of({"page": [3, 4]}) == (3, 4)
    assert pages_of({"slide": [7]}) == (7,)
    assert pages_of({"page": 3}) == ()  # a chunk's form is not the stored one
    assert pages_of({"page": []}) == ()
    assert pages_of({"section": ["Intro"]}) == ()
