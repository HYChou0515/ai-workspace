"""Ingest computes `embedding_img` on image documents (#519).

The `embedding_img` column existed since #513 but nothing ever wrote it — no
producer. Now, when an image embedder is wired, an ingested image's chunks
carry the image vector (of the pixels, not the VLM text), so query-by-image has
something to match. Text/non-image documents are untouched, and with no image
embedder wired the column stays None — the additive guarantee.
"""

from __future__ import annotations

import io

import pytest
from specstar import QB, SpecStar

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.image_embedder import PerceptualImageEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.vlm_image import VlmImageParser
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources.kb import EMBED_DIM, IMG_EMBED_DIM, Collection, DocChunk

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


class _FakeVlm(IVlm):
    def stream(self, prompt, *, images):
        yield "## Figure\n\na described defect", False


def _png(color=(180, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def spec() -> SpecStar:
    from workspace_app.resources import make_spec

    return make_spec(default_user="u")


def _ingestor(spec: SpecStar, *, image_embedder) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(VlmImageParser(VlmDescriber(_FakeVlm())))
    return Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
        image_embedder=image_embedder,
    )


def _coll(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id


def _chunks(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    return [
        r.data
        for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())
        if isinstance(r.data, DocChunk)
    ]


def _ingest(ingestor: Ingestor, cid: str, filename: str, data: bytes) -> str:
    from workspace_app.kb.doc_id import encode_doc_id

    ingestor.ingest(collection_id=cid, user="u", filename=filename, data=data)
    return encode_doc_id(cid, filename)


def test_an_ingested_image_carries_the_image_vector(spec: SpecStar):
    ing = _ingestor(spec, image_embedder=PerceptualImageEmbedder(dim=IMG_EMBED_DIM))
    cid = _coll(spec)
    doc_id = _ingest(ing, cid, "ring.png", _png())

    chunks = _chunks(spec, doc_id)
    assert chunks, "image produced no chunks"
    assert all(c.embedding_img is not None for c in chunks), "image vector not written"
    assert all(len(c.embedding_img or []) == IMG_EMBED_DIM for c in chunks)
    # And the text description vector still lives beside it — additive.
    assert all(c.embedding is not None for c in chunks)


def test_the_image_vector_is_the_pixels_not_the_description(spec: SpecStar):
    """Two images with the SAME VLM description but different pixels must get
    different image vectors — proving it embeds the bytes, not the text."""
    ing = _ingestor(spec, image_embedder=PerceptualImageEmbedder(dim=IMG_EMBED_DIM))
    cid = _coll(spec)
    red = _chunks(spec, _ingest(ing, cid, "red.png", _png((200, 30, 30))))
    blue = _chunks(spec, _ingest(ing, cid, "blue.png", _png((30, 30, 200))))

    assert red[0].embedding_img != blue[0].embedding_img


def test_a_text_document_gets_no_image_vector(spec: SpecStar):
    ing = _ingestor(spec, image_embedder=PerceptualImageEmbedder(dim=IMG_EMBED_DIM))
    cid = _coll(spec)
    doc_id = _ingest(ing, cid, "notes.md", b"# Notes\n\nplain text, no picture.\n")

    chunks = _chunks(spec, doc_id)
    assert chunks
    assert all(c.embedding_img is None for c in chunks)


def test_without_an_image_embedder_the_column_stays_none(spec: SpecStar):
    """The additive guarantee: no image embedder wired ⇒ ingest behaves exactly
    as before, image column unset even for an image."""
    ing = _ingestor(spec, image_embedder=None)
    cid = _coll(spec)
    doc_id = _ingest(ing, cid, "ring.png", _png())

    chunks = _chunks(spec, doc_id)
    assert chunks
    assert all(c.embedding_img is None for c in chunks)
