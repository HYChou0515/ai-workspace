"""Issue #254: PDF outline → page→section breadcrumb map (page-level
approximation + nested ancestor path)."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from typing import cast

import pypdf

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.pdf import PdfParser, outline_sections, pdf_pages_to_documents
from workspace_app.kb.vlm import IVlm, VlmDescriber

_RAISE = object()  # sentinel: this bookmark's destination cannot be resolved


class _Dest:
    def __init__(self, title: str, page: object) -> None:
        self.title = title
        self._page = page


class _FakeReader:
    """Duck-typed PdfReader for the outline robustness branches — pypdf can't
    easily be coaxed into broken/unresolvable bookmarks from a real file."""

    def __init__(self, outline: object, npages: int) -> None:
        self._outline = outline
        self.pages = [object()] * npages

    @property
    def outline(self) -> object:
        if self._outline == "BROKEN":
            raise ValueError("broken outline tree")
        return self._outline

    def get_destination_page_number(self, dest: _Dest) -> object:
        if dest._page is _RAISE:
            raise ValueError("unresolvable destination")
        return dest._page


def _sections(outline: object, npages: int) -> dict[int, str]:
    return outline_sections(cast("pypdf.PdfReader", _FakeReader(outline, npages)))


class _FakeVlm(IVlm):
    """Describes every rendered page so blank-but-bookmarked test pages still
    yield Documents (sparse text → VLM path)."""

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        yield "## Figure\n\ndescribed body", False


def _describer() -> VlmDescriber:
    return VlmDescriber(_FakeVlm())


def _pdf_with_outline() -> bytes:
    w = pypdf.PdfWriter()
    for _ in range(5):
        w.add_blank_page(width=200, height=200)
    c1 = w.add_outline_item("Chapter 1", 0)
    w.add_outline_item("1.1 Intro", 1, parent=c1)
    c2 = w.add_outline_item("Chapter 2", 3)
    w.add_outline_item("2.1 Method", 4, parent=c2)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_outline_sections_nested_breadcrumb_and_page_approximation():
    """Each 0-based page maps to the deepest bookmark starting at-or-before it,
    rendered as an ancestor breadcrumb. Pages with no bookmark of their own
    inherit the active section (page 2 stays under 1.1)."""
    r = pypdf.PdfReader(io.BytesIO(_pdf_with_outline()))
    assert outline_sections(r) == {
        0: "Chapter 1",
        1: "Chapter 1 > 1.1 Intro",
        2: "Chapter 1 > 1.1 Intro",
        3: "Chapter 2",
        4: "Chapter 2 > 2.1 Method",
    }


def test_outline_sections_empty_when_no_outline():
    """No bookmarks → empty map → every page's section is None downstream."""
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    r = pypdf.PdfReader(io.BytesIO(buf.getvalue()))
    assert outline_sections(r) == {}


def _no_outline_pdf(pages: int = 2) -> bytes:
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_pages_carry_section_breadcrumb_from_outline():
    """Each per-page Document carries its 1-based ``page`` plus the ``section``
    breadcrumb the outline assigns that page (issue #254)."""
    docs = pdf_pages_to_documents(
        _pdf_with_outline(),
        filename="m.pdf",
        mime="application/pdf",
        describer=_describer(),
    )
    by_page = {d.metadata["page"]: d.metadata.get("section") for d in docs}
    assert by_page == {
        1: "Chapter 1",
        2: "Chapter 1 > 1.1 Intro",
        3: "Chapter 1 > 1.1 Intro",
        4: "Chapter 2",
        5: "Chapter 2 > 2.1 Method",
    }


def test_pdf_pages_have_no_section_key_without_outline():
    """A PDF with no bookmarks → Documents carry ``page`` but never a
    ``section`` key (so provenance simply omits it — graceful degrade)."""
    docs = pdf_pages_to_documents(
        _no_outline_pdf(),
        filename="plain.pdf",
        mime="application/pdf",
        describer=_describer(),
    )
    assert docs
    assert all("section" not in d.metadata for d in docs)


def test_pdf_parser_end_to_end_attaches_section():
    """Through the IParser surface, not just the helper."""
    docs = PdfParser(_describer()).parse(
        MaterialisedParserInput(_pdf_with_outline(), filename="m.pdf"),
        filename="m.pdf",
        mime="application/pdf",
    )
    assert docs[0].metadata["section"] == "Chapter 1"


def test_outline_sections_skips_unresolvable_and_none_destinations():
    """A bookmark whose destination raises or resolves to None is skipped (not
    fatal); the surrounding bookmarks still map their pages."""
    outline = [_Dest("Intro", 0), _Dest("Bad", _RAISE), _Dest("Ghost", None), _Dest("Later", 2)]
    assert _sections(outline, 3) == {0: "Intro", 1: "Intro", 2: "Later"}


def test_outline_sections_returns_empty_on_broken_outline_tree():
    """A broken outline tree degrades to no sections rather than failing ingest."""
    assert _sections("BROKEN", 2) == {}


def test_outline_sections_handles_a_child_list_with_no_parent_bookmark():
    """Defensive: a child list with no preceding bookmark keeps the parent
    trail instead of crashing."""
    assert _sections([[_Dest("Orphan", 0)]], 1) == {0: "Orphan"}
