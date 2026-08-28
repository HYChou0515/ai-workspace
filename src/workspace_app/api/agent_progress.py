"""Render a sub-agent's events as live progress lines under the parent's tool card.

Every delegation path shares this: `ask_knowledge_base` relaying the KB agent's
searches, and `run_agent` relaying a delegated sub-agent's work. Two renderers
would drift, and a user watching a card would have to learn two vocabularies for
the same thing.

Only work-IN-PROGRESS surfaces. The sub-agent's answer is the tool's *result* —
relaying it here too would show the user the same text twice.
"""

from __future__ import annotations

from .events import AgentEvent, MessageDelta, ToolLog, ToolStart

#: Which argument is worth showing beside a tool's name, most-telling first. A
#: tool call reads as "what it is doing to what", and these are the "what".
_HINT_KEYS = ("query", "question", "path", "command", "prompt", "name", "term")


def progress_line(ev: AgentEvent) -> str | None:
    """One line for `ev`, or `None` when there is nothing worth surfacing."""
    if isinstance(ev, ToolStart):
        icon = "🔎" if _is_search(ev.name) else "🔧"
        hint = _hint(ev.args)
        return f"{icon} {ev.name}: {hint}\n" if hint else f"{icon} {ev.name}\n"
    if isinstance(ev, ToolLog):
        # A still-running tool's own output — e.g. the retriever's enhancement
        # LLM thinking, or a delegated sub-agent's exec streaming.
        return ev.text
    if isinstance(ev, MessageDelta) and ev.reasoning:
        return ev.text
    return None


def _is_search(name: str) -> bool:
    """Searching is the one kind of work worth distinguishing at a glance: it is
    what a sub-agent spends most of its time doing, and what the user most often
    wants to check it looked in the right place."""
    return "search" in name or name.startswith(("ask_", "lookup_"))


def _hint(args: dict[str, object]) -> str:
    for key in _HINT_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""
