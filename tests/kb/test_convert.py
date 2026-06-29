"""``Ingestor.convert`` (#324) — parse-only conversion that reuses the SAME parser
registry the index step uses, but produces just the joined text (no chunk/embed) and
NEVER touches a SourceDoc. Topic-hub's ``→collections`` workflow calls this to turn an
upload into text BEFORE filing it into a collection, so only the converted artifact is
stored — never the raw binary.

``convert`` has no ``collection_id``/``doc_id`` by design, so it structurally cannot
persist anything — these tests assert the observable ``(text, kind)`` contract.
"""

from __future__ import annotations

from llama_index.core.schema import Document
from specstar import SpecStar

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.parsers import IParser, ParserRegistry


class _SlideParser(IParser):
    """A stand-in for a VLM/office parser: claims ``.pptx`` and emits markdown docs."""

    def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
        return filename.endswith(".pptx")

    def parse(self, source, *, filename, mime, on_progress=None, on_preview=None, unit_range=None):  # type: ignore[no-untyped-def]
        return [
            Document(text="# Slide 1\n\nHello", metadata={}),
            Document(text="bullet", metadata={}),
        ]


def test_convert_runs_parser_and_returns_joined_markdown(spec: SpecStar, embedder: HashEmbedder):
    registry = ParserRegistry().register(_SlideParser())
    ingestor = Ingestor(spec, embedder=embedder, parser_registry=registry)

    result = ingestor.convert(path="deck.pptx", data=b"PK\x03\x04rawpptxbytes")

    assert result.kind == "markdown"
    assert result.text == "# Slide 1\n\nHello\n\nbullet"


def test_convert_passes_through_plain_text_no_parser_claims(spec: SpecStar, embedder: HashEmbedder):
    """A plain-text/markdown upload no parser claims is already coherent: ``passthrough``
    so the caller files the original unchanged (keeps its extension). #324 Q5 'not
    necessarily md'."""
    ingestor = Ingestor(spec, embedder=embedder, parser_registry=ParserRegistry())

    result = ingestor.convert(path="notes.md", data=b"# Title\r\n\r\nBody under the heading.\n")

    assert result.kind == "passthrough"
    # normalized line endings, no raw bytes
    assert result.text == "# Title\n\nBody under the heading.\n"


def test_convert_returns_none_for_unreadable_binary(spec: SpecStar, embedder: HashEmbedder):
    """A binary no parser can read converts to nothing → ``none``; the caller skips it
    rather than storing the raw bytes (#324 Q6)."""
    ingestor = Ingestor(spec, embedder=embedder, parser_registry=ParserRegistry())

    result = ingestor.convert(path="mystery.bin", data=b"\x00\x01\x02\x03\xff\xfe\xfd\xfc")

    assert result == (None, "none")


class _ProgressParser(IParser):
    """Claims ``.pptx`` and reports progress like a real VLM slide parser."""

    def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
        return filename.endswith(".pptx")

    def parse(self, source, *, filename, mime, on_progress=None, on_preview=None, unit_range=None):  # type: ignore[no-untyped-def]
        if on_progress is not None:
            on_progress("describing slide 1/2")
        return [Document(text="slide text", metadata={})]


def test_convert_forwards_parser_progress_to_caller(spec: SpecStar, embedder: HashEmbedder):
    """A long parser's status reaches the caller (the workflow surfaces it) — there's no
    SourceDoc to write it onto during convert."""
    registry = ParserRegistry().register(_ProgressParser())
    ingestor = Ingestor(spec, embedder=embedder, parser_registry=registry)
    seen: list[str] = []

    ingestor.convert(path="deck.pptx", data=b"PK\x03\x04", on_progress=seen.append)

    assert seen == ["describing slide 1/2"]
