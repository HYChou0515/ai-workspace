"""PptxParser — slide decks via soffice → PDF → per-page logic
(issue #39 P11).

There is no pure-Python pptx rasteriser (python-pptx manipulates XML,
it doesn't render), so headless LibreOffice does the conversion. The
converted PDF keeps a **verbatim text layer** AND rasterises — which
means ``pdf_pages_to_documents`` gives us the full hybrid for free:
text per slide from the layer, VLM description only for slides whose
meaning lives in pixels (one Document per slide).

soffice missing → RuntimeError with the binary named; the Ingestor
flips the doc to status=error with the message in status_detail, and
the operator installs LibreOffice and reindexes (Q12 manual-reindex
model — the upload itself is already stored).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..vlm import VlmDescriber
from .pdf import SPARSE_TEXT_THRESHOLD, pdf_pages_to_documents
from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_CONVERT_TIMEOUT_SEC = 180


def _find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


class PptxParser(IParser):
    def __init__(
        self,
        describer: VlmDescriber | None = None,
        *,
        sparse_text_threshold: int = SPARSE_TEXT_THRESHOLD,
    ) -> None:
        self._describer = describer
        self._sparse_text_threshold = sparse_text_threshold

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        # pptx is a zip container — libmagic may report either the
        # office mime or bare application/zip; extension is reliable.
        return mime == _PPTX_MIME or filename.lower().endswith(".pptx")

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
        soffice = _find_soffice()
        if soffice is None:
            raise RuntimeError(
                "LibreOffice (soffice) is not installed — required to convert "
                ".pptx for ingestion. Install it and reindex this document."
            )
        if on_progress is not None:
            on_progress(f"PptxParser: converting {filename} via soffice")
        with tempfile.TemporaryDirectory(prefix="kb-pptx-") as td:
            src = Path(td) / (Path(filename).name or "deck.pptx")
            src.write_bytes(source.as_bytes())
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", td, str(src)],
                check=True,
                capture_output=True,
                timeout=_CONVERT_TIMEOUT_SEC,
            )
            pdf_path = src.with_suffix(".pdf")
            if not pdf_path.is_file():
                raise RuntimeError(f"soffice reported success but produced no PDF for {filename}")
            pdf_bytes = pdf_path.read_bytes()
        if on_preview is not None:
            # Hand the converted PDF back as the doc's browser preview —
            # the Ingestor persists it on SourceDoc.preview so the viewer
            # iframes slides instead of showing the download notice.
            on_preview(pdf_bytes, "application/pdf")
        return pdf_pages_to_documents(
            pdf_bytes,
            filename=filename,
            mime=mime,
            describer=self._describer,
            on_progress=on_progress,
            sparse_text_threshold=self._sparse_text_threshold,
            parser_label="PptxParser",
            page_word="slide",
        )
