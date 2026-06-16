"""PdfParser v2 — per-page, selectively VLM-backed (issue #39).

The KB's PDFs come in two shapes: "paper" (born-digital, rich text
layer) and "slide export" (sparse text, the meaning lives in pixels).
SOTA practice is **selective VLM**: extract the text layer per page
(pypdf — fast, verbatim), and only pages whose text is sparse or that
embed images get rendered (pypdfium2 — pip wheel, liberal license, no
system deps) and described by the VLM. Text-only deploys (no
``kb.vlm_llm``) degrade to text-layer-only pages.

``pdf_pages_to_documents`` is shared with ``PptxParser`` (slides
convert to PDF via soffice, then ride this same path).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..vlm import VlmDescriber
from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document

logger = logging.getLogger(__name__)

# Pages whose extracted text is shorter than this are "sparse" — their
# content presumably lives in pixels, so they go to the VLM. Tunable
# per-parser; 50 chars ≈ a title line.
SPARSE_TEXT_THRESHOLD = 50

# ~200 DPI (PDF user space is 72/inch) — the researched sweet spot for
# VLM legibility vs payload size.
_RENDER_SCALE = 200 / 72


def _render_page_png(data: bytes, page_index: int) -> bytes:
    """Rasterise one PDF page to PNG bytes via pypdfium2."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        bitmap = pdf[page_index].render(scale=_RENDER_SCALE)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


def pdf_pages_to_documents(
    data: bytes,
    *,
    filename: str,
    mime: str,
    describer: VlmDescriber | None,
    on_progress: Callable[[str], None] | None = None,
    sparse_text_threshold: int = SPARSE_TEXT_THRESHOLD,
    parser_label: str = "PdfParser",
    page_word: str = "page",
) -> list[Document]:
    """One Document per page: text layer + (selectively) VLM markdown.

    A page is "visual" when its text layer is sparse OR it embeds
    images; visual pages render → VLM describe, and the description is
    appended after the verbatim text layer. Pages that end up with no
    content at all (sparse + no VLM) produce no Document."""
    import pypdf
    from llama_index.core.schema import Document

    reader = pypdf.PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    docs: list[Document] = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        try:
            has_images = bool(page.images)
        except Exception:  # noqa: BLE001 — malformed resource dicts happen in the wild
            has_images = False
        visual = len(text) < sparse_text_threshold or has_images
        vlm_md = ""
        if visual and describer is not None:
            if on_progress is not None:
                on_progress(f"{parser_label}: {page_word} {i + 1}/{total} → VLM")
            png = _render_page_png(data, i)
            vlm_md = describer.describe(
                png, "image/png", context=f"{page_word} {i + 1} of {filename}"
            )
        body = "\n\n".join(part for part in (text, vlm_md) if part)
        if not body:
            logger.info(
                "%s: %s %d of %s has no content — skipped", parser_label, page_word, i + 1, filename
            )
            continue
        docs.append(
            Document(text=body, metadata={"filename": filename, "mime": mime, "page": i + 1})
        )
    return docs


class PdfParser(IParser):
    """Matches PDFs with or without a VLM — the text layer alone is
    worth indexing (unlike images, where no VLM means no text)."""

    def __init__(
        self,
        describer: VlmDescriber | None = None,
        *,
        sparse_text_threshold: int = SPARSE_TEXT_THRESHOLD,
    ) -> None:
        self._describer = describer
        self._sparse_text_threshold = sparse_text_threshold

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        return mime == "application/pdf" or filename.lower().endswith(".pdf")

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
    ) -> list[Document]:
        return pdf_pages_to_documents(
            source.as_bytes(),
            filename=filename,
            mime=mime,
            describer=self._describer,
            on_progress=on_progress,
            sparse_text_threshold=self._sparse_text_threshold,
        )
