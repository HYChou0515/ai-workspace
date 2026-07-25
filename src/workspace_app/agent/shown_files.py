"""The one channel a tool result uses to declare "render these workspace files in
the chat": a final line ``[shown-files]{json}``.

``show_file`` writes it; the provisioned plotting tools get their stdout
normalised into it (``tooling.registry``). The FE reads that one form
(``web/src/renderers/shownFiles.ts`` — keep the marker in sync).

A marker rather than the whole result being JSON, because a tool result also has
to stay readable to the model and to the tool card — ``_format_exec``'s header
carries the exit code and anchors attribution for small models. A marker rather
than a new event / ``Message`` field, because the declaration then survives a
reload for free: it IS the persisted tool message.

Its own module so the output cap can protect it without importing ``tools``
(which imports the cap).
"""

from __future__ import annotations

import json
from typing import Any

import magic

from ..files import WorkspaceFiles, abs_path
from ..filestore.protocol import FileNotFound

SHOWN_FILES_KEY = "shown_files"
SHOWN_FILES_MARKER = "\n[shown-files]"


def declare_shown_files(text: str, files: list[dict[str, Any]]) -> str:
    """`text` with `files` declared for the chat to render. No files ⇒ unchanged."""
    if not files:
        return text
    payload = json.dumps({SHOWN_FILES_KEY: files}, ensure_ascii=False)
    return f"{text}{SHOWN_FILES_MARKER}{payload}"


def split_declaration(text: str) -> tuple[str, str]:
    """`(body, declaration)` — the declaration includes its marker, or is `""`.

    Anything that rewrites a tool result (the output cap) has to put the
    declaration back verbatim: it sits at the very end, which is precisely what a
    head-and-tail truncation eats first, and losing it is silent — no error, no
    card, the user simply never sees the file they were told about.
    """
    at = text.rfind(SHOWN_FILES_MARKER)
    if at < 0:
        return text, ""
    return text[:at], text[at:]


async def describe_for_display(
    files: WorkspaceFiles, workspace_id: str, path: str
) -> dict[str, Any]:
    """One `shown_files` entry for `path`, or `{}` when it doesn't resolve.

    Reads the bytes: the mime has to be sniffed (it decides inline-image vs card,
    and an extension can lie) and a declaration must never name a file that isn't
    there — the FE renders whatever is declared."""
    try:
        data = await files.read(workspace_id, path)
    except FileNotFound:
        return {}
    return {
        # Absolute: the FE's fileUrl/openFile seams take that form, the agent
        # writes relative (#549).
        "path": abs_path(path),
        "mime": magic.from_buffer(data, mime=True),
        "size": len(data),
    }
