"""Deterministic GFM-table parsing for table-aware chunking (issue #116).

The table-aware splitter relies on finding Markdown tables in (VLM-produced)
text and reading their header + rows + exact char span — so a row chunk can
embed `col: value` while its start/end still point at the table in the
canonical text. These tests pin that pure parsing layer.
"""

from __future__ import annotations

from workspace_app.kb.markdown_table import find_markdown_tables, row_as_col_value


def test_find_markdown_tables_extracts_header_rows_and_span():
    text = (
        "Some intro prose.\n\n"
        "| week | yield |\n"
        "| --- | --- |\n"
        "| W1 | 92% |\n"
        "| W2 | 95% |\n\n"
        "Trailing prose.\n"
    )
    tables = find_markdown_tables(text)
    assert len(tables) == 1
    t = tables[0]
    assert t.header == ["week", "yield"]
    assert t.rows == [["W1", "92%"], ["W2", "95%"]]
    # The span re-slices to exactly the table block (valid canonical offsets).
    sliced = text[t.start : t.end]
    assert sliced.startswith("| week | yield |")
    assert sliced.rstrip().endswith("| W2 | 95% |")
    assert "intro prose" not in sliced and "Trailing prose" not in sliced


def test_find_markdown_tables_handles_tables_without_outer_pipes():
    """GFM allows tables without leading/trailing pipes — cells must parse
    without spurious empty columns from the missing border pipes."""
    text = "week | yield\n--- | ---\nW1 | 92%\nW2 | 95%\n"
    tables = find_markdown_tables(text)
    assert len(tables) == 1
    assert tables[0].header == ["week", "yield"]
    assert tables[0].rows == [["W1", "92%"], ["W2", "95%"]]


def test_row_as_col_value_pairs_each_column_name_with_its_cell():
    """Each row embeds as `col: value` lines so the column names travel with
    every value (the serialization the CSV path already uses)."""
    assert row_as_col_value(["week", "yield"], ["W1", "92%"]) == "week: W1\nyield: 92%"
    # An empty (but present) value keeps its column.
    assert row_as_col_value(["a", "b"], ["x", ""]) == "a: x\nb:"
