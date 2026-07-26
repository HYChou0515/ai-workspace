"""CsvParser + ExcelParser — bundled tabular IParsers (issue #39 P8).

SOTA table ingest (see docs/plan-kb-parsers.md research notes): one row
= one Document rendered as ``column: value`` lines, so the column names
travel with every row. Bare comma-joined values lose what each value
means and embed badly; fixed-token windows shred rows and orphan the
header. Each row-Document is small enough that the downstream
SentenceSplitter keeps it intact (one row = one chunk in practice).

- ``CsvParser`` walks the rows itself with the stdlib ``csv`` module;
  ``.tsv`` rides the same code with ``delimiter="\\t"``. It used to wrap
  LlamaIndex's ``PagedCSVReader``, which produces this shape but dies on
  any row whose field count differs from the header (#646) — see
  ``CsvParser._rows``.
- ``ExcelParser`` renders the same paged shape itself via pandas
  (openpyxl engine) — there is no PagedExcelReader, and the stock
  ``PandasExcelReader`` drops the header row (the anti-pattern above).
  ``sheet_name=None`` reads ALL sheets; rows carry a ``sheet: <name>``
  context line when the workbook has more than one sheet.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document

_CSV_MIMES = {"text/csv"}
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class CsvParser(IParser):
    """``.csv`` / ``.tsv`` → one Document per row, ``column: value``
    lines (PagedCSVReader)."""

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        # Extension matters: libmagic usually sniffs csv/tsv as text/plain.
        return mime in _CSV_MIMES or filename.lower().endswith((".csv", ".tsv"))

    def _rows(self, source: IParserInput, *, filename: str, mime: str) -> list[Document]:
        """One Document per data row, ``column: value`` lines.

        Walks the rows here instead of through ``PagedCSVReader`` (#646). That
        reader formats every cell as ``f"{k.strip()}: {v.strip()}"`` over
        ``csv.DictReader``, whose two padding behaviours both produce something
        that is not a ``str``: a SHORT row fills missing columns with ``restval``
        (``None``), and a LONG row parks the extras under the ``None`` key as a
        list. Either one raised ``AttributeError: 'NoneType' object has no
        attribute 'strip'`` — which ``ingest`` then surfaced verbatim on the doc
        row — and it aborted the WHOLE file, so one truncated line cost the other
        thousands of rows their index. Ragged CSVs are ordinary: a cut-short
        export, a hand-edited file, a trailing partial line.

        So: a short row's missing columns read blank, and a long row's extra
        fields are kept and named by position, because they are data the file
        really contains.
        """
        import csv

        from llama_index.core.schema import Document

        delimiter = "\t" if filename.lower().endswith(".tsv") else ","
        docs: list[Document] = []
        with open(source.as_path(), newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            header = next(reader, None)
            if header is None:  # an empty file has no rows to index
                return docs
            names = [name.strip() for name in header]
            for row in reader:
                if not row:  # a blank line is not a row (DictReader skipped these too)
                    continue
                lines = [
                    f"{name}: {(row[i] if i < len(row) else '').strip()}"
                    for i, name in enumerate(names)
                ]
                lines += [f"column {i + 1}: {row[i].strip()}" for i in range(len(names), len(row))]
                docs.append(
                    Document(text="\n".join(lines), metadata={"filename": filename, "mime": mime})
                )
        return docs

    def count_units(self, source: IParserInput, *, filename: str, mime: str) -> int:
        """Data-row count (#227) — one row = one unit, so a huge CSV fans
        out into many small embed jobs (the parse is cheap; the embed of
        thousands of row-Documents is the long pole that must be split)."""
        return len(self._rows(source, filename=filename, mime=mime))

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
        docs = self._rows(source, filename=filename, mime=mime)
        if unit_range is not None:
            docs = docs[unit_range[0] : unit_range[1]]
        return docs


class ExcelParser(IParser):
    """``.xlsx`` → one Document per row across ALL sheets, ``column:
    value`` lines; multi-sheet workbooks prepend ``sheet: <name>``."""

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        # xlsx is a zip container — libmagic may report either the
        # office mime or bare application/zip, so the extension check
        # is the reliable signal.
        return mime == _XLSX_MIME or filename.lower().endswith(".xlsx")

    def _rows(self, source: IParserInput, *, filename: str, mime: str) -> list[Document]:
        """Every row across ALL sheets, in sheet order — one Document each,
        numbered globally so a fan-out unit_range can straddle sheets."""
        import pandas as pd
        from llama_index.core.schema import Document

        sheets = pd.read_excel(source.as_path(), sheet_name=None, engine="openpyxl")
        multi = len(sheets) > 1
        docs: list[Document] = []
        for sheet_name, df in sheets.items():
            df = df.fillna("")
            for _, row in df.iterrows():
                lines = [f"{col}: {row[col]}" for col in df.columns]
                if multi:
                    lines.insert(0, f"sheet: {sheet_name}")
                docs.append(
                    Document(
                        text="\n".join(lines),
                        metadata={"filename": filename, "mime": mime, "sheet": sheet_name},
                    )
                )
        return docs

    def count_units(self, source: IParserInput, *, filename: str, mime: str) -> int:
        """Total data rows across every sheet (#227) — one row = one unit."""
        return len(self._rows(source, filename=filename, mime=mime))

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
        docs = self._rows(source, filename=filename, mime=mime)
        if unit_range is not None:
            docs = docs[unit_range[0] : unit_range[1]]
        return docs
