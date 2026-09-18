"""The `.chat.json` export format — chat-history round-trip contract.

One format, two ends:

  - **Export**: ``GET /a/{slug}/items/{id}/chats/{chat_id}/export-chat``
    serialises THAT chat with ``build_chat_export`` and downloads it as
    ``{chat title}.chat.json`` (see ``chat_export_filename``). Naming the
    file after the chat is what lets a person tell two exports of the same
    item apart.
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
import re
from typing import Any, cast
from urllib.parse import quote

CHAT_EXPORT_SUFFIX = ".chat.json"
CHAT_MARKDOWN_SUFFIX = ".chat.md"
_SUFFIX = {"json": CHAT_EXPORT_SUFFIX, "md": CHAT_MARKDOWN_SUFFIX}


def is_chat_export(filename: str) -> bool:
    return filename.lower().endswith(CHAT_EXPORT_SUFFIX)


def chat_export_filename(
    title: str, *, fmt: str = "json", start: int | None = None, end: int | None = None
) -> str:
    """The download name for a chat's export — its title with the separators a
    filesystem dislikes folded away, plus the suffix the upload side dispatches
    on (``.chat.json``; the markdown twin is ``.chat.md``). Letters are KEPT
    whatever their script: these chats are named in Chinese, and a name
    reduced to hyphens names nothing. A title that folds away entirely
    (punctuation only, or an unnamed chat) falls back to ``chat`` rather than
    producing a bare ``.chat.json``. A range is named the way the person read
    it in the dialog — 1-based and inclusive, ``(2–3)`` for ``[1, 3)``."""
    safe = re.sub(r"[^\w.-]+", "-", title, flags=re.UNICODE).strip("-") or "chat"
    if start is not None or end is not None:
        safe += f" ({(start or 0) + 1}–{end})"
    return f"{safe}{_SUFFIX[fmt]}"


def slice_messages(
    messages: list[dict[str, Any]], start: int | None, end: int | None
) -> list[dict[str, Any]]:
    """The messages at positions ``[start, end)`` — 0-based, half-open, like a
    Python slice, so there is no argument about whether the last one is in.
    ``None`` at either end means from the first / to the last. The FE counts
    from the newest and converts; the API keeps one unambiguous coordinate
    system, so a job's payload can be replayed. A range that names nothing
    is refused with the rule it broke."""
    lo = 0 if start is None else start
    hi = len(messages) if end is None else end
    if lo < 0:
        raise ValueError("start must be 0 or more")
    if hi > len(messages):
        raise ValueError(f"end must be at most {len(messages)}")
    if lo >= hi:
        raise ValueError("start must be before end")
    return messages[lo:hi]


def chat_export_disposition(
    title: str, *, fmt: str = "json", start: int | None = None, end: int | None = None
) -> str:
    """The whole ``Content-Disposition`` value for a chat export.

    A header is latin-1 on the wire, so a Chinese chat title put straight into
    ``filename="…"`` raises `UnicodeEncodeError` while the response is being
    written — the export does not fail politely, it 500s. RFC 6266 is the settled
    answer, and the same one Starlette reaches for in `FileResponse`:
    ``filename*=UTF-8''…`` percent-encoded, which every current browser prefers
    when present. We also keep an ASCII ``filename`` (Starlette drops it) so a
    client that reads only the plain parameter still gets a sane name rather than
    none. Both are built here so the two can never disagree — the ASCII one is
    the UTF-8 one with the en dash of a range folded to a hyphen and every
    other non-ASCII run folded to one."""
    unicode_name = chat_export_filename(title, fmt=fmt, start=start, end=end)
    stem = unicode_name[: -len(_SUFFIX[fmt])]
    ascii_stem = re.sub(r"[^\x20-\x7e]+", "-", stem.replace("–", "-")).strip("-") or "chat"
    return (
        f'attachment; filename="{ascii_stem}{_SUFFIX[fmt]}"; '
        f"filename*=UTF-8''{quote(unicode_name, safe='')}"
    )


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
    except RecursionError as exc:  # nested past the decoder: 100k `[`
        raise ValueError("invalid JSON: nested too deep") from exc
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


def build_chat_markdown(*, title: str, messages: list[dict[str, Any]]) -> str:
    """The same messages ``build_chat_export`` serialises, rendered for a
    person to read or paste into a report: the title as the heading, one
    section per message headed by who spoke. An assistant's markdown is
    left exactly as written, its reasoning quoted above it; a tool call
    shows its name, its arguments as JSON and its output cut to the same
    ``tool_output_chars`` the video shows, with a ``[shown-files]``
    declaration turned into a list of paths (parsed the way the chat and
    the video parse it, ``shown_files_in``); an error names its kind; a
    stopped reply says so; any other role is one italic line."""
    from ..chat_video.options import VideoOptions
    from ..chat_video.timeline import cut_text, shown_files_in

    cut = VideoOptions().tool_output_chars
    parts = [f"# {title}\n"]
    for m in messages:
        role, content = str(m.get("role", "")), str(m.get("content", ""))
        if role == "user":
            parts.append(f"\n### 👤 {m.get('author') or 'User'}\n\n{content.rstrip()}\n")
        elif role == "assistant":
            parts.append(f"\n### 🤖 {m.get('author') or 'AI'}\n")
            if m.get("reasoning"):
                first, *rest = str(m["reasoning"]).rstrip().splitlines() or [""]
                quoted = "\n".join([f"> 💭 {first}", *(f"> {ln}" for ln in rest)])
                parts.append(f"\n{quoted}\n")
            parts.append(f"\n{content.rstrip()}\n")
            if m.get("stopped_reason"):
                parts.append(f"\n_（已中止：{m['stopped_reason']}）_\n")
        elif role == "tool":
            body, files = shown_files_in(content)
            parts.append(f"\n### 🔧 {m.get('tool_name') or 'tool'}\n")
            if m.get("tool_args"):
                args = json.dumps(m["tool_args"], ensure_ascii=False, indent=2)
                parts.append(f"\n```json\n{args}\n```\n")
            if body.strip():
                parts.append(f"\n```\n{cut_text(body.rstrip(), cut)}\n```\n")
            if files:
                parts.append("\n" + "".join(f"- 📎 {f.path}\n" for f in files))
        elif role == "error":
            kind = f"（{m['error_kind']}）" if m.get("error_kind") else ""
            parts.append(f"\n### ⚠️ 錯誤{kind}\n\n{content.rstrip()}\n")
        else:
            parts.append(f"\n_{content.strip()}_\n")
    return "".join(parts)
