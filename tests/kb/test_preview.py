"""`kb.preview.preview_markdown` — the doc viewer's per-type "file"
representation (issue #39 follow-up: every ingestable type must render
in the FE, not just text).

The contract per type:
  - browser-native types (image / pdf / html) → "" — the FE renders
    them from the blob endpoint (<img> / <iframe>) instead.
  - structured text (json / csv / tsv / xlsx / docx) → a markdown
    projection the existing ReactMarkdown view renders (fenced code,
    GFM tables, plain text).
  - text/markdown/code → the decoded body (pre-existing behaviour).
  - undisplayable binary (pptx, unknown) → "" — FE shows the download
    notice; the chunks tab still shows what got indexed.
"""

from __future__ import annotations

import io

from workspace_app.kb.preview import preview_markdown


def _xlsx(sheets: dict[str, list[dict[str, object]]]) -> bytes:
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:  # ty: ignore[invalid-argument-type]
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


# ── browser-native → empty (FE renders from the blob) ───────────────


def test_image_pdf_html_and_pptx_yield_empty_preview():
    assert preview_markdown(path="a.png", content_type="image/png", raw=b"\x89PNG") == ""
    assert preview_markdown(path="a.pdf", content_type="application/pdf", raw=b"%PDF") == ""
    assert preview_markdown(path="a.html", content_type="text/html", raw=b"<html/>") == ""
    assert preview_markdown(path="deck.pptx", content_type="application/zip", raw=b"PK") == ""
    assert preview_markdown(path="blob.bin", content_type="application/o", raw=b"\x00") == ""


# ── structured text → markdown projections ──────────────────────────


def test_json_pretty_prints_into_a_fenced_block():
    raw = b'{"users":[{"name":"Bob"}]}'
    md = preview_markdown(path="u.json", content_type="application/json", raw=raw)
    assert md.startswith("```json\n")
    assert '"name": "Bob"' in md  # pretty-printed, not the minified original
    assert md.rstrip().endswith("```")


def test_invalid_json_falls_back_to_raw_text_in_the_fence():
    md = preview_markdown(path="u.json", content_type="text/plain", raw=b"{broken")
    assert "{broken" in md and md.startswith("```json\n")


def test_csv_renders_a_gfm_table_with_headers():
    raw = b"name,email\nBob,bob@x.com\nAmy,amy@x.com\n"
    md = preview_markdown(path="u.csv", content_type="text/csv", raw=raw)
    assert "| name | email |" in md
    assert "| --- | --- |" in md
    assert "| Bob | bob@x.com |" in md


def test_tsv_renders_the_same_table_via_tab_delimiter():
    raw = b"name\temail\nBob\tbob@x.com\n"
    md = preview_markdown(path="u.tsv", content_type="text/plain", raw=raw)
    assert "| name | email |" in md and "| Bob | bob@x.com |" in md


def test_csv_caps_rows_and_says_how_many_were_omitted():
    rows = "\n".join(f"r{i},v{i}" for i in range(500))
    raw = f"a,b\n{rows}\n".encode()
    md = preview_markdown(path="big.csv", content_type="text/csv", raw=raw)
    assert "| r0 | v0 |" in md
    assert "| r499 | v499 |" not in md  # capped
    assert "300 more rows" in md  # 500 - 200 shown


def test_csv_cells_escape_pipes():
    raw = b"a,b\nx|y,z\n"
    md = preview_markdown(path="p.csv", content_type="text/csv", raw=raw)
    assert r"x\|y" in md


def test_xlsx_renders_one_table_per_sheet_with_sheet_headings():
    raw = _xlsx(
        {
            "People": [{"name": "Bob"}],
            "Tools": [{"tool": "etcher"}],
        }
    )
    md = preview_markdown(path="fab.xlsx", content_type="application/zip", raw=raw)
    assert "## People" in md and "## Tools" in md
    assert "| name |" in md and "| Bob |" in md
    assert "| tool |" in md and "| etcher |" in md


def test_docx_extracts_text():
    # Build a real minimal docx via the zip layout docx2txt understands.
    import zipfile

    import docx2txt  # noqa: F401 — dep present (runtime requirement for DocxParser too)

    ct_xml = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    doc_xml = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>solder voids exceed spec</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", ct_xml)
        z.writestr("word/document.xml", doc_xml)
    md = preview_markdown(path="r.docx", content_type="application/zip", raw=buf.getvalue())
    assert "solder voids exceed spec" in md


# ── plain text path unchanged ────────────────────────────────────────


def test_markdown_and_plain_text_decode_as_before():
    md = preview_markdown(path="n.md", content_type="text/markdown", raw=b"# Hi\r\nbody")
    assert md == "# Hi\nbody"  # normalize_text applied
    txt = preview_markdown(path="n.txt", content_type="text/plain", raw=b"plain body")
    assert txt == "plain body"


def test_code_files_decode_by_extension_even_with_x_mime():
    py = preview_markdown(path="a.py", content_type="text/x-script.python", raw=b"def f(): ...")
    assert py == "def f(): ..."
