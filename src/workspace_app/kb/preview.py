"""Per-type "file view" projections for the doc viewer (issue #39).

`render_document` used to utf-8-decode EVERY doc's blob into the
markdown body — correct when ingest was text-only, mojibake once
store-all kept images / PDFs / office files. This module owns the
decision per type:

  - **browser-native** (image / pdf / html): return "" — the FE
    renders the original bytes from the blob endpoint (`<img>`,
    `<iframe>`); shipping a text body would be garbage.
  - **structured text** (json / csv / tsv / xlsx / docx): project into
    markdown the existing ReactMarkdown view renders — fenced code for
    JSON, GFM tables for tabular, extracted text for docx.
  - **text / markdown / code**: the decoded body, as before.
  - **undisplayable binary** (pptx, unknown): "" — the FE shows the
    download notice; the chunks tab still shows what got indexed.

Previews are bounded (`_MAX_TABLE_ROWS` per table) — the viewer is a
peek, the Download button is the full fidelity path.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging

from .ingest import normalize_text

logger = logging.getLogger(__name__)

_MAX_TABLE_ROWS = 200
_MAX_XLSX_ROWS_PER_SHEET = 100

# Types the FE renders natively from `/blobs/{file_id}` — no text body.
_BLOB_NATIVE_MIMES = {"application/pdf", "text/html"}
_BLOB_NATIVE_EXTENSIONS = (".pdf", ".html", ".htm")

_CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx")


def preview_markdown(*, path: str, content_type: str, raw: bytes) -> str:
    """The markdown body the doc viewer shows for this document, or ""
    when the FE should render (or refuse) the blob itself."""
    p = path.lower()
    ct = content_type
    if ct.startswith("image/"):
        return ""
    if ct in _BLOB_NATIVE_MIMES or p.endswith(_BLOB_NATIVE_EXTENSIONS):
        return ""
    if ct == "application/json" or p.endswith((".json", ".jsonl")):
        return _json_preview(raw)
    if ct == "text/csv" or p.endswith((".csv", ".tsv")):
        return _table_preview(raw, delimiter="\t" if p.endswith(".tsv") else ",")
    if p.endswith(".xlsx"):
        return _xlsx_preview(raw)
    if p.endswith(".docx"):
        return _docx_preview(raw)
    if ct.startswith("text/") or p.endswith(_CODE_EXTENSIONS):
        return normalize_text(raw.decode("utf-8", errors="replace"))
    # Undisplayable binary (pptx, unknown) — FE shows the download notice.
    return ""


def _json_preview(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    # jsonl / malformed stay as-is inside the fence.
    with contextlib.suppress(json.JSONDecodeError):
        text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    return f"```json\n{text}\n```"


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


def _table_preview(raw: bytes, *, delimiter: str) -> str:
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return ""
    rows = list(reader)
    shown = rows[:_MAX_TABLE_ROWS]
    return _md_table(header, shown, omitted=len(rows) - len(shown))


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
