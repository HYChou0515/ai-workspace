"""CsvParser + ExcelParser — bundled tabular IParsers (issue #39 P8).

SOTA table ingest (see docs/plan-kb-parsers.md research notes): one row
= one Document rendered as ``column: value`` lines, so the column names
travel with every row. Bare comma-joined values lose what each value
means and embed badly; fixed-token windows shred rows and orphan the
header. Each row-Document is small enough that the downstream
SentenceSplitter keeps it intact (one row = one chunk in practice).

- ``CsvParser`` wraps LlamaIndex's ``PagedCSVReader`` (which produces
  exactly this shape); ``.tsv`` rides the same reader via
  ``delimiter="\\t"``.
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

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
    ) -> list[Document]:
        from llama_index.readers.file import PagedCSVReader

        delimiter = "\t" if filename.lower().endswith(".tsv") else ","
        docs = PagedCSVReader().load_data(file=source.as_path(), delimiter=delimiter)
        for d in docs:
            d.metadata.setdefault("filename", filename)
            d.metadata.setdefault("mime", mime)
        return docs


class ExcelParser(IParser):
    """``.xlsx`` → one Document per row across ALL sheets, ``column:
    value`` lines; multi-sheet workbooks prepend ``sheet: <name>``."""

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        # xlsx is a zip container — libmagic may report either the
        # office mime or bare application/zip, so the extension check
        # is the reliable signal.
        return mime == _XLSX_MIME or filename.lower().endswith(".xlsx")

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
    ) -> list[Document]:
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
