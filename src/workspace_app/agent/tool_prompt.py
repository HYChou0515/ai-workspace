"""Format the agent's tool inventory into a system-prompt section.

When `Agent[AgentToolContext]` runs, the LLM sees the tools via the
OpenAI-style tool_calls schema in the API payload. That's enough for
beefy models, but small local LLMs (Qwen3:14b and similar) don't
reliably associate ``wafer-history`` in their inventory with the right
calling convention — they fall back to ``exec(["wafer-history", ...])``
because the name *looks* like a shell command. A short name inventory
in the system prompt keeps that binding inescapable.

#107 request hygiene: the section is deliberately SLIM — names + a
one-line summary, NO JSON args schemas. The schemas already reach the
model through the native ``tools`` param, and chat templates (Qwen/GLM)
render them into their own ``<tools>`` block; duplicating them as
fenced JSON in the content channel teaches the model a *textual*
representation of tool calls — exactly the shape it degenerates into
on long args, printing the call as message text instead of emitting a
tool_call (the #107 failure; none of the surveyed clients that don't
exhibit it, e.g. opencode, put schemas in the prompt). The section also
names that degeneration explicitly so the model can self-correct.

This is appended by ``_agent_for`` after ``compose_system_prompt`` —
the template-time prompt assembly stays template-time, and the runtime-
only tool inventory is composed in at runtime.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents import FunctionTool


def _summary(description: str) -> str:
    """First non-empty line of a tool description — the prompt list only
    binds names to "function tool"; the full description rides the native
    ``tools`` param."""
    for line in description.strip().splitlines():
        if line.strip():
            return line.strip()
    return "_(no description)_"


def format_tools_for_prompt(tools: Sequence[FunctionTool]) -> str:
    """Render `tools` as a slim Markdown "Tools available" section. Empty
    `tools` → empty string (caller skips concatenation)."""
    if not tools:
        return ""
    lines: list[str] = [
        "## Tools available",
        "",
        (
            "The function tools below are provisioned for this conversation; "
            "their argument schemas are provided through the API's tool-calling "
            "interface. Invoke a tool ONLY by emitting a native tool call. "
            "**Do NOT** `exec(['<tool-name>', ...])` — these tools are NOT "
            "shell binaries on PATH; trying to exec one will fail with "
            "`not found` (exit 127). **NEVER write a tool call or its JSON "
            "arguments into your message text** — a printed call is not "
            "executed; if you intend to call a tool, emit it only through "
            "the tool-call channel."
        ),
        "",
    ]
    for t in tools:
        lines.append(f"- `{t.name}` — {_summary(t.description or '')}")
    return "\n".join(lines) + "\n"
