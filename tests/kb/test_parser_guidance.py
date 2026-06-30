"""Issue #328: a collection's ``parser_guidance`` — a single per-collection
free-text prompt APPENDED to every prompt-driven parser's base prompt — reaches
the VLM at index time through the real ingest pipeline."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence

import pypdf

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM


class _RecordingVlm(IVlm):
    """Records every prompt so a test can assert what steering reached it."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield "## Figure\n\ndescribed body", False


def _blank_pdf(pages: int = 1) -> bytes:
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)  # sparse → forces the VLM describe path
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _ingestor(spec, vlm: IVlm) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    return Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=ParserRegistry().register(PdfParser(VlmDescriber(vlm))),
    )


def test_collection_guidance_reaches_the_vlm_prompt_at_index_time():
    """A collection that set ``parser_guidance`` ⇒ every prompt-driven parser
    (here PDF→VLM) gets it appended to its base prompt when the doc indexes."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", parser_guidance="If you see a fishbone diagram, emit JSON."))
        .resource_id
    )
    vlm = _RecordingVlm()
    _ingestor(spec, vlm).ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf())

    assert vlm.prompts, "the VLM describe path should have run on a sparse page"
    assert all("If you see a fishbone diagram, emit JSON." in p for p in vlm.prompts)


def test_no_guidance_leaves_the_prompt_unsteered():
    """A collection with no ``parser_guidance`` (the default) ⇒ the base prompt
    is untouched — the seam is opt-in and zero-churn for existing collections."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    vlm = _RecordingVlm()
    _ingestor(spec, vlm).ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf())

    assert vlm.prompts
    assert all("fishbone" not in p.lower() for p in vlm.prompts)
