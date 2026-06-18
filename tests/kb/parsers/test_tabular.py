"""CsvParser + ExcelParser — issue #39 P8.

Locked decisions (docs/plan-kb-parsers.md §2-P8, research-corrected):
  - SOTA for tables: one row = one Document, rendered as
    ``column: value`` lines so the column names travel with every row
    (bare comma-joined values are an anti-pattern — the embedding
    loses what each value means).
  - CsvParser wraps LlamaIndex ``PagedCSVReader`` (already does
    exactly that); ``.tsv`` rides the same reader via
    ``delimiter="\\t"``.
  - ExcelParser renders the same paged shape via pandas + openpyxl;
    ``sheet_name=None`` reads ALL sheets; sheet name prepended as
    context when the workbook has more than one sheet.
"""

from __future__ import annotations

import io

import pytest

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.tabular import CsvParser, ExcelParser


def _input(data: bytes, filename: str = "x.csv") -> MaterialisedParserInput:
    return MaterialisedParserInput(data, filename=filename)


def _xlsx(sheets: dict[str, list[dict[str, object]]]) -> bytes:
    """Build a real .xlsx in memory (openpyxl is a direct test dep —
    the parser itself reads via pandas)."""
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:  # ty: ignore[invalid-argument-type]
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


# ── CsvParser.matches ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("t.csv", "text/csv", True),
        ("t.csv", "text/plain", True),  # magic often sniffs csv as text
        ("t.tsv", "text/plain", True),
        ("noext", "text/csv", True),
        ("t.txt", "text/plain", False),
        ("t.xlsx", "application/zip", False),  # Excel is the other parser's
    ],
)
def test_csv_matches(filename: str, mime: str, expected: bool):
    p = CsvParser()
    assert p.matches(filename=filename, mime=mime, source=_input(b"a,b\n1,2\n")) is expected


# ── CsvParser.parse ──────────────────────────────────────────────────


def test_csv_one_row_one_document_with_column_names():
    """Every row becomes its own Document carrying `column: value`
    lines — the header travels with EVERY row, so any chunk the
    splitter produces still knows what its values mean."""
    data = b"name,email\nBob,bob@x.com\nAmy,amy@x.com\n"
    docs = list(CsvParser().parse(_input(data), filename="users.csv", mime="text/csv"))
    assert len(docs) == 2
    assert docs[0].text == "name: Bob\nemail: bob@x.com"
    assert docs[1].text == "name: Amy\nemail: amy@x.com"
    assert docs[0].metadata["filename"] == "users.csv"


def test_tsv_rides_the_same_parser_via_tab_delimiter():
    data = b"name\temail\nBob\tbob@x.com\n"
    docs = list(CsvParser().parse(_input(data, "u.tsv"), filename="u.tsv", mime="text/plain"))
    assert len(docs) == 1
    assert docs[0].text == "name: Bob\nemail: bob@x.com"


# ── ExcelParser.matches ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("t.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True),
        ("t.xlsx", "application/zip", True),  # magic may only see the zip container
        ("noext", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True),
        ("t.csv", "text/csv", False),
        ("t.docx", "application/zip", False),
    ],
)
def test_excel_matches(filename: str, mime: str, expected: bool):
    p = ExcelParser()
    assert p.matches(filename=filename, mime=mime, source=_input(b"PK", filename)) is expected


# ── ExcelParser.parse ────────────────────────────────────────────────


def test_excel_single_sheet_rows_become_paged_documents():
    data = _xlsx({"Sheet1": [{"name": "Bob", "email": "bob@x.com"}]})
    docs = list(
        ExcelParser().parse(_input(data, "u.xlsx"), filename="u.xlsx", mime="application/zip")
    )
    assert len(docs) == 1
    # Single-sheet workbook: no sheet prefix noise.
    assert docs[0].text == "name: Bob\nemail: bob@x.com"


def test_excel_multi_sheet_rows_carry_sheet_context():
    data = _xlsx(
        {
            "People": [{"name": "Bob"}],
            "Tools": [{"tool": "etcher"}, {"tool": "stepper"}],
        }
    )
    docs = list(
        ExcelParser().parse(_input(data, "fab.xlsx"), filename="fab.xlsx", mime="application/zip")
    )
    assert len(docs) == 3
    # Sheet name prepended so retrieval can distinguish same-shaped rows
    # across sheets; also kept in metadata for citation labelling.
    assert docs[0].text == "sheet: People\nname: Bob"
    assert docs[0].metadata["sheet"] == "People"
    assert docs[1].text == "sheet: Tools\ntool: etcher"
    assert docs[2].metadata["sheet"] == "Tools"
