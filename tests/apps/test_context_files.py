"""Topic Hub §6 — `agent.context_files` deterministic injection. A labelled block
of the listed workspace files' LIVE content is prepended to each turn (read fresh,
never persisted). This covers the formatter + the FileStore reader; the app.py
send-message prepend is wired + integration-tested at Phase 10."""

from __future__ import annotations

from workspace_app.apps.context_files import build_context_block, context_files_block
from workspace_app.apps.manifest import AgentManifest
from workspace_app.filestore.memory import MemoryFileStore


def test_agent_manifest_carries_context_files():
    assert AgentManifest(prompt_file="p").context_files == []
    assert AgentManifest(prompt_file="p", context_files=["MEMORY.md"]).context_files == [
        "MEMORY.md"
    ]


def test_context_files_block_labels_each_file_under_an_authoritative_preamble():
    block = context_files_block([("MEMORY.md", "# Mem\nfoo"), ("collections.json", "[]")])
    assert "authoritative" in block
    assert "### MEMORY.md" in block and "foo" in block
    assert "### collections.json" in block and "[]" in block


def test_context_files_block_is_empty_when_nothing_substantive():
    assert context_files_block([]) == ""
    assert context_files_block([("empty.md", "   ")]) == ""  # whitespace-only is dropped


async def test_build_context_block_reads_live_filestore_and_skips_missing():
    fs = MemoryFileStore()
    await fs.write("ws1", "/MEMORY.md", b"# Mem\ncurrent")  # FileStore paths are absolute
    # 2nd file absent → skipped, not an error (hand-edited workspace).
    block = await build_context_block(fs, "ws1", ["MEMORY.md", "collections.json"])
    assert "current" in block and "### MEMORY.md" in block  # declared path labels it
    assert "collections.json" not in block


async def test_build_context_block_is_empty_with_no_paths():
    assert await build_context_block(MemoryFileStore(), "ws1", []) == ""
