"""Per-type "file view" projections for the doc viewer (issue #39, #361).

`render_document` used to utf-8-decode EVERY doc's blob into the
markdown body — correct when ingest was text-only, mojibake once
store-all kept images / PDFs / office files. This module owns the
decision per type:

  - **browser-native** (image / pdf / html): return "" — the FE
    renders the original bytes from the blob endpoint (`<img>`,
    `<iframe>`); shipping a text body would be garbage.
  - **structured text** (json / jsonl / csv / tsv / yaml): return the
    verbatim decoded text — the FE projects it into a collapsible tree /
    data grid client-side (#361), so no server-side markdown projection.
    `is_structured_text` marks them so the caller skips markdown-link
    rewriting (they aren't markdown).
  - **office (xlsx / docx)**: still projected server-side — xlsx into
    per-sheet GFM tables, docx into extracted text (binary formats the FE
    can't parse without a heavy dep).
  - **text / markdown / code**: the decoded body, as before.
  - **undisplayable binary** (pptx, unknown): "" — the FE shows the
    download notice; the chunks tab still shows what got indexed.

Office previews are bounded (`_MAX_XLSX_ROWS_PER_SHEET`) — the viewer is
a peek, the Download button is the full fidelity path.
"""

from __future__ import annotations

import io
import logging

from .code_lang import is_code_file
from .ingest import normalize_text

logger = logging.getLogger(__name__)

_MAX_XLSX_ROWS_PER_SHEET = 100

# Types the FE renders natively from `/blobs/{file_id}` — no text body.
_BLOB_NATIVE_MIMES = {"application/pdf", "text/html"}
_BLOB_NATIVE_EXTENSIONS = (".pdf", ".html", ".htm")

# Structured-data text the FE renders itself (#361): the doc viewer projects
# these into a collapsible tree / data grid, so the BE returns verbatim text.
_STRUCTURED_TEXT_EXTENSIONS = (".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".yaml", ".yml")
_STRUCTURED_TEXT_MIMES = {"application/json", "text/csv"}


def is_structured_text(path: str, content_type: str) -> bool:
    """A type the FE renders structurally from the raw text (#361). The doc
    viewer must NOT markdown-link-rewrite these — they aren't markdown, and a
    JSON string value that happens to look like a link must stay verbatim."""
    ext_match = path.lower().endswith(_STRUCTURED_TEXT_EXTENSIONS)
    return ext_match or content_type in _STRUCTURED_TEXT_MIMES


def preview_markdown(*, path: str, content_type: str, raw: bytes) -> str:
    """The body the doc viewer shows for this document, or "" when the FE
    should render (or refuse) the blob itself. Structured-data types return
    verbatim decoded text (the FE builds the tree/grid); xlsx/docx are still
    projected to markdown here."""
    p = path.lower()
    ct = content_type
    if ct.startswith("image/"):
        return ""
    if ct in _BLOB_NATIVE_MIMES or p.endswith(_BLOB_NATIVE_EXTENSIONS):
        return ""
    if is_structured_text(path, content_type):
        # FE renders the tree/grid from this — hand back the verbatim text.
        return normalize_text(raw.decode("utf-8", errors="replace"))
    if p.endswith(".xlsx"):
        return _xlsx_preview(raw)
    if p.endswith(".docx"):
        return _docx_preview(raw)
    if ct.startswith("text/") or is_code_file(p):
        return normalize_text(raw.decode("utf-8", errors="replace"))
    # Undisplayable binary (pptx, unknown) — FE shows the download notice.
    return ""


def _md_table(header: list[str], rows: list[list[str]], *, omitted: int) -> str:
    def esc(cell: str) -> str:
        return cell.replace("|", r"\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(esc(c) for c in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(esc(c) for c in row) + " |" for row in rows]
    if omitted > 0:
        lines.append(f"\n_… {omitted} more rows — download the file for the full data._")
    return "\n".join(lines)


def _xlsx_preview(raw: bytes) -> str:
    import pandas as pd

    try:
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, engine="openpyxl")
    except Exception:  # noqa: BLE001 — corrupt upload: viewer falls back to the notice
        logger.warning("xlsx preview failed", exc_info=True)
        return ""
    parts: list[str] = []
    for name, df in sheets.items():
        df = df.fillna("")
        header = [str(c) for c in df.columns]
        rows = [[str(v) for v in rec] for rec in df.itertuples(index=False, name=None)]
        shown = rows[:_MAX_XLSX_ROWS_PER_SHEET]
        parts.append(f"## {name}\n\n" + _md_table(header, shown, omitted=len(rows) - len(shown)))
    return "\n\n".join(parts)


def _docx_preview(raw: bytes) -> str:
    import docx2txt

    # docx2txt wants a path/file-like; BytesIO works.
    try:
        text = docx2txt.process(io.BytesIO(raw))
    except Exception:  # noqa: BLE001 — corrupt upload: viewer falls back to the notice
        logger.warning("docx preview failed", exc_info=True)
        return ""
    return normalize_text(text or "")
