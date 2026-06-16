"""`kb.chat_export` — the `.chat.json` round-trip contract.

The SAME format is produced by `GET /a/{slug}/items/{id}/export-chat`
and consumed by the KB upload path (Ingestor routes `*.chat.json`
through the insight-extraction pipeline instead of the parser
dispatch). Locked decisions: suffix convention `.chat.json` (export
guarantees it, upload recognises it); schema `{"title": str,
"messages": [{role, content, ...}]}` — the same message dicts the
promote path feeds `ingest_chat`.
"""

from __future__ import annotations

import json

import pytest

from workspace_app.kb.chat_export import (
    CHAT_EXPORT_SUFFIX,
    build_chat_export,
    is_chat_export,
    parse_chat_export,
)


def test_suffix_constant_is_the_contract():
    assert CHAT_EXPORT_SUFFIX == ".chat.json"
    assert is_chat_export("inv-123.chat.json")
    assert is_chat_export("nested/dir/INV-7.CHAT.JSON")  # case-insensitive
    assert not is_chat_export("plain.json")
    assert not is_chat_export("chat.json.bak")


def test_build_then_parse_round_trips():
    messages = [
        {"role": "user", "content": "AOI flagged voids", "tool_name": ""},
        {"role": "assistant", "content": "Checking zone temps.", "tool_name": ""},
    ]
    raw = build_chat_export(title="MX-7 voids", messages=messages)
    title, parsed = parse_chat_export(raw)
    assert title == "MX-7 voids"
    assert parsed == messages
    # The export is plain, pretty-printed JSON — debuggable in an editor.
    assert json.loads(raw.decode("utf-8"))["title"] == "MX-7 voids"


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (b"{not json", "invalid JSON"),
        (b'["just", "a", "list"]', "expected an object"),
        (b'{"messages": [{"role": "user", "content": "x"}]}', "title"),
        (b'{"title": "t"}', "messages"),
        (b'{"title": "t", "messages": "nope"}', "messages"),
        (b'{"title": "t", "messages": ["nope"]}', "message 1"),
        (b'{"title": "t", "messages": [{"content": "no role"}]}', "message 1"),
    ],
)
def test_parse_rejects_malformed_exports_with_actionable_messages(raw: bytes, match: str):
    """Bad uploads must raise ValueError (→ status=error + the message
    in status_detail) naming what's wrong — these are hand-crafted
    debug files, the operator needs to know which part to fix."""
    with pytest.raises(ValueError, match=match):
        parse_chat_export(raw)
