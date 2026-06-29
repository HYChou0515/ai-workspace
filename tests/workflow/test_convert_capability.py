"""The ``convert_upload`` capability + ``wf.convert`` handle method (#324) — topic-hub's
``→collections`` converts an upload to text BEFORE filing it, so only the converted
artifact reaches the collection (never the raw binary). The capability reuses the SAME KB
parsers (``Ingestor.convert``); the handle method journals it so a (VLM) conversion never
re-runs on replay.
"""

from __future__ import annotations

import pytest
from llama_index.core.schema import Document
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.parsers import IParser, ParserRegistry
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.workflow.capabilities import convert_upload
from workspace_app.workflow.handle import WorkflowHandle


class _SlideParser(IParser):
    def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
        return filename.endswith(".pptx")

    def parse(self, source, *, filename, mime, on_progress=None, on_preview=None, unit_range=None):  # type: ignore[no-untyped-def]
        return [
            Document(text="# Slide 1\n\nHello", metadata={}),
            Document(text="bullet", metadata={}),
        ]


def _ingestor(spec: SpecStar, parser: IParser) -> Ingestor:
    return Ingestor(
        spec,
        embedder=HashEmbedder(dim=EMBED_DIM),
        parser_registry=ParserRegistry().register(parser),
    )


async def test_convert_upload_writes_markdown_sibling_and_returns_its_path(spec_instance: SpecStar):
    store = MemoryFileStore()
    await store.write("ws", "/uploads/deck.pptx", b"PK\x03\x04rawpptxbytes")

    out_path, kind = await convert_upload(
        _ingestor(spec_instance, _SlideParser()),
        store,
        workspace_id="ws",
        src="uploads/deck.pptx",
        dest="deck.pptx",
    )

    assert (out_path, kind) == ("deck.pptx.md", "markdown")
    # the converted markdown is staged at its coherent bare path; raw is NOT what's filed
    assert await store.read("ws", "/deck.pptx.md") == b"# Slide 1\n\nHello\n\nbullet"


async def test_convert_upload_passes_through_text_keeping_its_name(spec_instance: SpecStar):
    """A plain-text upload no parser claims keeps its extension — the converted artifact
    is staged at ``dest`` (not ``dest.md``) so its name stays honest (#324 Q5)."""
    store = MemoryFileStore()
    await store.write("ws", "/uploads/notes.md", b"# Title\r\n\r\nBody.\n")

    out_path, kind = await convert_upload(
        _ingestor(spec_instance, _SlideParser()),  # parser doesn't claim .md
        store,
        workspace_id="ws",
        src="uploads/notes.md",
        dest="notes.md",
    )

    assert (out_path, kind) == ("notes.md", "passthrough")
    assert await store.read("ws", "/notes.md") == b"# Title\n\nBody.\n"


async def test_convert_upload_skips_unreadable_binary(spec_instance: SpecStar):
    """A binary no parser can read is skipped — nothing is staged, so the raw bytes never
    reach the collection (#324 Q6)."""
    store = MemoryFileStore()
    await store.write("ws", "/uploads/mystery.bin", b"\x00\x01\x02\x03\xff\xfe")

    out_path, kind = await convert_upload(
        _ingestor(spec_instance, _SlideParser()),
        store,
        workspace_id="ws",
        src="uploads/mystery.bin",
        dest="mystery.bin",
    )

    assert (out_path, kind) == (None, "none")
    assert not await store.exists("ws", "/mystery.bin")


async def test_wf_convert_journals_so_a_replay_does_not_reconvert(wf: WorkflowHandle):
    """``wf.convert`` is a deterministic node (manual §9): a replay returns the cached
    ``(out_path, kind)`` WITHOUT re-running the (VLM) conversion."""
    calls: list[tuple[str, str]] = []

    async def fake_convert(src: str, dest: str) -> tuple[str | None, str]:
        calls.append((src, dest))
        return f"{dest}.md", "markdown"

    wf._convert = fake_convert

    first = await wf.convert("uploads/deck.pptx", "deck.pptx")
    second = await wf.convert("uploads/deck.pptx", "deck.pptx")

    assert first == ("deck.pptx.md", "markdown")
    assert second == ("deck.pptx.md", "markdown")  # a real tuple, not a JSON list
    assert calls == [("uploads/deck.pptx", "deck.pptx")]  # journaled: ran once


async def test_wf_convert_without_capability_raises():
    """``wf.convert`` needs the capability wired by the run driver."""
    bare = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    with pytest.raises(RuntimeError):
        await bare.convert("uploads/a.pptx", "a.pptx")
