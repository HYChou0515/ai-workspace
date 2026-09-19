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
        # RecursionError in the decoder, not JSONDecodeError
        pytest.param(b"[" * 100_000, "invalid JSON", id="nested-too-deep"),
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


# ─── Markdown: the same messages, for a person to read ───────────────────────


def test_markdown_export_reads_as_a_conversation():
    """`build_chat_markdown` is the export a person pastes into a report: the
    title as the heading, one section per message headed by who spoke, the
    assistant's markdown left as it was written."""
    from workspace_app.kb.chat_export import build_chat_markdown

    md = build_chat_markdown(
        title="MX-7 voids",
        messages=[
            {"role": "user", "content": "Why the voids?", "author": "hychou"},
            {"role": "assistant", "content": "**Two** causes:\n\n- moisture\n- pressure"},
        ],
    )

    assert md == (
        "# MX-7 voids\n"
        "\n"
        "### 👤 hychou\n"
        "\n"
        "Why the voids?\n"
        "\n"
        "### 🤖 AI\n"
        "\n"
        "**Two** causes:\n"
        "\n"
        "- moisture\n"
        "- pressure\n"
    )


def test_markdown_export_renders_every_role_the_chat_has():
    """Reasoning is a quote above the answer; a tool call shows its name,
    its arguments as JSON and its output cut to the same 600 characters the
    video shows, with a `[shown-files]` declaration turned into a list of
    paths; an error names its kind; a stopped reply says so; a system line
    is one italic line."""
    from workspace_app.agent.shown_files import declare_shown_files
    from workspace_app.kb.chat_export import build_chat_markdown

    md = build_chat_markdown(
        title="t",
        messages=[
            {"role": "system", "content": "context compacted"},
            {"role": "assistant", "content": "Plotting.", "reasoning": "need a chart\nfirst"},
            {
                "role": "tool",
                "tool_name": "sci_plot",
                "tool_args": {"x": "t", "y": "ram"},
                "content": declare_shown_files(
                    "x" * 700, [{"path": "/plots/a.png", "mime": "image/png", "size": 10}]
                ),
            },
            {"role": "assistant", "content": "Done", "stopped_reason": "length"},
            {"role": "error", "content": "model went away", "error_kind": "upstream"},
        ],
    )

    assert md == (
        "# t\n"
        "\n"
        "_context compacted_\n"
        "\n"
        "### 🤖 AI\n"
        "\n"
        "> 💭 need a chart\n"
        "> first\n"
        "\n"
        "Plotting.\n"
        "\n"
        "### 🔧 sci_plot\n"
        "\n"
        "```json\n"
        "{\n"
        '  "x": "t",\n'
        '  "y": "ram"\n'
        "}\n"
        "```\n"
        "\n"
        "```\n" + "x" * 600 + "…\n"
        "```\n"
        "\n"
        "- 📎 /plots/a.png\n"
        "\n"
        "### 🤖 AI\n"
        "\n"
        "Done\n"
        "\n"
        "_（已中止：length）_\n"
        "\n"
        "### ⚠️ 錯誤（upstream）\n"
        "\n"
        "model went away\n"
    )


def test_markdown_export_shows_a_tool_with_only_what_it_has():
    """`show_file` declares a file and says nothing else: the heading and
    the list, no empty JSON block and no empty output block; a bare tool
    result is the heading and the output."""
    from workspace_app.agent.shown_files import declare_shown_files
    from workspace_app.kb.chat_export import build_chat_markdown

    md = build_chat_markdown(
        title="t",
        messages=[
            {
                "role": "tool",
                "tool_name": "show_file",
                "content": declare_shown_files(
                    "", [{"path": "/a.png", "mime": "image/png", "size": 1}]
                ),
            },
            {"role": "tool", "content": "ok"},
        ],
    )

    assert md == "# t\n\n### 🔧 show_file\n\n- 📎 /a.png\n\n### 🔧 tool\n\n```\nok\n```\n"


# ─── a range of messages: absolute positions, half-open ──────────────────────


@pytest.mark.parametrize(
    ("start", "end", "picked"),
    [
        (None, None, [0, 1, 2, 3]),
        (0, 4, [0, 1, 2, 3]),
        (1, 3, [1, 2]),
        (2, None, [2, 3]),  # "from the third to the end"
        (None, 2, [0, 1]),
        (3, 4, [3]),
    ],
)
def test_a_range_is_absolute_positions_half_open(start, end, picked):
    """`[start, end)`, 0-based, like a Python slice — no argument about
    whether the last one is included. The FE counts from the newest and
    converts; the API keeps one unambiguous coordinate system, so a job's
    payload can be replayed."""
    from workspace_app.kb.chat_export import slice_messages

    messages = [{"role": "user", "content": str(i)} for i in range(4)]

    assert [int(m["content"]) for m in slice_messages(messages, start, end)] == picked


@pytest.mark.parametrize(
    ("start", "end", "why"),
    [
        (3, 3, "start must be before end"),
        (4, 2, "start must be before end"),
        (-1, 2, "start must be 0 or more"),
        (0, 5, "end must be at most 4"),
        (0, 0, "start must be before end"),
    ],
)
def test_a_range_that_names_nothing_is_refused_with_the_rule(start, end, why):
    from workspace_app.kb.chat_export import slice_messages

    messages = [{"role": "user", "content": str(i)} for i in range(4)]

    with pytest.raises(ValueError, match=why):
        slice_messages(messages, start, end)


def test_no_range_is_the_whole_thread_even_when_the_thread_is_empty():
    """Decision 6: "不給 = 全部". `[0, 0)` names nothing and is refused when
    ASKED for; an empty thread with no range asked for is simply an empty
    export — the review found the export of a fresh chat had gone from 200
    to 422 "start must be before end", a rule the caller never invoked."""
    from workspace_app.kb.chat_export import slice_messages

    assert slice_messages([], None, None) == []


def test_a_half_range_names_no_range_in_the_file():
    """`?start=2` alone used to name the file `(3–None)`. The route resolves
    the missing end before naming; the name itself carries a range only when
    it has both ends."""
    from workspace_app.kb.chat_export import chat_export_filename

    assert chat_export_filename("t", start=1, end=None) == "t.chat.json"
    assert chat_export_filename("t", start=None, end=3) == "t.chat.json"


@pytest.mark.parametrize(
    ("title", "start", "end", "ascii_name"),
    [
        # A CJK title with a range used to lose the `chat` fallback and ship
        # `filename=" (2-3).chat.json"`: the range was appended before the fold.
        ("調查 報告", 1, 3, "chat (2-3).chat.json"),
        # Each CJK run became its own hyphen next to the stem's: `1--v2`.
        ("第1章 v2", None, None, "1-v2.chat.json"),
        ("A 報告 B", None, None, "A-B.chat.json"),
        ("MX-7 voids", 1, 3, "MX-7-voids (2-3).chat.json"),
    ],
)
def test_the_ascii_fallback_name_is_folded_then_ranged(title, start, end, ascii_name):
    from workspace_app.kb.chat_export import chat_export_disposition

    header = chat_export_disposition(title, fmt="json", start=start, end=end)

    assert f'filename="{ascii_name}"' in header


def test_the_download_name_carries_the_format_and_the_range():
    """`t.chat.json` as before; the markdown twin is `t.chat.md`; a range
    is named 1-based and inclusive in the file name — `(2–3)` for
    `[1, 3)` — because that is how the person read it in the dialog."""
    from workspace_app.kb.chat_export import chat_export_filename

    assert chat_export_filename("MX-7 voids") == "MX-7-voids.chat.json"
    assert chat_export_filename("MX-7 voids", fmt="md") == "MX-7-voids.chat.md"
    assert (
        chat_export_filename("MX-7 voids", fmt="md", start=1, end=3) == "MX-7-voids (2–3).chat.md"
    )
    assert chat_export_filename("MX-7 voids", start=0, end=4) == "MX-7-voids (1–4).chat.json"
