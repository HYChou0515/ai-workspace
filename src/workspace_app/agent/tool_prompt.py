"""Format the agent's tool inventory into a system-prompt section.

When `Agent[AgentToolContext]` runs, the LLM sees the tools via the
OpenAI-style tool_calls schema in the API payload. That's enough for
beefy models, but small local LLMs (Qwen3:14b and similar) don't
reliably associate ``wafer-history`` in their inventory with the right
calling convention — they fall back to ``exec(["wafer-history", ...])``
because the name *looks* like a shell command.

Listing every tool's (name, description, JSON args schema) directly
in the system prompt makes the inventory inescapable: the LLM reads
it before generating any ``tool_calls``, so it can't confuse a
provisioned tool with a sandbox binary.

This is appended by ``_agent_for`` after ``compose_system_prompt`` —
the template-time prompt assembly stays template-time, and the runtime-
only tool inventory is composed in at runtime.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agents import FunctionTool

if TYPE_CHECKING:
    from ..tooling.catalog import ToolMeta


def format_tools_for_prompt(tools: Sequence[FunctionTool]) -> str:
    """Render `tools` as a Markdown "Tools available" section. Empty
    `tools` → empty string (caller skips concatenation)."""
    if not tools:
        return ""
    lines: list[str] = [
        "## Tools available",
        "",
        (
            "Each entry below is a **function tool** — call it by **name** through "
            "`tool_calls` with a JSON args object matching its schema. **Do NOT** "
            "`exec(['<tool-name>', ...])` — these tools are NOT shell binaries on "
            "PATH; trying to exec one will fail with `not found` (exit 127)."
        ),
        "",
    ]
    for t in tools:
        lines.append(f"### `{t.name}`")
        lines.append("")
        lines.append((t.description or "_(no description)_").strip())
        lines.append("")
        lines.append("Args (JSON schema):")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(t.params_json_schema or {}, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_disabled_tools_for_prompt(metas: Sequence[ToolMeta]) -> str:
    """Render App-declared-but-off tools (#480) as a "Tools available on
    request" section. These are NOT registered as callable tools — the section
    just makes the agent aware they exist so it avoids them by default and can
    ask the user to turn one on in the tool picker.

    Only ``name — one-line description`` per tool (no JSON args schema): they
    can't be called, so the schema is dead weight, and keeping the section lean
    matters for small local models. Empty ``metas`` → empty string (caller skips
    concatenation)."""
    if not metas:
        return ""
    lines: list[str] = [
        "## Tools available on request",
        "",
        (
            "These tools belong to this workspace but are turned off right now, so "
            "they are not in your callable set. If a task genuinely needs one, use "
            "the tools you already have where you can, and tell the user which tool "
            "to turn on in the tool picker (and why) so they can enable it for you."
        ),
        "",
    ]
    for m in metas:
        desc = m.description.strip()
        lines.append(f"- `{m.name}`" + (f" — {desc}" if desc else ""))
    return "\n".join(lines).rstrip() + "\n"
