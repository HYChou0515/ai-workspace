"""plan-rag-context P4 — reading the original: `read_page` (image + text layer,
page-shaped documents) and `read_lines` (text, `read_file` dialect). Two units;
a unit is offered where it exists and refused where it does not."""

import io
from collections.abc import Iterator, Sequence

import pypdf
from agents import RunContextWrapper, ToolOutputImage, ToolOutputText
from specstar import SpecStar

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import read_lines_impl, read_page_impl
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.parsers.registry import ParserRegistry
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import AgentConfig
from workspace_app.resources.kb import EMBED_DIM, Collection


class _FakeVlm(IVlm):
    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        yield "described page", False


def _blank_pdf(pages: int) -> bytes:
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return buf.getvalue()


def _kb(spec, docs: dict[str, bytes], *, vlm: IVlm | None = None):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(PdfParser(VlmDescriber(vlm or _FakeVlm())))
    ing = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
    )
    for name, data in docs.items():
        ing.ingest(collection_id=cid, user="u", filename=name, data=data)
    return cid, embedder


def _ctx(spec, embedder, cid, *, vision: bool = True, **kw):
    return RunContextWrapper(
        AgentToolContext(
            spec=spec,
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=[cid],
            agent_config=AgentConfig(name="kb", model="x", vision=vision),
            **kw,
        )
    )


# ── read_lines ────────────────────────────────────────────────────────────


async def test_read_lines_returns_a_window_in_the_read_file_dialect_and_is_citable(
    spec: SpecStar,
):
    text = "\n".join(f"line {i}" for i in range(1, 11)).encode()
    cid, emb = _kb(spec, {"notes.md": text})
    ctx = _ctx(spec, emb, cid)
    out = await read_lines_impl(ctx, "notes.md", offset=3, limit=2)
    # Numbered like a kb_search passage: what you read is what you cite from.
    assert out.startswith("[1] notes.md (lines 3-4):\nline 3\nline 4")
    assert "showing lines 3-4 of 10" in out
    [p] = ctx.context.kb_passages
    assert (p.start, p.end) == (len("line 1\nline 2\n"), len("line 1\nline 2\nline 3\nline 4"))
    assert p.text == "line 3\nline 4"
    # Reading the same window again reuses [1]; a different window is [2].
    await read_lines_impl(ctx, "notes.md", offset=3, limit=2)
    assert len(ctx.context.kb_passages) == 1
    out2 = await read_lines_impl(ctx, "notes.md", offset=5, limit=1)
    assert out2.startswith("[2] ")


async def test_read_lines_past_the_end_mints_no_citation(spec: SpecStar):
    # An offset beyond the last line reads nothing; nothing read means nothing
    # to cite — no [n], no empty past-EOF span in the registry.
    cid, emb = _kb(spec, {"notes.md": b"one\ntwo\nthree"})
    ctx = _ctx(spec, emb, cid)
    out = await read_lines_impl(ctx, "notes.md", offset=100)
    assert out == "notes.md has 3 lines; pass an offset from 1 to 3."
    assert ctx.context.kb_passages == []
    # The other way to read nothing — a limit below 1 — is the same class, and
    # is refused up front: `lines[0:-1]` is NOT empty, so a `-1` (a common
    # "everything" convention) used to read n-1 lines with a truncation notice
    # and mint a citation, while `-5` was refused. One rule, before the slice.
    for limit in (0, -1, -5):
        out = await read_lines_impl(ctx, "notes.md", limit=limit)
        assert out == f"nothing to read: limit must be at least 1 (got {limit})."
    assert ctx.context.kb_passages == []


async def test_read_lines_refuses_a_screenshot_and_points_at_read_page(spec: SpecStar):
    cid, emb = _kb(spec, {"shot.png": _png()})
    out = await read_lines_impl(_ctx(spec, emb, cid), "shot.png")
    assert "has no lines" in out and "read_page" in out


async def test_read_lines_respects_the_speakers_document_exclusion(spec: SpecStar):
    from workspace_app.kb.doc_id import encode_doc_id

    cid, emb = _kb(spec, {"secret.md": b"hidden"})
    ctx = _ctx(spec, emb, cid, exclude_doc_ids=frozenset({encode_doc_id(cid, "secret.md")}))
    out = await read_lines_impl(ctx, "secret.md")
    assert "hidden" not in out and "No document matching" in out


async def test_read_lines_never_names_a_denied_document_when_disambiguating(spec: SpecStar):
    from workspace_app.kb.doc_id import encode_doc_id

    cid, emb = _kb(spec, {"hr/report.md": b"salary table", "pub/report.md": b"public notes"})
    ctx = _ctx(spec, emb, cid, exclude_doc_ids=frozenset({encode_doc_id(cid, "hr/report.md")}))
    out = await read_lines_impl(ctx, "report.md")
    assert "public notes" in out and "hr/" not in out and "salary" not in out


# ── read_page ─────────────────────────────────────────────────────────────


async def test_read_page_returns_the_pdf_page_as_an_image_plus_its_text_layer(
    spec: SpecStar,
):
    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(3)})
    ctx = _ctx(spec, emb, cid)
    out = await read_page_impl(ctx, "deck.pdf", 2)
    assert isinstance(out, list)
    text, image = out
    assert isinstance(text, ToolOutputText) and isinstance(image, ToolOutputImage)
    assert text.text.startswith("[1] deck.pdf — page 2 of 3")
    assert "described page" in text.text  # the page's own chunks, not the whole doc
    assert image.image_url is not None and image.image_url.startswith("data:image/png;base64,")
    # Citable: the page's text layer is registered with its page provenance.
    [p] = ctx.context.kb_passages
    assert p.provenance == {"page": [2]}
    assert p.text == "described page"


async def test_read_page_on_a_deck_uses_the_slide_provenance_and_keeps_pages_apart(
    spec: SpecStar,
):
    # A slide deck's chunks carry `slide`, not `page` (pdf_pages_to_documents
    # with page_word="slide"); the text layer must still be found, and two
    # image-only pages (empty span) of one document must not share a marker.
    import msgspec
    from specstar import QB

    from workspace_app.resources.kb import DocChunk

    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(2)})
    rm = spec.get_resource_manager(DocChunk)
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        prov = {"slide": ch.provenance["page"]}
        rm.update(r.info.resource_id, msgspec.structs.replace(ch, provenance=prov))  # ty: ignore[unresolved-attribute]
    ctx = _ctx(spec, emb, cid)
    one = await read_page_impl(ctx, "deck.pdf", 1)
    two = await read_page_impl(ctx, "deck.pdf", 2)
    assert isinstance(one, list) and isinstance(two, list)
    first, second = one[0], two[0]
    assert isinstance(first, ToolOutputText) and isinstance(second, ToolOutputText)
    assert "described page" in first.text  # found via `slide`
    assert first.text.startswith("[1] ") and second.text.startswith("[2] ")
    assert [p.provenance for p in ctx.context.kb_passages] == [{"page": [1]}, {"page": [2]}]
    # …and reading a page AGAIN reuses its marker: the dedup key compares the
    # page on both sides in the same form (the first version compared an int
    # against the aggregated list and never matched, so every read minted a
    # new [n] — and this test passed for the wrong reason).
    again = await read_page_impl(ctx, "deck.pdf", 1)
    assert isinstance(again, list) and isinstance(again[0], ToolOutputText)
    assert again[0].text.startswith("[1] ")
    assert len(ctx.context.kb_passages) == 2


async def test_read_page_out_of_range_says_how_many_pages_there_are(spec: SpecStar):
    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(3)})
    out = await read_page_impl(_ctx(spec, emb, cid), "deck.pdf", 9)
    assert isinstance(out, str) and "has 3 pages" in out


async def test_read_page_on_a_screenshot_is_the_image_itself(spec: SpecStar):
    cid, emb = _kb(spec, {"shot.png": _png()})
    out = await read_page_impl(_ctx(spec, emb, cid), "shot.png", 1)
    assert isinstance(out, list)
    assert any(isinstance(p, ToolOutputImage) for p in out)
    assert isinstance(await read_page_impl(_ctx(spec, emb, cid), "shot.png", 2), str)


async def test_read_page_refuses_a_text_document_and_points_at_read_lines(spec: SpecStar):
    cid, emb = _kb(spec, {"notes.md": b"just text"})
    out = await read_page_impl(_ctx(spec, emb, cid), "notes.md", 1)
    assert isinstance(out, str) and "has no pages" in out and "read_lines" in out


async def test_read_page_on_a_text_only_model_goes_through_the_describer(spec: SpecStar):
    # No vision on the main model: the page image is described by `kb.vlm_llm`
    # (the same branch `read_image` takes) and the text layer still rides along.
    class _ReadTimeVlm(IVlm):
        calls = 0

        def stream(
            self, prompt: str, *, images: Sequence[tuple[bytes, str]]
        ) -> Iterator[tuple[str, bool]]:
            _ReadTimeVlm.calls += 1
            yield "a bar chart, read at request time", False

    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(1)})
    ctx = _ctx(spec, emb, cid, vision=False, describer=VlmDescriber(_ReadTimeVlm()))
    out = await read_page_impl(ctx, "deck.pdf", 1)
    assert isinstance(out, str)
    # The ingest-time description ("described page") is the text layer; the
    # describer's own words are what prove the image was looked at NOW.
    assert "described page" in out and "a bar chart, read at request time" in out
    assert _ReadTimeVlm.calls == 1


async def test_read_page_without_any_vision_model_says_so(spec: SpecStar):
    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(1)})
    out = await read_page_impl(_ctx(spec, emb, cid, vision=False), "deck.pdf", 1)
    assert isinstance(out, str) and "no vision model" in out


async def test_read_page_bounds_its_text_part_so_the_image_survives_the_output_cap(
    spec: SpecStar,
):
    # A long text layer is middle-truncated INSIDE the tool: the output cap
    # can only degrade a [text, image] list to text (losing the image), so the
    # text part must never be what trips it.
    class _LongVlm(IVlm):
        def stream(
            self, prompt: str, *, images: Sequence[tuple[bytes, str]]
        ) -> Iterator[tuple[str, bool]]:
            yield " ".join(f"word{i}" for i in range(400)), False

    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(1)}, vlm=_LongVlm())
    ctx = _ctx(spec, emb, cid, read_file_max_chars=300)
    out = await read_page_impl(ctx, "deck.pdf", 1)
    assert isinstance(out, list) and len(out) == 2
    assert isinstance(out[0], ToolOutputText) and isinstance(out[1], ToolOutputImage)
    assert len(out[0].text) <= 300 + 120 and "chars omitted" in out[0].text
    assert out[0].text.startswith("[1] deck.pdf — page 1 of 1")


async def test_read_lines_on_a_document_whose_text_is_not_extracted_yet(spec: SpecStar):
    # Stored but not indexed: `SourceDoc.text` is None until the index job runs.
    from workspace_app.kb.ingest import Ingestor

    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    emb = HashEmbedder(dim=EMBED_DIM)
    ing = Ingestor(spec, pipeline=build_doc_pipeline(embedder=emb), embedder=emb)
    ing.store(collection_id=cid, user="u", filename="later.md", data=b"not yet")
    out = await read_lines_impl(_ctx(spec, emb, cid), "later.md")
    assert "has no extracted text yet" in out


async def test_read_page_on_a_missing_document_says_so(spec: SpecStar):
    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(1)})
    out = await read_page_impl(_ctx(spec, emb, cid), "nowhere.pdf", 1)
    assert isinstance(out, str) and "No document matching 'nowhere.pdf'" in out


async def test_read_tools_treat_a_document_deleted_mid_resolve_as_missing(
    spec: SpecStar, monkeypatch
):
    # The name resolves, the row is gone by the time it is read (deleted between
    # the two queries): the same answer as a name that never matched.
    from workspace_app.kb import doc_resolve
    from workspace_app.kb.doc_resolve import DocResolution

    cid, emb = _kb(spec, {"notes.md": b"one\ntwo"})
    real = doc_resolve.resolve_document

    def gone(*a, **kw):
        res = real(*a, **kw)
        assert res.status == "ok" and res.doc_id is not None
        return DocResolution(status="ok", doc_id=res.doc_id + "-gone", path=res.path)

    monkeypatch.setattr(doc_resolve, "resolve_document", gone)
    out = await read_lines_impl(_ctx(spec, emb, cid), "notes.md")
    assert "No document matching 'notes.md'" in out


def test_a_deck_with_a_preview_pdf_is_a_page_source():
    from specstar.types import Binary

    from workspace_app.kb.pages import page_source
    from workspace_app.resources import SourceDoc

    deck = SourceDoc(
        collection_id="c",
        path="talk.pptx",
        content=Binary(data=b"not a pdf", content_type="application/vnd.ms-powerpoint"),
        preview=Binary(data=_blank_pdf(3), content_type="application/pdf"),
    )
    src = page_source(deck)
    assert src is not None and (src.kind, src.pages, src.mime) == ("pdf", 3, "application/pdf")
    plain = SourceDoc(
        collection_id="c",
        path="notes.md",
        content=Binary(data=b"x", content_type="text/markdown"),
    )
    assert page_source(plain) is None
