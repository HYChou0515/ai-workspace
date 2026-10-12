"""Render a sub-agent's events as live progress lines under the parent's tool card.

Every delegation path shares this: `ask_knowledge_base` relaying the KB agent's
searches, and `run_agent` relaying a delegated sub-agent's work. Two renderers
would drift, and a user watching a card would have to learn two vocabularies for
the same thing.

Only work-IN-PROGRESS surfaces. The sub-agent's answer is the tool's *result* —
relaying it here too would show the user the same text twice.
"""

from __future__ import annotations

from collections.abc import Callable

from ..agent.delegation import Delegation
from ..sandbox.protocol import OutputSink
from .events import (
    AgentEvent,
    MessageDelta,
    ModelRetrying,
    RateLimited,
    RunError,
    ToolLog,
    ToolStart,
)

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


def make_relay(emit: OutputSink | None) -> Callable[[AgentEvent], None]:
    """The one relay every delegation path hands its sub-agent. Work lines go
    under the parent's tool card. When the delegating tool handed down a
    `Delegation` (docs/plan-friendly-errors.md P4), a hold or a retry goes to
    the parent TURN's status line — a sub-agent waiting out a 429 is the turn
    waiting, and shown as a card that stopped moving it read as a hang — and the
    run's failure goes back to the tool, so its card can say what happened."""

    def relay(ev: AgentEvent) -> None:
        if isinstance(ev, (RateLimited, ModelRetrying)):
            if isinstance(emit, Delegation):
                emit.status(ev)
            return
        if isinstance(ev, RunError):
            if isinstance(emit, Delegation):
                emit.failed(ev)
            return
        if emit is not None and (line := progress_line(ev)):
            emit(line.encode())

    return relay
