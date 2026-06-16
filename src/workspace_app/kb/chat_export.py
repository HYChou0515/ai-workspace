"""The `.chat.json` export format — chat-history round-trip contract.

One format, two ends:

  - **Export**: ``GET /a/{slug}/items/{id}/export-chat`` serialises a
    conversation with ``build_chat_export`` and downloads it as
    ``{investigation_id}.chat.json``.
  - **Upload**: the Ingestor recognises ``*.chat.json`` (suffix
    convention — the export side guarantees it) and routes the file
    through the SAME insight-extraction pipeline the promote button
    uses, instead of the parser dispatch. Hand-crafted files work too
    — that's the debug path the feature exists for.

Schema (plain, pretty-printed JSON — editable in any editor)::

    {"title": "<investigation title>",
     "messages": [{"role": "...", "content": "...", "tool_name": "..."}, ...]}

``messages`` entries are the same dicts the promote path feeds
``Ingestor.ingest_chat``; extra keys are preserved and passed through.
"""

from __future__ import annotations

import json
from typing import Any, cast

CHAT_EXPORT_SUFFIX = ".chat.json"


def is_chat_export(filename: str) -> bool:
    return filename.lower().endswith(CHAT_EXPORT_SUFFIX)


def build_chat_export(*, title: str, messages: list[dict[str, Any]]) -> bytes:
    return json.dumps({"title": title, "messages": messages}, indent=2, ensure_ascii=False).encode(
        "utf-8"
    )


def parse_chat_export(raw: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Validate + decode an export. Raises ``ValueError`` naming the
    offending part — uploads surface it on ``SourceDoc.status_detail``
    so the operator knows which bit of a hand-crafted file to fix."""
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"expected an object at the top level, got {type(data).__name__}")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError('missing or empty "title"')
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError('"messages" must be a non-empty list')
    for i, m in enumerate(messages, start=1):
        if not isinstance(m, dict):
            raise ValueError(f"message {i} is not an object")
        # `cast`: ty types the json-loaded dict as dict[Unknown, Unknown]
        # whose .get rejects str keys; runtime shape is checked above.
        msg = cast("dict[str, Any]", m)
        if not isinstance(msg.get("role"), str) or not isinstance(msg.get("content"), str):
            raise ValueError(f'message {i} needs string "role" and "content"')
    return title, messages
