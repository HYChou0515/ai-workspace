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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

if TYPE_CHECKING:
    # Late import: ``llama_index`` is heavy + optional in some test
    # paths. The runtime type is opaque to the ABC — we only promise
    # the return is iterable.
    from llama_index.core.schema import Document


@dataclass(frozen=True)
class ParamSpec:
    """One tunable knob a parser exposes (#328). A parser declares its
    knobs via :meth:`IParser.config_fields`; the findability-probe modal
    renders an editor from them (``text`` → textarea, ``number`` →
    numeric input, ``bool`` → toggle) and the ingestor / dry-run
    re-parse feed the chosen values back through ``parse(config=...)``.

    ``key`` is the dict key the parser reads out of its ``config``;
    ``default`` is what the parser uses when neither the collection nor a
    per-doc override sets it (the bottom of the precedence merge —
    ``kb.parser_config.effective_config``). ``label`` is the human string
    shown in the editor."""

    key: str
    kind: Literal["text", "number", "bool"]
    label: str
    default: Any = None


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

    def count_units(self, source: IParserInput, *, filename: str, mime: str) -> int:
        """How many independently-parseable **units** this source holds —
        pages (PDF), rows (CSV / Excel), top-level array elements (JSON),
        etc. (#227). The fan-out splitter uses this to break a large index
        into many small jobs that each ``parse`` a ``unit_range`` slice, so
        no single job exceeds the broker's consumer-ack timeout.

        MUST be **cheap** — a page/row/element count, never the expensive
        per-unit work (no VLM describe, no embed). The default ``1`` means
        "one indivisible unit" (the whole file): such a parser is never
        fanned out and indexes as a single job, exactly as before the seam.
        Override only for parsers whose per-unit work can be large."""
        return 1

    def config_fields(self) -> list[ParamSpec]:
        """The tunable knobs this parser exposes (#328). Default ``[]`` —
        the parser has no operator-tunable config, so the findability
        modal shows no editor for it and the ingestor calls ``parse``
        WITHOUT a ``config`` (back-compat: existing parsers are untouched).

        A prompt-driven parser (e.g. an ontology extractor whose prompt
        carries the target JSON schema, or the VLM describer) overrides
        this to advertise its knobs; the ingestor then resolves the
        effective config (parser defaults < collection < per-doc override)
        and threads it into ``parse(config=...)``.

        Declaring a knob here is the OPT-IN: a config-aware parser ADDS a
        ``config: Mapping[str, Any] | None = None`` keyword to its own ``parse``
        override (an optional kwarg, so it stays Liskov-compatible with this
        base signature) and reads its knobs out of it. Knob-less parsers leave
        both this and ``parse`` untouched — the ingestor never passes them a
        ``config`` — so the seam is zero-churn for every existing parser."""
        return []

    @abstractmethod
    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
        unit_range: tuple[int, int] | None = None,
    ) -> Iterator[Document] | list[Document]:
        """Convert the source into LlamaIndex ``Document``s. Return an
        iterator OR a list — the Ingestor materialises whichever shape
        the parser produces. Multiple Documents per source are normal
        (one per slide / CSV row group / JSON entity); per-Document
        metadata carries the position so retrieval can name it back.

        ``unit_range`` (#227), when given, restricts parsing to the
        half-open unit interval ``[start, end)`` (units as counted by
        ``count_units``) — the fan-out process job's slice. ``None`` (the
        default) parses the whole source. Parsers that leave
        ``count_units`` at the default ``1`` only ever receive ``None`` or
        the full range, so they may ignore the argument.

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
