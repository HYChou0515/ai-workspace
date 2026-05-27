"""Shared agent-turn lifecycle for every chat surface (RCA workspace + KB chat).

Both surfaces drive the SAME runner through one cancellable in-flight turn per
conversation, stream the agent's events over SSE, and reduce those events into a
list of produced messages to persist. The only per-surface differences are the
`AgentToolContext` the caller builds and how it persists the result — injected
via `stream(..., on_complete=...)`. This keeps the turn/cancel/SSE machinery in
one place instead of duplicated per surface.

It owns turn lifecycle only — NOT the sandbox lifecycle (that stays in
InvestigationRegistry; the KB agent has no sandbox).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.responses import StreamingResponse

from ..agent.context import AgentToolContext
from ..resources.conversation import MessageMetrics
from .events import (
    AgentEvent,
    AgentMetrics,
    MessageDelta,
    RunCancelled,
    RunError,
    ToolEnd,
    ToolStart,
    to_sse,
)
from .runner import AgentRunner


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


@dataclass
class TurnMessage:
    """A flavour-neutral message produced by a turn — an assistant answer or a
    tool call. The caller maps these into its own model (RCA `Message` /
    KB `KbMessage`) when persisting in `on_complete`."""

    role: str  # "assistant" | "tool"
    content: str = ""
    reasoning: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    created_at: int = field(default_factory=_now_ms)
    metrics: MessageMetrics | None = None


@dataclass
class _TurnSession:
    """Per-conversation turn state: a lock serializing turns + the cancellable
    in-flight driver task."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_turn: asyncio.Task | None = None


class ChatTurnEngine:
    """Runs one cancellable agent turn at a time per conversation key, streams
    its events over SSE, and reduces them into TurnMessages for the caller to
    persist. Shared by the RCA workspace and KB chat endpoints."""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner
        self._sessions: dict[str, _TurnSession] = {}

    def _session(self, key: str) -> _TurnSession:
        return self._sessions.setdefault(key, _TurnSession())

    def forget(self, key: str) -> None:
        """Drop a conversation's turn session (on close / delete) so the
        registry doesn't grow without bound."""
        self._sessions.pop(key, None)

    async def _cancel_prior_turn(self, session: _TurnSession) -> None:
        """Cancel the session's in-flight turn and wait for it to wind down.
        The cancelled task emits RunCancelled to its subscriber before exiting.
        Serialized by the caller holding `session.lock`."""
        prev = session.current_turn
        if prev is None or prev.done():
            return
        prev.cancel()
        with contextlib.suppress(BaseException):
            await prev

    async def _drive(
        self, content: str, ctx: AgentToolContext, queue: asyncio.Queue[AgentEvent | None]
    ) -> None:
        """Pump runner.run into the per-turn queue; translate cancellation and
        any other failure into a terminal event so the subscriber stream always
        closes cleanly."""
        try:
            async for ev in self._runner.run(content, ctx):
                await queue.put(ev)
        except asyncio.CancelledError:
            await queue.put(RunCancelled())
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a terminal error event
            await queue.put(RunError(message=f"{type(exc).__name__}: {exc}"))
        finally:
            await queue.put(None)  # sentinel: stream closed

    async def stream(
        self,
        key: str,
        content: str,
        ctx: AgentToolContext,
        *,
        on_complete: Callable[[list[TurnMessage]], None],
    ) -> StreamingResponse:
        """Start a turn for `key` (cancelling any in-flight one first) and return
        its SSE response. `on_complete` is invoked once with the produced
        messages when the turn ends — normally, cancelled, or errored — so the
        caller can persist them in its own model."""
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        session = self._session(key)
        async with session.lock:
            await self._cancel_prior_turn(session)
            session.current_turn = asyncio.create_task(self._drive(content, ctx, queue))

        async def gen() -> AsyncIterator[str]:
            produced: list[TurnMessage] = []
            # call_id → its ToolStart, so the persisted tool message keeps the
            # tool's name + args (ToolEnd alone carries only the output).
            pending_tools: dict[str, ToolStart] = {}

            def add_assistant(text: str, reasoning: bool) -> None:
                last = produced[-1] if produced else None
                # A tool message between answers starts a fresh assistant turn.
                if last is None or last.role != "assistant":
                    last = TurnMessage(role="assistant")
                    produced.append(last)
                if reasoning:
                    last.reasoning = (last.reasoning or "") + text
                else:
                    last.content += text

            while True:
                item = await queue.get()
                if item is None:
                    on_complete(produced)
                    return
                if isinstance(item, MessageDelta):
                    add_assistant(item.text, item.reasoning)
                elif isinstance(item, ToolStart):
                    pending_tools[item.call_id] = item
                elif isinstance(item, ToolEnd):
                    start = pending_tools.pop(item.call_id, None)
                    produced.append(
                        TurnMessage(
                            role="tool",
                            content=item.output,
                            tool_call_id=item.call_id,
                            tool_name=start.name if start else None,
                            tool_args=dict(start.args) if start else None,
                        )
                    )
                elif isinstance(item, AgentMetrics):
                    # Pin the latest token usage onto the current assistant answer
                    # so the ↑/↓ line survives a reload (the stream is live-only).
                    for msg in reversed(produced):
                        if msg.role == "assistant":
                            msg.metrics = MessageMetrics(
                                prompt_tokens=item.prompt_tokens,
                                completion_tokens=item.completion_tokens,
                                elapsed_ms=item.elapsed_ms,
                            )
                            break
                yield to_sse(item)

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def cancel(self, key: str) -> None:
        """Interrupt the conversation's in-flight turn (its stream gets
        RunCancelled, then closes). A no-op when nothing is running."""
        async with (session := self._session(key)).lock:
            await self._cancel_prior_turn(session)
