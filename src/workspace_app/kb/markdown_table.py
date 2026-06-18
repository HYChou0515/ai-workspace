"""Deterministic GFM-table parsing for table-aware chunking (issue #116).

A small, dependency-free reader: find GitHub-flavoured Markdown tables in a
block of text and expose each table's header, rows, and exact char span. The
span lets a row chunk embed a `col: value` representation while its start/end
still point at the table in the canonical text (so the structural parent-doc
merge re-extracts the whole table and citations stay valid).

We do NOT validate or repair here — ragged rows are returned as-is; the caller
decides how to render/serialize them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A separator cell: optional leading/trailing colon around one-or-more dashes
# (e.g. `---`, `:--`, `--:`, `:-:`). A separator ROW is all such cells.
_SEP_CELL = re.compile(r"^:?-+:?$")


@dataclass
class MarkdownTable:
    """A parsed GFM table and its char span into the source text."""

    header: list[str]
    rows: list[list[str]]
    start: int  # inclusive char offset of the header line
    end: int  # exclusive char offset at the end of the last data row line


def row_as_col_value(header: list[str], row: list[str]) -> str:
    """Serialize one row as `col: value` lines so the column names travel with
    every value — the representation the CSV/Excel path already embeds. Pairs
    by position; the caller ensures `len(row) == len(header)` (ragged rows are
    handled upstream)."""
    return "\n".join(f"{h}: {v}".rstrip() for h, v in zip(header, row, strict=False))


def _split_row(line: str) -> list[str]:
    """Split one `| a | b |` line into stripped cells, dropping the empty
    cells produced by leading/trailing pipes (middle empties are kept — an
    empty value is meaningful)."""
    cells = [c.strip() for c in line.split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_table_row(line: str) -> bool:
    return "|" in line


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    return len(cells) > 0 and all(_SEP_CELL.match(c) for c in cells)


def find_markdown_tables(text: str) -> list[MarkdownTable]:
    """Return every GFM table in `text` — a header row, a separator row, then
    one or more data rows — with the char span of the whole table block."""
    # Char offset of the start of each line (line i spans [offsets[i], …)).
    offsets: list[int] = []
    pos = 0
    lines = text.splitlines(keepends=True)
    for ln in lines:
        offsets.append(pos)
        pos += len(ln)
    stripped = [ln.rstrip("\n") for ln in lines]

    tables: list[MarkdownTable] = []
    i = 0
    n = len(stripped)
    while i < n:
        # A table needs a header row immediately followed by a separator row.
        if i + 1 < n and _is_table_row(stripped[i]) and _is_separator(stripped[i + 1]):
            header = _split_row(stripped[i])
            j = i + 2
            rows: list[list[str]] = []
            while j < n and _is_table_row(stripped[j]) and not _is_separator(stripped[j]):
                rows.append(_split_row(stripped[j]))
                j += 1
            last = j - 1  # index of the last data row line
            start = offsets[i]
            end = offsets[last] + len(stripped[last])
            tables.append(MarkdownTable(header=header, rows=rows, start=start, end=end))
            i = j
        else:
            i += 1
    return tables
