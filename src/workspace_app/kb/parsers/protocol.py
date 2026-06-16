"""IParser + IParserInput ABCs — the contract every KB parser implements.

Per the project's interface convention (memory: ABC over Protocol):

  - ABCs (not Protocols) so subclasses miss-overriding ``parse`` /
    ``matches`` blow up at construction time, not at the first call.
  - Interface and implementation live in separate modules — see
    ``input.py`` (concrete ``IParserInput``) and ``registry.py``
    (concrete ``ParserRegistry``).

The contract leaves three things deliberately to each parser impl:

  - **Which input form to consume** — ``IParserInput`` is a lazy
    adapter; parsers call ``source.as_bytes()`` / ``source.as_path()``
    / ``source.as_stream()`` according to taste. Calls are cached, so
    asking twice doesn't materialise twice.
  - **Whether a given file is its job** — ``matches(...)`` takes
    filename + mime + source; the parser may peek (cheap) into the
    source bytes when filename / mime alone are insufficient (e.g.
    sniffing JSON inside an ``application/octet-stream`` upload).
  - **How many Documents** the file produces — slides typically emit
    one ``Document`` per page, CSV one per row group, JSON one per
    top-level entity, etc. The chunker downstream still runs over the
    Document's text, but the per-Document metadata (page index, row
    range, JSON path) is preserved on retrieval.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    # Late import: ``llama_index`` is heavy + optional in some test
    # paths. The runtime type is opaque to the ABC — we only promise
    # the return is iterable.
    from llama_index.core.schema import Document


class IParserInput(ABC):
    """Lazy adapter the Ingestor hands to a parser's ``parse()`` call.

    Every concrete subclass MUST be able to produce all three input
    forms. A parser picks whichever form it prefers; un-called forms
    cost nothing (no tempfile written, no full read).
    """

    @abstractmethod
    def as_bytes(self) -> bytes:
        """The whole source as a bytes blob. Cached — repeat calls
        return the same instance. Use for small files where the parser
        wants to keep all bytes in memory (JSON, small CSVs)."""

    @abstractmethod
    def as_path(self) -> Path:
        """A real filesystem path the parser can hand to libraries
        that demand a path (pandas ``read_csv`` / pdfplumber /
        LlamaIndex Readers). The path is materialised once and reused;
        the file lives until the adapter is closed."""

    @abstractmethod
    def as_stream(self) -> BinaryIO:
        """A fresh BinaryIO over the source bytes — each call returns
        a NEW stream positioned at byte 0, so two parsers (or two
        passes in one parser) don't race on the cursor. Use for large
        files the parser wants to consume incrementally."""


class IParser(ABC):
    """One pluggable parser. Concrete impls live alongside this module
    (built-ins: CSV / JSON / PDF / image-VLM / slide-VLM) or under
    the operator's own package (in-house types — register in
    ``ParserRegistry`` at deploy startup).
    """

    @abstractmethod
    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        """``True`` when this parser handles the given file. ``filename``
        is the upload's stored path (e.g. ``docs/notes.json``);
        ``mime`` is the libmagic-sniffed type. The parser MAY peek at
        ``source.as_bytes()`` (or ``source.as_stream()``) when the
        filename + mime are insufficient — peeks are cached by the
        adapter so subsequent ``matches`` calls and the eventual
        ``parse`` call don't reread the same bytes."""

    @abstractmethod
    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
    ) -> Iterator[Document] | list[Document]:
        """Convert the source into LlamaIndex ``Document``s. Return an
        iterator OR a list — the Ingestor materialises whichever shape
        the parser produces. Multiple Documents per source are normal
        (one per slide / CSV row group / JSON entity); per-Document
        metadata carries the position so retrieval can name it back.

        ``on_progress(message)``, when supplied, lets a long-running
        parser (VLM image / VLM slide) surface a short status string
        that the Ingestor writes onto ``SourceDoc.status_detail`` —
        the FE polls / displays it so the operator sees the work isn't
        stalled. Cheap parsers (PDF / DOCX / JSON) typically ignore it.

        ``on_preview(data, mime)``, when supplied, lets a parser hand
        back a **browser-displayable derivative** of the original —
        e.g. PptxParser's soffice-converted PDF. The Ingestor persists
        it on ``SourceDoc.preview`` and the doc viewer renders it
        instead of the binary-download notice. Most parsers ignore it
        (their originals already display natively)."""
