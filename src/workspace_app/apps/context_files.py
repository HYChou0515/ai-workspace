"""Deterministic per-turn context injection for App turns (Topic Hub §6).

An App declares ``agent.context_files`` — workspace files (e.g. ``MEMORY.md``,
``collections.json``) whose **live** content is prepended to the content handed to
the agent **each turn**, wrapped in a labelled block. The block is re-derived fresh
from the FileStore every turn and **never persisted** into the conversation (only the
latest turn carries it), so the agent always sees current content and the history
stays clean — idempotent + replay-safe. Generalises the #106 context-card prepend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..filestore.protocol import FileNotFound

if TYPE_CHECKING:
    from ..filestore.protocol import FileStore

_PREAMBLE = (
    "The following workspace files are provided as current context for this turn — "
    "treat them as authoritative and up to date:"
)


def context_files_block(entries: list[tuple[str, str]]) -> str:
    """Render ``(path, content)`` pairs as a labelled block, or ``""`` when nothing
    is substantive (empty list, or only blank files) so the caller prepends nothing."""
    real = [(path, content) for path, content in entries if content.strip()]
    if not real:
        return ""
    parts = [_PREAMBLE]
    parts.extend(f"### {path}\n{content.rstrip()}" for path, content in real)
    return "\n\n".join(parts)


async def build_context_block(filestore: FileStore, workspace_id: str, paths: list[str]) -> str:
    """Read each ``path`` from the FileStore (skipping ones that don't exist — the
    workspace is hand-editable) and render them via :func:`context_files_block`.

    FileStore paths are absolute (``/MEMORY.md``); a declared path is normalised to
    that for the read, but its as-declared form labels the block."""
    entries: list[tuple[str, str]] = []
    for path in paths:
        read_path = path if path.startswith("/") else "/" + path
        try:
            data = await filestore.read(workspace_id, read_path)
        except FileNotFound:
            continue
        entries.append((path, data.decode("utf-8", "replace")))
    return context_files_block(entries)
