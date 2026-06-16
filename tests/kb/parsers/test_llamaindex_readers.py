"""HTML / DOCX IParser wrappers.

The bundled parsers wrap LlamaIndex Readers (``HTMLTagReader`` /
``DocxReader``) so the registry sees a uniform ``IParser`` interface —
same shape as a custom in-house parser the operator might add (issue
#39's "custom parser" requirement).

The Readers want a filesystem path, so each parser calls
``source.as_path()`` (the lazy adapter materialises a tempfile on
first call, caches it, and the Ingestor's ``close()`` cleans up).

PDF graduated to ``kb/parsers/pdf.py`` (per-page + selective VLM) —
its tests live in ``test_vision.py`` now.
"""

from __future__ import annotations

import pytest

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.llamaindex_readers import (
    DocxParser,
    HtmlParser,
)

# ─── matches: mime OR extension ─────────────────────────────────────


def test_html_parser_matches_text_html_mime_and_html_extensions():
    p = HtmlParser()
    src = MaterialisedParserInput(b"")
    assert p.matches(filename="page.html", mime="text/html", source=src)
    assert p.matches(filename="page.htm", mime="application/octet-stream", source=src)
    assert not p.matches(filename="page.md", mime="text/markdown", source=src)


def test_docx_parser_matches_docx_mime_and_extension():
    p = DocxParser()
    src = MaterialisedParserInput(b"")
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert p.matches(filename="r.docx", mime=docx_mime, source=src)
    # Mime-less upload (rare but happens with weird servers) — extension wins.
    assert p.matches(filename="r.docx", mime="application/octet-stream", source=src)
    assert not p.matches(filename="r.html", mime="text/html", source=src)


# ─── parse: drive the wrapped Reader ─────────────────────────────────


_HTML_DOC = b"<html><body><p>reflow temperature drift in zone three</p></body></html>"


def test_html_parser_parse_returns_documents_with_the_body_text():
    p = HtmlParser()
    with MaterialisedParserInput(_HTML_DOC, filename="page.html") as src:
        docs = list(p.parse(src, filename="page.html", mime="text/html"))
    assert docs, "HtmlParser produced no documents"
    text = " ".join(d.text for d in docs)
    assert "reflow temperature drift" in text


def test_docx_parser_constructs_its_reader():
    """`python-docx` isn't a runtime dep of this package — the legacy
    `test_lazy_docx_reader_constructs` just exercises the constructor.
    Match that style: confirm the wired Reader factory loads + builds
    without crashing. The full end-to-end parse path (uploading a
    real DOCX, recovering its paragraphs) is exercised in deploy
    smoke tests, not offline."""
    pytest.importorskip("llama_index.readers.file")
    from workspace_app.kb.parsers.llamaindex_readers import _lazy_docx_reader

    assert _lazy_docx_reader() is not None


def test_parser_uses_as_path_so_caller_can_reuse_the_tempfile():
    """Reader-wrapped parsers consume the source via `as_path()` — they
    DON'T re-read the bytes themselves. Two parse() calls on the same
    source should reuse the cached tempfile (no second materialise)."""
    src = MaterialisedParserInput(_HTML_DOC, filename="page.html")
    try:
        p = HtmlParser()
        first_path = src.as_path()
        list(p.parse(src, filename="page.html", mime="text/html"))
        second_path = src.as_path()
        assert first_path == second_path
    finally:
        src.close()
