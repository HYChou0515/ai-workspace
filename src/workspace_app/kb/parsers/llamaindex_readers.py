"""Bundled IParser wrappers for the LlamaIndex Reader-backed formats.

Replaces the pre-#39 ``reader_for(filename, mime) -> ReaderFn``
function with concrete ``IParser`` subclasses that the bundled
``ParserRegistry`` (built by ``factories.get_parser_registry``)
registers at the tail. Routing logic moves from the ``reader_for``
if/elif chain into each parser's own ``matches(...)``.

PDF graduated out of this module: ``kb/parsers/pdf.py`` owns it now
(per-page + selective VLM); HTML and DOCX remain Reader-wrapped here.

Each parser:

  - matches by **mime OR extension** — same OR semantics
    ``reader_for`` had (a ``.pdf`` upload whose libmagic sniff
    returned ``application/octet-stream`` should still parse);
  - consumes the source via ``source.as_path()`` — LlamaIndex
    Readers want a filesystem path. The ``MaterialisedParserInput``
    adapter caches the tempfile across calls, so a parser that calls
    ``as_path()`` twice (or the registry's ``matches`` then
    ``parse``) only writes one tempfile;
  - returns the Reader's ``Document`` list verbatim — the chunker +
    embedder pipeline downstream chews them the same as before.

The Reader constructors stay lazy so the module's import cost is
proportional to which formats the operator actually exercises (a
text-only deploy doesn't pull in ``pypdf`` / ``python-docx``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .protocol import IParser, IParserInput

# `on_progress` type — re-exported for parser implementations that
# want to surface "still working on page N/M" status. PDF / HTML /
# DOCX are fast enough to ignore it; VLM-backed parsers (future) use it.
_OnProgress = Callable[[str], None] | None

if TYPE_CHECKING:
    from llama_index.core.schema import Document


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _BaseReaderParser(IParser):
    """Shared shape for Reader-wrapped parsers — match by mime OR
    extension, parse by handing the cached tempfile to a lazily-
    constructed LlamaIndex Reader. Concrete subclasses fill in the
    constants + the Reader factory."""

    _ACCEPTED_MIMES: tuple[str, ...] = ()
    _ACCEPTED_EXTENSIONS: tuple[str, ...] = ()
    _READER_FACTORY: Callable[[], Any] | None = None

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        if mime in self._ACCEPTED_MIMES:
            return True
        fl = filename.lower()
        return any(fl.endswith(ext) for ext in self._ACCEPTED_EXTENSIONS)

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: _OnProgress = None,
        on_preview: Callable[[bytes, str], None] | None = None,
        unit_range: tuple[int, int] | None = None,
    ) -> list[Document]:
        # HTML / DOCX readers are fast enough that per-file status
        # updates would be noise; ignore on_progress entirely.
        # The Reader wants a path; the adapter materialised one (or
        # will, on this call) and caches it for the rest of the
        # ingest. The Ingestor's `close()` removes the tempfile.
        path = source.as_path()
        factory = type(self)._READER_FACTORY
        assert factory is not None  # subclass forgot to wire it
        return factory().load_data(file=path)


class HtmlParser(_BaseReaderParser):
    """Wraps ``HTMLTagReader(tag="body")`` — extracts the body text of
    an HTML upload. Picks up ``text/html`` and ``.html`` / ``.htm``."""

    _ACCEPTED_MIMES = ("text/html",)
    _ACCEPTED_EXTENSIONS = (".html", ".htm")
    _READER_FACTORY = staticmethod(lambda: _lazy_html_reader())  # type: ignore[assignment]


class DocxParser(_BaseReaderParser):
    """Wraps ``DocxReader``. Picks up the canonical DOCX mime + the
    ``.docx`` extension."""

    _ACCEPTED_MIMES = (_DOCX_MIME,)
    _ACCEPTED_EXTENSIONS = (".docx",)
    _READER_FACTORY = staticmethod(lambda: _lazy_docx_reader())  # type: ignore[assignment]


# ─── lazy reader constructors ───────────────────────────────────────


def _lazy_html_reader() -> Any:
    from llama_index.readers.file import HTMLTagReader

    return HTMLTagReader(tag="body")


def _lazy_docx_reader() -> Any:
    from llama_index.readers.file import DocxReader

    return DocxReader()
