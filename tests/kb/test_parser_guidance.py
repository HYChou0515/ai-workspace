"""Issue #328: a collection's ``parser_guidance`` — a single per-collection
free-text prompt APPENDED to every prompt-driven parser's base prompt — reaches
the VLM at index time through the real ingest pipeline."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence

import msgspec
import pypdf
from specstar import QB

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import Collection, DocChunk, make_spec
from workspace_app.resources.kb import EMBED_DIM, SourceDoc


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


def _set_override(spec, doc_id: str, override: str) -> None:
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    drm.update(doc_id, msgspec.structs.replace(doc, parser_guidance_override=override))


def test_doc_override_replaces_collection_guidance_at_index_time():
    """#356 escape hatch: a doc whose ``parser_guidance_override`` is set is
    parsed with THAT override INSTEAD OF the collection's ``parser_guidance``
    (REPLACE, not append) — so a few special docs can opt out of the collection
    prompt without changing it for everyone."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", parser_guidance="COLLECTION GUIDANCE"))
        .resource_id
    )
    vlm = _RecordingVlm()
    ing = _ingestor(spec, vlm)
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf())

    _set_override(spec, doc_id, "DOC OVERRIDE")
    vlm.prompts.clear()
    ing.index(doc_id)  # re-index now that the doc carries its own override

    assert vlm.prompts
    assert all("DOC OVERRIDE" in p for p in vlm.prompts)
    assert all("COLLECTION GUIDANCE" not in p for p in vlm.prompts)


def test_empty_doc_override_inherits_collection_guidance():
    """An empty override (the default for every doc) ⇒ the doc still inherits the
    collection's ``parser_guidance`` — the escape hatch is opt-in per doc."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", parser_guidance="COLLECTION GUIDANCE"))
        .resource_id
    )
    vlm = _RecordingVlm()
    ing = _ingestor(spec, vlm)
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf())

    assert vlm.prompts
    assert all("COLLECTION GUIDANCE" in p for p in vlm.prompts)


def test_doc_override_survives_reupload():
    """#356: the override is an extraction setting, not tied to a content version
    — re-uploading new bytes for the same path keeps the per-doc tuning."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", parser_guidance="COLLECTION GUIDANCE"))
        .resource_id
    )
    ing = _ingestor(spec, _RecordingVlm())
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf(1))
    _set_override(spec, doc_id, "DOC OVERRIDE")

    # re-upload DIFFERENT bytes for the same path → new revision in place, same id
    (doc_id2,) = ing.ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf(2))
    assert doc_id2 == doc_id
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc)
    assert doc.parser_guidance_override == "DOC OVERRIDE"


def _chunk_count(spec, doc_id: str) -> int:
    rm = spec.get_resource_manager(DocChunk)
    return len(list(rm.list_resources((QB["source_doc_id"] == doc_id).build())))


def test_dry_run_reparses_with_candidate_guidance_and_persists_nothing():
    """#328 dry-run: re-parse ONE doc with a CANDIDATE guidance → return virtual
    chunks (real text + embeddings, for the Overlay preview) + the re-joined text,
    WITHOUT writing a single row. The candidate steering reaches the VLM."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", parser_guidance="BASE GUIDANCE"))
        .resource_id
    )
    vlm = _RecordingVlm()
    ing = _ingestor(spec, vlm)
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf())
    baseline = _chunk_count(spec, doc_id)
    assert baseline > 0
    vlm.prompts.clear()

    chunks, virtual_text = ing.dry_run_chunks(doc_id, guidance="CANDIDATE: a fishbone -> JSON")

    # Real virtual chunks: text + an embedding the Overlay can rank on.
    assert chunks and all(c.text for c in chunks)
    assert all(len(c.embedding or []) == EMBED_DIM for c in chunks)
    assert virtual_text  # the re-joined canonical text the offsets index into
    # The candidate guidance — not the collection's "BASE GUIDANCE" — drove this parse.
    assert vlm.prompts and all("CANDIDATE: a fishbone -> JSON" in p for p in vlm.prompts)
    assert all("BASE GUIDANCE" not in p for p in vlm.prompts)
    # Nothing persisted: the store still holds exactly the baseline chunks.
    assert _chunk_count(spec, doc_id) == baseline
