"""`kb.preview.preview_markdown` — the doc viewer's per-type "file"
representation (issue #39 follow-up: every ingestable type must render
in the FE, not just text).

The contract per type:
  - browser-native types (image / pdf / html) → "" — the FE renders
    them from the blob endpoint (<img> / <iframe>) instead.
  - structured text (json / jsonl / csv / tsv / yaml) → the verbatim
    decoded text (#361); the FE projects it into a collapsible tree /
    data grid client-side, so no server-side markdown projection.
  - office (xlsx / docx) → a markdown projection the existing
    ReactMarkdown view renders (GFM tables, extracted text).
  - text/markdown/code → the decoded body (pre-existing behaviour).
  - undisplayable binary (pptx, unknown) → "" — FE shows the download
    notice; the chunks tab still shows what got indexed.
"""

from __future__ import annotations

import contextlib
import io

from workspace_app.kb.preview import is_structured_text, once, preview_markdown


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


# ── structured text → verbatim decoded text (FE renders tree/grid, #361) ──


def test_json_returns_verbatim_text_not_a_fenced_projection():
    raw = b'{"users":[{"name":"Bob"}]}'
    md = preview_markdown(path="u.json", content_type="application/json", raw=raw)
    # No server-side pretty-print / fence — the FE builds the tree from this.
    assert md == '{"users":[{"name":"Bob"}]}'
    assert "```" not in md


def test_invalid_json_still_returns_the_raw_bytes_verbatim():
    md = preview_markdown(path="u.json", content_type="text/plain", raw=b"{broken")
    assert md == "{broken"  # the FE's JsonTreeView degrades to raw text itself


def test_jsonl_returns_verbatim_text():
    raw = b'{"a":1}\n{"b":2}\n'
    md = preview_markdown(path="events.jsonl", content_type="text/plain", raw=raw)
    assert md == '{"a":1}\n{"b":2}\n'


def test_ndjson_is_treated_as_jsonl():
    raw = b'{"a":1}\n'
    md = preview_markdown(path="events.ndjson", content_type="application/octet-stream", raw=raw)
    assert md == '{"a":1}\n'


def test_csv_returns_verbatim_text_not_a_gfm_table():
    raw = b"name,email\nBob,bob@x.com\n"
    md = preview_markdown(path="u.csv", content_type="text/csv", raw=raw)
    assert md == "name,email\nBob,bob@x.com\n"
    assert "|" not in md  # no markdown table


def test_tsv_returns_verbatim_text():
    raw = b"name\temail\nBob\tbob@x.com\n"
    md = preview_markdown(path="u.tsv", content_type="text/plain", raw=raw)
    assert md == "name\temail\nBob\tbob@x.com\n"


def test_yaml_returns_verbatim_text():
    raw = b"name: widget\nqty: 3\n"
    want = "name: widget\nqty: 3\n"
    assert preview_markdown(path="c.yaml", content_type="text/plain", raw=raw) == want
    assert preview_markdown(path="c.yml", content_type="application/octet-stream", raw=raw) == want


def test_structured_text_normalizes_crlf():
    md = preview_markdown(path="u.csv", content_type="text/csv", raw=b"a,b\r\n1,2\r\n")
    assert md == "a,b\n1,2\n"


def test_is_structured_text_matches_by_extension_and_mime():
    assert is_structured_text("x.json", "text/plain")
    assert is_structured_text("x.jsonl", "text/plain")
    assert is_structured_text("x.ndjson", "application/octet-stream")
    assert is_structured_text("x.csv", "text/plain")
    assert is_structured_text("x.tsv", "text/plain")
    assert is_structured_text("x.yaml", "text/plain")
    assert is_structured_text("x.yml", "text/plain")
    # mime-only match (extension not recognised) still routes structured.
    assert is_structured_text("noext", "application/json")
    assert is_structured_text("noext", "text/csv")
    # markdown / plain text are NOT structured (they keep link-rewriting).
    assert not is_structured_text("x.md", "text/markdown")
    assert not is_structured_text("x.txt", "text/plain")


def test_xlsx_caps_rows_per_sheet_and_says_how_many_were_omitted():
    raw = _xlsx({"Big": [{"n": i} for i in range(101)]})  # 101 rows, cap is 100
    md = preview_markdown(path="big.xlsx", content_type="application/zip", raw=raw)
    assert "| 0 |" in md
    assert "| 100 |" not in md  # capped at 100 shown
    assert "more rows" in md  # the omitted-rows notice (1 omitted)


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


def test_once_calls_through_exactly_one_time():
    """#730: `preview_markdown` takes a callable because the fetch is expensive.

    Probed HERE rather than through `preview_markdown`, because no branch there
    reads twice — so deleting the memoisation changes nothing that function can
    show, and the first version of this test passed with it removed.
    """
    calls = []

    def fetch() -> bytes:
        calls.append(1)
        return b"payload"

    read = once(fetch)
    assert read() == b"payload"
    assert read() == b"payload"
    assert read() == b"payload"
    assert calls == [1], f"the expensive call was made {len(calls)} times"


def test_once_caches_an_EMPTY_result_too():
    """The classic memoisation bug: `if not cached: cached = fn()` re-fetches
    forever when `fn` legitimately returns b"" — an empty file is a real
    document, and it is the cheapest one to fetch twice by accident."""
    calls = []

    def fetch() -> bytes:
        calls.append(1)
        return b""

    read = once(fetch)
    assert read() == b""
    assert read() == b""
    assert calls == [1], "an empty result was re-fetched"


def test_once_does_not_cache_a_failure():
    """A raise is not a result. Caching one would turn a transient blob-store
    error into a permanently broken document for the life of the call."""
    calls = []

    def fetch() -> bytes:
        calls.append(1)
        raise RuntimeError("blob store said no")

    read = once(fetch)
    for _ in range(2):
        with contextlib.suppress(RuntimeError):
            read()
    assert calls == [1, 1], "a failure was cached instead of retried"


def test_once_is_lazy_until_asked():
    """Never asked ⇒ never fetched. That is the half that saves the ten seconds."""
    calls = []
    once(lambda: (calls.append(1), b"x")[1])
    assert calls == []


def test_preview_markdown_fetches_a_lazy_blob_for_a_projected_type():
    calls = []

    def fetch() -> bytes:
        calls.append(1)
        return b"# hi"

    assert preview_markdown(path="a.md", content_type="text/markdown", raw=fetch) == "# hi"
    assert calls == [1]


def test_preview_markdown_never_fetches_for_a_type_the_browser_renders():
    """The other half: an image must not touch the blob at all."""
    calls = []

    def fetch() -> bytes:  # pragma: no cover - must never run
        calls.append(1)
        return b""

    assert preview_markdown(path="a.png", content_type="image/png", raw=fetch) == ""
    assert preview_markdown(path="a.pdf", content_type="application/pdf", raw=fetch) == ""
    assert preview_markdown(path="x.bin", content_type="application/octet-stream", raw=fetch) == ""
    assert calls == [], "the bytes were fetched for a type that never reads them"
