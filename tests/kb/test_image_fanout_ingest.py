"""#513 P6 — HTML/MD upload fans referenced images out into their own SourceDocs.

The defect library's HTML/MD knowledge links its figures on an internal image
server. At store time the Ingestor pulls each fetchable image down and stores it
as its OWN image SourceDoc (bytes on ``content``) — a first-class, VLM-
describable, P4-image-vector-able unit — rather than folding a description into
the text and discarding the pixels (the PdfParser template). Reuses the same
"one upload → many SourceDocs" seam as archive expansion.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.image_fetcher import IImageFetcher
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.vlm_image import VlmImageParser
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc


class _FakeFetcher(IImageFetcher):
    """Returns canned bytes for known URLs, ``None`` otherwise — stands in for
    the allowlist + network so the fan-out logic is tested in isolation."""

    def __init__(self, mapping: dict[str, tuple[bytes, str]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def fetch(self, url: str) -> tuple[bytes, str] | None:
        self.calls.append(url)
        return self._mapping.get(url)


def _ingestor(spec: SpecStar, *, image_fetcher: IImageFetcher | None) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    return Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        image_fetcher=image_fetcher,
    )


def _content_bytes(spec: SpecStar, doc_id: str) -> bytes:
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    data = drm.restore_binary(doc).content.data
    assert isinstance(data, bytes)
    return data


def test_html_upload_fans_out_allowlisted_images_as_source_docs(spec: SpecStar):
    fetcher = _FakeFetcher({"http://img.local/a.png": (b"PNGBYTES-A", "image/png")})
    ingestor = _ingestor(spec, image_fetcher=fetcher)
    cid = spec.get_resource_manager(Collection).create(Collection(name="defects")).resource_id
    html = (
        "<html><body>"
        '<img src="http://img.local/a.png">'  # fetchable → stored as its own doc
        '<img src="http://evil.example.com/b.png">'  # fetch returns None → skipped
        "</body></html>"
    )
    ids = ingestor.store(collection_id=cid, user="u", filename="d.html", data=html.encode())

    text_id = encode_doc_id(cid, "d.html")
    img_id = encode_doc_id(cid, "img.local/a.png")
    assert text_id in ids  # the HTML text doc
    assert img_id in ids  # the fetched image doc

    assert _content_bytes(spec, img_id) == b"PNGBYTES-A"  # original pixels preserved

    # The off-allowlist image was attempted but not stored (fetch → None).
    assert "http://evil.example.com/b.png" in fetcher.calls
    with pytest.raises(ResourceIDNotFoundError):
        spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "evil.example.com/b.png"))


class _FakeVlm(IVlm):
    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        yield "## Figure\n\ndescribed body", False


def _collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="defects")).resource_id


def _chunks(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    out: list[DocChunk] = []
    for r in rm.list_resources((QB["source_doc_id"] == doc_id).build()):
        assert isinstance(r.data, DocChunk)
        out.append(r.data)
    return out


def test_markdown_upload_fans_out_image_links(spec: SpecStar):
    fetcher = _FakeFetcher(
        {
            "http://img.local/x/1.png": (b"IMG1", "image/png"),
            "http://img.local/x/2.jpg": (b"IMG2", "image/jpeg"),
        }
    )
    ingestor = _ingestor(spec, image_fetcher=fetcher)
    cid = _collection(spec)
    md = "# Defect X\n\n![v1](http://img.local/x/1.png)\n\n![v2](http://img.local/x/2.jpg)\n"
    ids = ingestor.store(collection_id=cid, user="u", filename="d.md", data=md.encode())

    assert encode_doc_id(cid, "img.local/x/1.png") in ids
    assert encode_doc_id(cid, "img.local/x/2.jpg") in ids
    assert _content_bytes(spec, encode_doc_id(cid, "img.local/x/1.png")) == b"IMG1"
    assert _content_bytes(spec, encode_doc_id(cid, "img.local/x/2.jpg")) == b"IMG2"


def test_no_fetcher_wired_stores_text_only(spec: SpecStar):
    """Additivity: without an image fetcher the HTML upload behaves exactly as
    today — text doc only, referenced images left as inert links."""
    ingestor = _ingestor(spec, image_fetcher=None)
    cid = _collection(spec)
    html = '<html><body><img src="http://img.local/a.png"></body></html>'
    ids = ingestor.store(collection_id=cid, user="u", filename="d.html", data=html.encode())

    assert ids == [encode_doc_id(cid, "d.html")]
    with pytest.raises(ResourceIDNotFoundError):
        spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "img.local/a.png"))


def test_non_html_md_upload_is_not_scanned_for_images(spec: SpecStar):
    """A plain-text upload is never scanned — its ``![](url)``-looking text is
    not a figure, so the fetcher is never called and no image doc appears."""
    fetcher = _FakeFetcher({"http://img.local/a.png": (b"IMG", "image/png")})
    ingestor = _ingestor(spec, image_fetcher=fetcher)
    cid = _collection(spec)
    txt = "see ![](http://img.local/a.png) — but this is a .txt, not markdown\n"
    ingestor.store(collection_id=cid, user="u", filename="notes.txt", data=txt.encode())

    assert fetcher.calls == []
    with pytest.raises(ResourceIDNotFoundError):
        spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "img.local/a.png"))


def test_same_url_referenced_twice_is_fetched_once(spec: SpecStar):
    fetcher = _FakeFetcher({"http://img.local/a.png": (b"IMG", "image/png")})
    ingestor = _ingestor(spec, image_fetcher=fetcher)
    cid = _collection(spec)
    html = (
        "<html><body>"
        '<img src="http://img.local/a.png">'
        '<p>and again</p><img src="http://img.local/a.png">'
        "</body></html>"
    )
    ingestor.store(collection_id=cid, user="u", filename="d.html", data=html.encode())

    assert fetcher.calls == ["http://img.local/a.png"]  # deduped within the doc


def test_legacy_chunker_mode_does_not_fetch_images(spec: SpecStar):
    """Image fan-out is pipeline-mode only: the VLM parser that would describe a
    fetched image doesn't exist in legacy chunker mode, so nothing is fetched."""
    fetcher = _FakeFetcher({"http://img.local/a.png": (b"IMG", "image/png")})
    embedder = HashEmbedder(dim=EMBED_DIM)
    ingestor = Ingestor(
        spec,
        chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=1),
        embedder=embedder,
        image_fetcher=fetcher,
    )
    cid = _collection(spec)
    md = "# X\n\n![v1](http://img.local/a.png)\n"
    ingestor.store(collection_id=cid, user="u", filename="d.md", data=md.encode())

    assert fetcher.calls == []
    with pytest.raises(ResourceIDNotFoundError):
        spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "img.local/a.png"))


def test_fetched_image_is_indexed_and_vlm_described(spec: SpecStar):
    """End-to-end (#513 P6 DoD): a fetched figure becomes a searchable image doc
    — the VLM describes it and the description lands on the image doc's chunks."""
    png = b"\x89PNG\r\n\x1a\n fake pixels"
    fetcher = _FakeFetcher({"http://img.local/ring.png": (png, "image/png")})
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(VlmImageParser(VlmDescriber(_FakeVlm())))
    ingestor = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
        image_fetcher=fetcher,
    )
    cid = _collection(spec)
    md = "# Ring defect\n\n![ring](http://img.local/ring.png)\n"
    ingestor.ingest(collection_id=cid, user="u", filename="d.md", data=md.encode())

    chunks = _chunks(spec, encode_doc_id(cid, "img.local/ring.png"))
    assert chunks, "fetched image produced no chunks"
    assert any("described body" in c.text for c in chunks)


def test_create_app_accepts_kb_image_fetcher():
    """The P6 socket is reachable from the composition root: create_app threads
    an image fetcher into the KB Ingestor without error (defaults to None, so
    existing deploys are unaffected)."""
    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.kb.image_fetcher import HttpImageFetcher
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_image_fetcher=HttpImageFetcher(allowed_hosts=["img.local"]),
    )
    assert app is not None  # construction succeeded with the image fetcher wired
