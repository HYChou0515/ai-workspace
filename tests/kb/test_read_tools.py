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


def _kb(spec, docs: dict[str, bytes]):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(PdfParser(VlmDescriber(_FakeVlm())))
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
    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(1)})
    ctx = _ctx(spec, emb, cid, vision=False, describer=VlmDescriber(_FakeVlm()))
    out = await read_page_impl(ctx, "deck.pdf", 1)
    assert isinstance(out, str)
    assert "described page" in out


async def test_read_page_without_any_vision_model_says_so(spec: SpecStar):
    cid, emb = _kb(spec, {"deck.pdf": _blank_pdf(1)})
    out = await read_page_impl(_ctx(spec, emb, cid, vision=False), "deck.pdf", 1)
    assert isinstance(out, str) and "no vision model" in out
