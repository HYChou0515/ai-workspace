"""Query-by-image, end to end: ingest images, search with an image, get the
visually-similar one back (#519).

The retriever's existing image arm is *text→image* (CLIP-style, mounted only for
a shared-space model). The placeholder is image-only, so query-by-image needs an
*image→image* arm: `search(query_image=...)` embeds the query image via
`embed_query_image` and ranks over `embedding_img`. This test drives the whole
ingest→embed→store→query→retrieve path, which is the point of wiring the
placeholder at all.
"""

from __future__ import annotations

import io

import pytest
from specstar import SpecStar

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.image_embedder import PerceptualImageEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.vlm_image import VlmImageParser
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, IMG_EMBED_DIM, Collection

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


class _FakeVlm(IVlm):
    def stream(self, prompt, *, images):
        yield "## Figure\n\na described defect image", False


def _png(color, size=(48, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def wired():
    """A collection with a red image and a blue image ingested, plus a retriever
    over the same embedders. Returns (spec, cid, retriever, image_embedder)."""
    spec = make_spec(default_user="u")
    text_emb = HashEmbedder(dim=EMBED_DIM)
    img_emb = PerceptualImageEmbedder(dim=IMG_EMBED_DIM)
    cid = spec.get_resource_manager(Collection).create(Collection(name="d")).resource_id
    registry = ParserRegistry().register(VlmImageParser(VlmDescriber(_FakeVlm())))
    ing = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=text_emb),
        embedder=text_emb,
        parser_registry=registry,
        image_embedder=img_emb,
    )
    ing.ingest(collection_id=cid, user="u", filename="red.png", data=_png((200, 40, 40)))
    ing.ingest(collection_id=cid, user="u", filename="blue.png", data=_png((40, 40, 200)))
    r = Retriever(spec, embedder=text_emb, image_embedder=img_emb)
    return spec, cid, r


def test_query_image_finds_the_visually_similar_document(wired):
    _spec, cid, r = wired
    # A reddish query image should surface the red doc above the blue one.
    hits = r.search("figure", collection_ids=[cid], query_image=_png((190, 55, 45)))

    assert hits, "query-by-image returned nothing"
    red_id = encode_doc_id(cid, "red.png")
    blue_id = encode_doc_id(cid, "blue.png")
    ranks = [h.document_id for h in hits]
    assert red_id in ranks
    assert ranks.index(red_id) < (ranks.index(blue_id) if blue_id in ranks else 1_000)


def test_no_query_image_leaves_the_text_search_untouched(wired):
    """The image→image arm only mounts when a query image is supplied — a plain
    text search must be byte-for-byte the same with or without the param."""
    _spec, cid, r = wired
    base = [h.document_id for h in r.search("figure", collection_ids=[cid])]
    with_none = [h.document_id for h in r.search("figure", collection_ids=[cid], query_image=None)]
    assert base == with_none


def test_query_image_with_no_image_embedder_does_not_crash():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="d")).resource_id
    r = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))  # no image embedder
    # No arm mounted, no crash — just a normal (empty) result.
    assert r.search("x", collection_ids=[cid], query_image=_png((1, 2, 3))) == []
