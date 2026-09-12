"""Reading a document's original page (plan-rag-context P4) — the pure half.

"The original" is the page as it actually looks, not the parsed text: a figure's
caption is second-hand, the figure is first-hand. Pages are the unit for
page-shaped documents (PDF, slide decks, images); text-shaped documents have
lines instead (`read_lines`), never synthetic pages.

Where a page's pixels come from:

- a PDF: its own bytes, rasterised (`parsers.pdf.render_page_png`);
- a slide deck: the PDF LibreOffice produced at ingest — already persisted on
  `SourceDoc.preview` for the browser viewer, so no re-conversion;
- an image file: the file itself, and it has exactly one page.

The page's TEXT LAYER is the union of the chunks the parser stamped with that
page (`provenance.page`, indexed), sliced from the canonical text — verbatim and
cheap, for "what does this page say"; the image is for what text cannot carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from specstar import QB, SpecStar

from ..resources.kb import DocChunk, SourceDoc


@dataclass(frozen=True)
class PageSource:
    """Which bytes render a document's pages, and how many there are."""

    kind: str  # "pdf" | "image"
    data: bytes
    mime: str
    pages: int


def page_source(doc: SourceDoc) -> PageSource | None:
    """The rendering source for `doc` (loaded with its blobs), or ``None`` for a
    document that has no pages."""
    from .parsers.pdf import page_count

    ctype = doc.content.content_type or ""
    if ctype == "application/pdf":
        data = doc.content.data
        assert isinstance(data, bytes)
        return PageSource("pdf", data, ctype, page_count(data))
    if ctype.startswith("image/"):
        data = doc.content.data
        assert isinstance(data, bytes)
        return PageSource("image", data, ctype, 1)
    preview = doc.preview
    if preview is not None and preview.content_type == "application/pdf":
        data = preview.data
        assert isinstance(data, bytes)
        return PageSource("pdf", data, "application/pdf", page_count(data))
    return None


def page_png(source: PageSource, page: int) -> tuple[bytes, str]:
    """``(bytes, mime)`` for 1-based `page` of `source` — caller has range-checked."""
    if source.kind == "image":
        return source.data, source.mime
    from .parsers.pdf import render_page_png

    return render_page_png(source.data, page - 1), "image/png"


def page_text(spec: SpecStar, doc_id: str, doc: SourceDoc, page: int) -> tuple[str, int, int]:
    """The text layer of 1-based `page` as ``(text, start, end)``: the union span
    of the document's chunks stamped with that page, sliced from the canonical
    text — the span is what the read registers as a citable passage. ``("", 0, 0)``
    when no chunk names the page (an image-only page the parser described, or a
    document indexed before pages were recorded)."""
    text = doc.text or ""
    fid = getattr(doc.content, "file_id", None)
    by_doc = QB["source_doc_id"] == doc_id
    cond = (QB["source_file_id"] == fid) | by_doc if isinstance(fid, str) and fid else by_doc
    # A PDF stamps `page`, a deck stamps `slide` — both indexed (#263).
    on_page = (QB["page"] == page) | (QB["slide"] == page)
    cond = (QB["collection_id"] == doc.collection_id) & cond & on_page
    rm = spec.get_resource_manager(DocChunk)
    lo, hi = None, None
    for r in rm.list_resources(cond.build(), returns=["data"], partial=["/start", "/end"]):
        ch = cast(DocChunk, r.data)  # a partial projection, not a full record
        lo = ch.start if lo is None else min(lo, ch.start)
        hi = ch.end if hi is None else max(hi, ch.end)
    if lo is None or hi is None:
        return "", 0, 0
    return text[lo:hi], lo, hi
