"""VlmImageParser + PdfParser v2 + PptxParser — issue #39 P10/P11 +
PDF upgrade.

Locked decisions (docs/plan-kb-parsers.md, research-corrected):
  - Images: one VLM call per image, layered prompt, embed the text.
    No VLM wired → ``matches`` returns False (doc stores with zero
    chunks; reindex picks it up once a VLM is configured).
  - PDF: per-page Documents. The user's PDFs come in two shapes —
    "paper" (rich text layer) and "slide export" (sparse text, visual)
    — so VLM is **selective**: only pages whose text layer is sparse
    or that embed images go through pypdfium2 render → VlmDescriber.
    Without a VLM the parser degrades to text-only pages.
  - PPTX: soffice (LibreOffice headless) converts to PDF — the
    converted PDF keeps a verbatim text layer AND rasterizes — then
    the same per-page logic runs. soffice missing → clear error
    (status=error + status_detail; operator installs and reindexes).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.parsers.slides import PptxParser
from workspace_app.kb.parsers.vlm_image import VlmImageParser
from workspace_app.kb.vlm import IVlm, VlmDescriber

# 1x1 px PNG (same fixture as tests/api/test_kb_api.py).
_MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)

# Single-page PDF with a real text layer (same fixture as
# tests/kb/test_li_pipeline.py).
_TEXT_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
    b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
    b"3 0 obj <</Type /Page /Parent 2 0 R /Contents 4 0 R /Resources <</Font <</F1 "
    b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>>>>>>> endobj\n"
    b"4 0 obj <</Length 44>> stream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello PDF World) Tj ET\nendstream endobj\n"
    b"xref\n0 5\n"
    b"0000000000 65535 f\n0000000009 00000 n\n0000000055 00000 n\n"
    b"0000000098 00000 n\n0000000182 00000 n\n"
    b"trailer <</Size 5 /Root 1 0 R>>\nstartxref\n275\n%%EOF\n"
)


def _sparse_pdf() -> bytes:
    """Single empty page — no text layer at all (the "slide export"
    shape). Built with pypdf's writer so the xref table is valid."""
    import io

    import pypdf

    w = pypdf.PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


_SPARSE_PDF = _sparse_pdf()


class FakeVlm(IVlm):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield "## OCR\n\netch chamber wafer map", False


def _describer() -> tuple[FakeVlm, VlmDescriber]:
    vlm = FakeVlm()
    return vlm, VlmDescriber(vlm)


def _input(data: bytes, filename: str) -> MaterialisedParserInput:
    return MaterialisedParserInput(data, filename=filename)


# ── VlmImageParser ───────────────────────────────────────────────────


def test_image_parser_does_not_match_without_a_vlm():
    """No VLM wired → matches() False → image uploads store with zero
    chunks (Q9b) instead of erroring; an operator who later configures
    kb.vlm_llm just reindexes."""
    p = VlmImageParser(None)
    assert p.matches(filename="d.png", mime="image/png", source=_input(_MIN_PNG, "d.png")) is False


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("d.png", "image/png", True),
        ("photo.jpg", "image/jpeg", True),
        ("photo.jpeg", "image/jpeg", True),
        ("d.webp", "image/webp", True),
        ("anim.gif", "image/gif", False),  # animated — out of scope this round
        ("vector.svg", "image/svg+xml", False),  # text format, not raster
        ("doc.pdf", "application/pdf", False),
    ],
)
def test_image_parser_matches_raster_types(filename: str, mime: str, expected: bool):
    _, d = _describer()
    p = VlmImageParser(d)
    assert p.matches(filename=filename, mime=mime, source=_input(b"x", filename)) is expected


def test_image_parser_one_image_one_document_via_vlm():
    vlm, d = _describer()
    p = VlmImageParser(d)
    progress: list[str] = []
    docs = list(
        p.parse(
            _input(_MIN_PNG, "wafer.png"),
            filename="wafer.png",
            mime="image/png",
            on_progress=progress.append,
        )
    )
    assert len(docs) == 1
    assert "etch chamber wafer map" in docs[0].text
    # The image bytes reached the VLM; the filename anchored the prompt.
    (call,) = vlm.calls
    assert call["images"] == [(_MIN_PNG, "image/png")]
    assert "wafer.png" in str(call["prompt"])
    # Long-call progress surfaced for the FE status_detail.
    assert any("wafer.png" in m for m in progress)


def test_image_parser_uses_guidance_and_threads_it_to_the_vlm():
    """#328: a VLM parser declares ``uses_guidance()`` True and threads the
    collection's ``parser_guidance`` into the describe prompt, so the operator's
    per-collection extraction steering reaches the VLM at parse time."""
    vlm, d = _describer()
    p = VlmImageParser(d)
    assert p.uses_guidance() is True
    list(
        p.parse(
            _input(_MIN_PNG, "wafer.png"),
            filename="wafer.png",
            mime="image/png",
            guidance="A fishbone diagram -> emit JSON.",
        )
    )
    assert "A fishbone diagram -> emit JSON." in str(vlm.calls[0]["prompt"])


def test_image_parser_tags_document_as_markdown_content():
    """Issue #115: the VLM emits Markdown, but the source mime is image/png.
    The parser flags the output `content_format='markdown'` so DispatchSplitter
    routes it through the Markdown path (heading-aware, tables kept whole)
    instead of the SentenceSplitter that truncates mid-structure."""
    _, d = _describer()
    docs = list(
        VlmImageParser(d).parse(
            _input(_MIN_PNG, "wafer.png"), filename="wafer.png", mime="image/png"
        )
    )
    assert docs[0].metadata["content_format"] == "markdown"
    # Source mime stays truthful — we route on content_format, never by lying
    # about what the original file was.
    assert docs[0].metadata["mime"] == "image/png"


# ── PdfParser v2 ─────────────────────────────────────────────────────


def test_pdf_text_page_skips_the_vlm():
    """A page with a healthy text layer ("paper" shape) must NOT spend
    a VLM call — selective VLM is the researched default."""
    vlm, d = _describer()
    p = PdfParser(d, sparse_text_threshold=5)
    docs = list(
        p.parse(_input(_TEXT_PDF, "paper.pdf"), filename="paper.pdf", mime="application/pdf")
    )
    assert len(docs) == 1
    assert "Hello PDF World" in docs[0].text
    assert docs[0].metadata["page"] == 1
    assert vlm.calls == []


def test_pdf_sparse_page_goes_through_the_vlm():
    """A page with no text layer ("slide export" shape) renders via
    pypdfium2 and the VLM's markdown becomes the page Document."""
    vlm, d = _describer()
    p = PdfParser(d)
    docs = list(
        p.parse(_input(_SPARSE_PDF, "deck.pdf"), filename="deck.pdf", mime="application/pdf")
    )
    assert len(docs) == 1
    assert "etch chamber wafer map" in docs[0].text
    (call,) = vlm.calls
    # A real PNG render reached the VLM.
    img, mime = call["images"][0]  # type: ignore[index]  # ty: ignore[not-subscriptable]
    assert isinstance(img, bytes) and img[:8] == b"\x89PNG\r\n\x1a\n"
    assert mime == "image/png"
    assert "page 1 of deck.pdf" in str(call["prompt"])


def test_pdf_visual_page_tagged_markdown_text_page_is_not():
    """Issue #115: a page whose body came from the VLM is Markdown → tagged
    content_format='markdown' so it splits on structure. A plain text-layer
    page is prose → NOT tagged, so it stays on the SentenceSplitter (tagging it
    markdown would make MarkdownNodeParser emit one giant heading-less node)."""
    _, d = _describer()
    visual = list(
        PdfParser(d).parse(
            _input(_SPARSE_PDF, "deck.pdf"), filename="deck.pdf", mime="application/pdf"
        )
    )
    assert visual[0].metadata["content_format"] == "markdown"

    _, d2 = _describer()
    text_only = list(
        PdfParser(d2, sparse_text_threshold=5).parse(
            _input(_TEXT_PDF, "paper.pdf"), filename="paper.pdf", mime="application/pdf"
        )
    )
    assert text_only[0].metadata.get("content_format") != "markdown"


def test_pdf_without_vlm_degrades_to_text_only():
    """No VLM wired: text pages still index (pypdf text layer); sparse
    pages simply produce nothing — never an error."""
    p = PdfParser(None)
    docs = list(
        p.parse(_input(_TEXT_PDF, "paper.pdf"), filename="paper.pdf", mime="application/pdf")
    )
    assert len(docs) == 1 and "Hello PDF World" in docs[0].text

    sparse_docs = list(
        p.parse(_input(_SPARSE_PDF, "deck.pdf"), filename="deck.pdf", mime="application/pdf")
    )
    assert sparse_docs == []


def test_pdf_matches_with_or_without_vlm():
    """Unlike images, PDFs are useful without a VLM (text layer), so
    matches() doesn't depend on the describer."""
    p = PdfParser(None)
    assert p.matches(filename="a.pdf", mime="application/pdf", source=_input(b"%PDF", "a.pdf"))
    assert not p.matches(filename="a.txt", mime="text/plain", source=_input(b"x", "a.txt"))


def test_pdf_parser_threads_guidance_into_the_vlm_describe():
    """#328: a PDF's visual pages run through the VLM describe path, so the
    collection's parser_guidance must reach that prompt (slides reuse the SAME
    pdf_pages_to_documents helper, so this covers both)."""
    vlm, d = _describer()
    p = PdfParser(d)
    assert p.uses_guidance() is True
    list(
        p.parse(
            _input(_SPARSE_PDF, "deck.pdf"),
            filename="deck.pdf",
            mime="application/pdf",
            guidance="A fishbone diagram -> emit JSON.",
        )
    )
    assert any("A fishbone diagram -> emit JSON." in str(c["prompt"]) for c in vlm.calls)


# ── PptxParser ───────────────────────────────────────────────────────


def test_pptx_uses_guidance():
    """PPTX is the user's headline case (slides → VLM → chunks); it threads the
    collection guidance through the shared pdf-page helper."""
    _, d = _describer()
    assert PptxParser(d).uses_guidance() is True


def test_pptx_matches_by_extension_or_office_mime():
    p = PptxParser(None)
    pptx_mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert p.matches(filename="deck.pptx", mime="application/zip", source=_input(b"PK", "d.pptx"))
    assert p.matches(filename="noext", mime=pptx_mime, source=_input(b"PK", "noext"))
    assert not p.matches(filename="t.xlsx", mime="application/zip", source=_input(b"PK", "t.xlsx"))


def test_pptx_without_soffice_raises_actionable_error(monkeypatch):
    """soffice not installed → a clear RuntimeError naming the missing
    binary. The Ingestor turns it into status=error with the message in
    status_detail; the operator installs LibreOffice and reindexes."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    p = PptxParser(None)
    with pytest.raises(RuntimeError, match="soffice"):
        p.parse(_input(b"PK\x03\x04", "deck.pptx"), filename="deck.pptx", mime="application/zip")


def test_pptx_converts_via_soffice_then_reuses_pdf_page_logic(monkeypatch, tmp_path):
    """The conversion seam: soffice CLI is mocked to 'convert' by
    writing our fixture PDF next to the input; the parser must then
    produce the same per-page Documents PdfParser would."""
    import shutil
    import subprocess
    from pathlib import Path

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/soffice")

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        src = Path(cmd[-1])
        (outdir / src.with_suffix(".pdf").name).write_bytes(_TEXT_PDF)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    vlm, d = _describer()
    p = PptxParser(d, sparse_text_threshold=5)
    docs = list(
        p.parse(
            _input(b"PK\x03\x04fake-pptx", "deck.pptx"),
            filename="deck.pptx",
            mime="application/zip",
        )
    )
    assert len(docs) == 1
    assert "Hello PDF World" in docs[0].text
    # Slide context, not page: the VLM prompt / metadata speak "slide" (#254 —
    # the locator key now follows page_word so provenance reads "slide N").
    assert docs[0].metadata["slide"] == 1
    assert "page" not in docs[0].metadata
    assert vlm.calls == []  # healthy text layer → no VLM spend


def _sparse_pdf_n(n: int) -> bytes:
    """N blank pages — the multi-page "slide export" shape, for fan-out
    (#227) range tests. Every page is sparse, so each goes to the VLM."""
    import io

    import pypdf

    w = pypdf.PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_count_units_is_the_page_count():
    """#227: count_units = page count (cheap, no VLM) so the fan-out
    splitter knows how many process jobs to spawn — even with no VLM."""
    p = PdfParser(None)
    assert p.count_units(_input(_sparse_pdf_n(5), "deck.pdf"), filename="deck.pdf", mime="") == 5
    assert p.count_units(_input(_TEXT_PDF, "paper.pdf"), filename="paper.pdf", mime="") == 1


def test_pdf_parse_unit_range_processes_only_those_pages():
    """#227: a process job's unit_range=[a,b) describes ONLY pages a..b-1,
    keeping global ``page`` metadata so chunk seq stays globally ordered.
    The VLM is spent only on the in-range pages — the whole point of fan-out."""
    vlm, d = _describer()
    p = PdfParser(d)
    docs = list(
        p.parse(
            _input(_sparse_pdf_n(5), "deck.pdf"),
            filename="deck.pdf",
            mime="application/pdf",
            unit_range=(1, 3),
        )
    )
    assert [doc.metadata["page"] for doc in docs] == [2, 3]
    # Only pages 2 and 3 reached the VLM — pages 1, 4, 5 were never rendered.
    prompts = [str(c["prompt"]) for c in vlm.calls]
    assert len(prompts) == 2
    assert "page 2 of deck.pdf" in prompts[0]
    assert "page 3 of deck.pdf" in prompts[1]


def test_pdf_parse_unit_range_none_is_the_whole_file():
    """unit_range=None (the default) parses every page — backwards-compatible
    with the pre-fan-out single-job path."""
    p = PdfParser(None)
    whole = list(
        p.parse(_input(_sparse_pdf_n(3), "d.pdf"), filename="d.pdf", mime="", unit_range=None)
    )
    # No VLM → sparse pages yield nothing, but count_units still sees all 3.
    assert whole == []
    assert p.count_units(_input(_sparse_pdf_n(3), "d.pdf"), filename="d.pdf", mime="") == 3


def test_pdf_progress_reported_per_vlm_page():
    """Q11: each VLM-bound page reports `PdfParser: page N/M → VLM`
    through on_progress (→ SourceDoc.status_detail)."""
    _, d = _describer()
    p = PdfParser(d)
    progress: list[str] = []
    p.parse(
        _input(_SPARSE_PDF, "deck.pdf"),
        filename="deck.pdf",
        mime="application/pdf",
        on_progress=progress.append,
    )
    assert progress == ["PdfParser: page 1/1 → VLM"]


def test_pdf_page_with_broken_image_resources_still_indexes(monkeypatch):
    """`page.images` can raise on malformed resource dicts in the wild —
    the parser treats that as "no images" and keeps going (the text
    layer still indexes)."""
    import pypdf

    real_reader = pypdf.PdfReader

    class _BrokenImagesPage:
        def __init__(self, page) -> None:
            self._page = page

        def extract_text(self):
            return self._page.extract_text()

        @property
        def images(self):
            raise ValueError("broken /Resources")

    class _Reader:
        def __init__(self, *a, **kw) -> None:
            self._r = real_reader(*a, **kw)
            self.pages = [_BrokenImagesPage(pg) for pg in self._r.pages]

    monkeypatch.setattr(pypdf, "PdfReader", _Reader)
    p = PdfParser(None, sparse_text_threshold=5)
    docs = list(
        p.parse(_input(_TEXT_PDF, "paper.pdf"), filename="paper.pdf", mime="application/pdf")
    )
    assert len(docs) == 1 and "Hello PDF World" in docs[0].text


def test_pptx_reports_conversion_progress(monkeypatch):
    import shutil
    import subprocess
    from pathlib import Path

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/soffice")

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        src = Path(cmd[-1])
        (outdir / src.with_suffix(".pdf").name).write_bytes(_TEXT_PDF)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    p = PptxParser(None, sparse_text_threshold=5)
    progress: list[str] = []
    p.parse(
        _input(b"PK\x03\x04fake", "deck.pptx"),
        filename="deck.pptx",
        mime="application/zip",
        on_progress=progress.append,
    )
    assert progress == ["PptxParser: converting deck.pptx via soffice"]


def test_pptx_conversion_producing_no_pdf_raises(monkeypatch):
    """soffice can exit 0 yet write nothing (fontconfig issues etc.) —
    surface that as a RuntimeError instead of a confusing FileNotFound."""
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/soffice")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b"")
    )
    p = PptxParser(None)
    with pytest.raises(RuntimeError, match="no PDF"):
        p.parse(_input(b"PK\x03\x04", "deck.pptx"), filename="deck.pptx", mime="application/zip")


def test_image_parser_parse_without_on_progress():
    """on_progress is optional — the parser must not require it."""
    _, d = _describer()
    docs = list(
        VlmImageParser(d).parse(_input(_MIN_PNG, "d.png"), filename="d.png", mime="image/png")
    )
    assert len(docs) == 1


def test_pptx_hands_the_converted_pdf_to_on_preview(monkeypatch):
    """PPTX preview pipeline: the soffice-converted PDF is handed back
    through `on_preview(bytes, mime)` so the Ingestor can persist it on
    `SourceDoc.preview` — the viewer then iframes the PDF instead of
    showing the binary-download notice."""
    import shutil
    import subprocess
    from pathlib import Path

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/soffice")

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        src = Path(cmd[-1])
        (outdir / src.with_suffix(".pdf").name).write_bytes(_TEXT_PDF)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    previews: list[tuple[bytes, str]] = []
    p = PptxParser(None, sparse_text_threshold=5)
    p.parse(
        _input(b"PK\x03\x04fake", "deck.pptx"),
        filename="deck.pptx",
        mime="application/zip",
        on_preview=lambda data, mime: previews.append((data, mime)),
    )
    assert previews == [(_TEXT_PDF, "application/pdf")]
