"""Format the agent's tool inventory into a system-prompt section.

The LLM sees the tools themselves via the OpenAI-style tool schemas in the API
payload. This section does NOT repeat them: it used to render every tool's
name, description and full JSON schema a second time. Measured locally, that
second copy was the larger half — 14 built-ins cost 4,538 tokens as wire
schemas and 9,397 more as this section — and an operator reported a 414,115
character system prompt on a deployment with many third-party tools
(`docs/plan-tool-on-demand-loading.md`).

What survives is the rule the section existed for. Small local LLMs
(Qwen3:14b and similar) don't reliably associate ``wafer-history`` in their
inventory with the right calling convention — they fall back to
``exec(["wafer-history", ...])`` because the name *looks* like a shell
command. One short paragraph says so, and it costs the same whether the turn
carries three tools or three hundred.

It also explains the catalog: a command the turn has not used yet is advertised
with an EMPTY parameter schema, which without a word of explanation reads as a
tool that takes no arguments. Calling it returns the real schema.

This is appended by ``_agent_for`` after ``compose_system_prompt`` —
the template-time prompt assembly stays template-time, and the runtime-
only tool inventory is composed in at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from agents import FunctionTool

if TYPE_CHECKING:
    from ..tooling.catalog import ToolMeta


#: Fixed cost, whatever the turn carries. The tools themselves are in the API
#: payload; this says how to call them.
_TOOLS_NOTE = """## Tools available

Your tools are the ones in this request's tool list — call one by **name**
through `tool_calls` with a JSON args object. **Do NOT**
`exec(['<tool-name>', ...])`: they are NOT shell binaries on PATH, and trying
to exec one fails with `not found` (exit 127).
"""

#: Added only when the turn actually carries catalog entries. Two things the
#: model cannot work out on its own: an empty argument schema does not mean
#: "no arguments", and several tools can be loaded in ONE step.
#:
#: That second sentence is not a nicety. Each first use of a command costs an
#: extra step, and `runner.max_turns` (default 10) bounds a turn — so a turn
#: using five commands one at a time hits the ceiling with the work already
#: done. One `load_tool` naming them all costs one step for all five; without
#: this paragraph the model was never told that path existed (round 1 A4).
_CATALOG_NOTE = """A tool you have not used yet in this turn may be listed with
an **empty** argument schema. That does not mean it takes no arguments — it
means its schema has not been sent yet. Call it anyway: the answer is its real
description and argument schema, and you then call it again with arguments
matching that schema.

If you expect to use several such tools, call `load_tool` ONCE with all of
their names (or with a bundle's name, shown in each entry as `[bundle]`, to
load every command in it) — each tool you load separately costs an extra step.
"""


def format_tools_for_prompt(tools: Sequence[FunctionTool]) -> str:
    """The one paragraph about how to call tools, plus the catalog paragraph
    when this turn has catalog entries. Empty `tools` → empty string (caller
    skips concatenation).

    `tools` decides WHETHER each paragraph is needed, never its length: it
    deliberately does not list them. See the module docstring."""
    if not tools:
        return ""
    # A turn with no package command carries no empty schema and no
    # `load_tool`, so the catalog paragraph would be ~70 tokens of instruction
    # about something that cannot happen (round 1 A15).
    if not any(t.name == "load_tool" for t in tools):
        return _TOOLS_NOTE
    return f"{_TOOLS_NOTE}\n{_CATALOG_NOTE}"


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


def format_unavailable_tools_for_prompt(reasons: Mapping[str, str]) -> str:
    """Render third-party tools this turn could not obtain (#674).

    Deliberately not folded into `format_disabled_tools_for_prompt`: that
    section tells the agent to ask the user to flip a switch, and here there is
    no switch. The bytes could not be fetched — an expired artifact, an
    unreachable store, a bundle built for the wrong base — and the fix belongs
    to whoever publishes the tool, not to the person in the chat.

    Saying so beats silence: without it the agent invents a workaround for a
    capability it was told about elsewhere, or insists the tool does not exist.
    Empty ``reasons`` → empty string (the usual case; a heading announcing that
    nothing is broken would cost context on every turn)."""
    if not reasons:
        return ""
    lines: list[str] = [
        "## Tools that are unavailable right now",
        "",
        (
            "These tools belong to this workspace but could not be loaded for this "
            "turn, so they are not in your callable set. This is not something the "
            "user can switch on — tell them what is missing and why, do what you "
            "can without it, and say plainly if the task needs it."
        ),
        "",
    ]
    lines.extend(f"- **{name}** — {reason}" for name, reason in sorted(reasons.items()))
    return "\n".join(lines) + "\n"
