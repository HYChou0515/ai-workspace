from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from ..agent.context import AgentToolContext
from .events import AgentEvent


class AgentRunner(Protocol):
    """Drives one agent turn and yields events.

    Real implementation (Step 7) wraps OpenAI Agents SDK + LiteLLM/Ollama.
    ScriptedAgentRunner below is the test/stub variant.
    """

    def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]: ...


class ScriptedAgentRunner:
    """Emits a fixed event sequence — used by tests and to develop SSE plumbing
    without depending on a real LLM."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = list(events)

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        for ev in self._events:
            yield ev
