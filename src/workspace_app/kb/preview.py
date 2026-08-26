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
from collections.abc import Callable

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


def once(fn: Callable[[], bytes]) -> Callable[[], bytes]:
    """Call `fn` at most once, then hand back what it returned.

    Named and exported rather than inlined as a closure so it can be PROBED. As
    a closure inside `preview_markdown` the memoisation was unreachable from any
    test: no branch there calls it twice, so deleting the memoisation changed no
    observable behaviour and the guard for it passed either way. A guarantee that
    cannot fail a test is not a guarantee.

    It earns its place because `preview_markdown`'s whole reason for taking a
    callable is that fetching the bytes is expensive (#730): a future branch that
    reads twice would silently pay twice, which is the defect that change removed.
    """
    cached: list[bytes] = []

    def read() -> bytes:
        if not cached:
            cached.append(fn())
        return cached[0]

    return read


def preview_markdown(*, path: str, content_type: str, raw: bytes | Callable[[], bytes]) -> str:
    """The body the doc viewer shows for this document, or "" when the FE
    should render (or refuse) the blob itself. Structured-data types return
    verbatim decoded text (the FE builds the tree/grid); xlsx/docx are still
    projected to markdown here.

    ``raw`` may be a CALLABLE, and a caller for whom fetching the bytes is
    expensive should pass one: three of the branches below never look at them.
    An image or a PDF is rendered by the browser from its own blob, and an
    unrecognised binary gets a download notice — so restoring the blob to answer
    "" meant the bytes were pulled once here, dropped, and then fetched again by
    the browser. For a photograph that was the ten seconds a person spent
    waiting (#730).

    Deliberately NOT a second `needs_bytes(path, content_type)` predicate beside
    this function: two copies of one branch table drift, and the copy that
    decides whether to do the expensive thing would be the one nobody re-reads.
    Asking for the bytes IS the condition.
    """
    p = path.lower()
    ct = content_type
    # `isinstance(bytes)` rather than `callable(...)`: the latter narrows to a
    # top callable whose signature is unknown, which a type checker cannot verify
    # at the call sites below.
    fetch: Callable[[], bytes] = (lambda data=raw: data) if isinstance(raw, bytes) else raw
    read = once(fetch)

    if ct.startswith("image/"):
        return ""
    if ct in _BLOB_NATIVE_MIMES or p.endswith(_BLOB_NATIVE_EXTENSIONS):
        return ""
    if is_structured_text(path, content_type):
        # FE renders the tree/grid from this — hand back the verbatim text.
        return normalize_text(read().decode("utf-8", errors="replace"))
    if p.endswith(".xlsx"):
        return _xlsx_preview(read())
    if p.endswith(".docx"):
        return _docx_preview(read())
    if ct.startswith("text/") or is_code_file(p):
        return normalize_text(read().decode("utf-8", errors="replace"))
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
