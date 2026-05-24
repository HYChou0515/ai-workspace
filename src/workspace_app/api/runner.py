from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from ..agent.context import AgentToolContext
from .events import AgentEvent


class AgentRunner(Protocol):
    """Drives one agent turn and yields events.

    Real implementation wraps the OpenAI Agents SDK + LiteLLM/Ollama
    (`LitellmAgentRunner`); `ScriptedAgentRunner` below is the test/stub
    variant. The RCA and KB chats share one runner — `ctx` (AgentToolContext)
    tells them apart.

    Implement this to swap the agent engine (different framework, add RAG,
    change the event stream): as long as `run` yields the `AgentEvent` union the
    FE understands, nothing else changes.
    """

    def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        """Drive one turn for `prompt` under `ctx`, yielding `AgentEvent`s as
        they happen (`MessageDelta`, `ToolStart`/`ToolEnd`, `AgentMetrics`, …)
        and finishing with a terminal event (`RunDone`/`RunError`/…). Async
        generator; tools are reached through `ctx`. Ordinary failures should be
        surfaced as `RunError`, not raised, so the SSE stream closes cleanly."""
        ...


class ScriptedAgentRunner:
    """Emits a fixed event sequence — used by tests and to develop SSE plumbing
    without depending on a real LLM."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = list(events)

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        for ev in self._events:
            yield ev
