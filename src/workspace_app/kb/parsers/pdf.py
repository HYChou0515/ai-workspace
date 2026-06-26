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
    import pypdf
    from llama_index.core.schema import Document

logger = logging.getLogger(__name__)

# Pages whose extracted text is shorter than this are "sparse" — their
# content presumably lives in pixels, so they go to the VLM. Tunable
# per-parser; 50 chars ≈ a title line.
SPARSE_TEXT_THRESHOLD = 50

# ~200 DPI (PDF user space is 72/inch) — the researched sweet spot for
# VLM legibility vs payload size.
_RENDER_SCALE = 200 / 72


def outline_sections(reader: pypdf.PdfReader) -> dict[int, str]:
    """Map each 0-based page index → the section breadcrumb that governs it,
    derived from the PDF's bookmark outline (issue #254).

    The outline is a page-level signal only, so we approximate: a page's
    section is the *deepest bookmark that starts at-or-before it* (the last
    one in document order whose destination page ``<= i``), rendered as an
    ancestor path like ``"Chapter 2 > 2.1 Method"``. Pages before the first
    bookmark — or any page in a PDF with no outline — are absent from the map
    (their section is ``None`` downstream). Best-effort: bookmarks whose
    destination can't be resolved are skipped, never fatal."""
    marks: list[tuple[int, str]] = []  # (dest_page, breadcrumb) in document order

    def walk(items: list, trail: list[str]) -> None:
        last_title: str | None = None
        for it in items:
            if isinstance(it, list):
                # A nested list holds the children of the preceding bookmark.
                walk(it, [*trail, last_title] if last_title is not None else trail)
                continue
            title = str(getattr(it, "title", "") or "").strip()
            try:
                page = reader.get_destination_page_number(it)
            except Exception:  # noqa: BLE001 — malformed dests happen in the wild
                last_title = title or last_title
                continue
            if page is None:
                continue
            marks.append((page, " > ".join([*trail, title])))
            last_title = title

    try:
        walk(list(reader.outline), [])
    except Exception:  # noqa: BLE001 — a broken outline tree must not fail ingest
        return {}

    # Last-at-or-before wins; stable sort keeps document order for ties so a
    # page that starts several bookmarks takes the last one declared on it.
    marks.sort(key=lambda m: m[0])
    sections: dict[int, str] = {}
    cur: str | None = None
    mi = 0
    for p in range(len(reader.pages)):
        while mi < len(marks) and marks[mi][0] <= p:
            cur = marks[mi][1]
            mi += 1
        if cur is not None:
            sections[p] = cur
    return sections


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
    page_range: tuple[int, int] | None = None,
) -> list[Document]:
    """One Document per page: text layer + (selectively) VLM markdown.

    A page is "visual" when its text layer is sparse OR it embeds
    images; visual pages render → VLM describe, and the description is
    appended after the verbatim text layer. Pages that end up with no
    content at all (sparse + no VLM) produce no Document.

    ``page_range`` (#227), when given, limits work to pages in the
    half-open ``[start, end)`` interval — the fan-out process job's slice
    — while ``page`` metadata and the progress ``N/total`` stay global so
    downstream chunk ``seq`` and the FE progress remain doc-wide."""
    import pypdf
    from llama_index.core.schema import Document

    reader = pypdf.PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    # Whole-doc bookmark outline → 0-based page → section breadcrumb (#254).
    # Computed once over the full document even under a fan-out ``page_range``
    # so each slice resolves the same global section for its pages.
    sections = outline_sections(reader)
    docs: list[Document] = []
    for i, page in enumerate(reader.pages):
        if page_range is not None and not (page_range[0] <= i < page_range[1]):
            continue  # outside this process job's slice — skipped cheaply
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
        # The locator key follows ``page_word`` so slides read "slide N", not
        # "page N" (#254). ``section`` is attached only when the outline
        # governs this page — absent ⇒ graceful degrade to page-only.
        meta: dict[str, object] = {"filename": filename, "mime": mime, page_word: i + 1}
        if i in sections:
            meta["section"] = sections[i]
        # A page whose body includes VLM output is Markdown → flag it so
        # DispatchSplitter splits on its structure (issue #115). A pure
        # text-layer page is prose and stays on the SentenceSplitter.
        if vlm_md:
            meta["content_format"] = "markdown"
        docs.append(Document(text=body, metadata=meta))
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

    def count_units(self, source: IParserInput, *, filename: str, mime: str) -> int:
        """Page count (#227) — cheap: pypdf reads the page tree without
        rendering or extracting text, so the splitter can size the fan-out
        without spending a single VLM call."""
        import pypdf

        return len(pypdf.PdfReader(io.BytesIO(source.as_bytes())).pages)

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
        unit_range: tuple[int, int] | None = None,
    ) -> list[Document]:
        return pdf_pages_to_documents(
            source.as_bytes(),
            filename=filename,
            mime=mime,
            describer=self._describer,
            on_progress=on_progress,
            sparse_text_threshold=self._sparse_text_threshold,
            page_range=unit_range,
        )
