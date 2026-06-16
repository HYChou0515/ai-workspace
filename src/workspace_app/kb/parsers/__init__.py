"""KB parser framework (issue #39).

Pluggable architecture for converting binary uploads (CSV, JSON, PDF,
images via VLM, slides, in-house file types) into LlamaIndex
``Document``s that the existing chunker + embedder pipeline can
consume.

Public surface:

  - :class:`~workspace_app.kb.parsers.protocol.IParser` — the ABC
    every parser implements. ``parse(source, ...)`` returns
    ``list[Document]``; ``matches(*, filename, mime, source)`` lets
    the registry pick uniformly.
  - :class:`~workspace_app.kb.parsers.protocol.IParserInput` — the
    lazy adapter the registry hands to ``parse()``. ``as_bytes()`` /
    ``as_path()`` / ``as_stream()`` materialise on demand so a parser
    that only wants bytes doesn't pay the tempfile cost.
  - :class:`~workspace_app.kb.parsers.input.MaterialisedParserInput`
    — the concrete ``IParserInput`` backed by raw bytes (caches the
    bytes and any tempfile it wrote).
  - :class:`~workspace_app.kb.parsers.registry.ParserRegistry` — the
    runtime directory the Ingestor calls
    ``pick(filename, mime, source) -> IParser | None`` against.

Custom parsers (operator's in-house file types) subclass ``IParser``
and register a fresh instance into the registry the Ingestor uses.
The wiring to ingest + the bundled parsers ship in follow-up commits
once their open design questions are locked.
"""

from __future__ import annotations

from .input import MaterialisedParserInput
from .protocol import IParser, IParserInput
from .registry import ParserRegistry

__all__ = [
    "IParser",
    "IParserInput",
    "MaterialisedParserInput",
    "ParserRegistry",
]
