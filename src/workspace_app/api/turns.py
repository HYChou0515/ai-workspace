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
import json
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.responses import StreamingResponse

from ..agent.context import AgentToolContext
from ..resources.conversation import MessageMetrics
from .events import (
    AgentEvent,
    AgentMetrics,
    MaxTurnsExceeded,
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


def _est_tokens(m: Any) -> int:
    """Rough token estimate for one persisted message (~4 chars/token).
    Used only for the history token budget — approximate is fine."""
    chars = len(getattr(m, "content", "") or "")
    args = getattr(m, "tool_args", None)
    if args:
        chars += len(str(args))
    return max(1, chars // 4)


def _fit_token_budget(msgs: list[Any], max_tokens: int) -> list[Any]:
    """Keep the NEWEST contiguous suffix of `msgs` whose estimated tokens
    fit `max_tokens` (issue #45). The newest message is always kept even
    if it alone exceeds the budget — dropping the current context is worse
    than a slight overflow, and the per-tool-output cap (#44) already
    bounds individual messages."""
    kept: list[Any] = []
    total = 0
    for m in reversed(msgs):
        cost = _est_tokens(m)
        if kept and total + cost > max_tokens:
            break
        kept.append(m)
        total += cost
    kept.reverse()
    return kept


def history_items(
    messages: Iterable[Any], *, max_messages: int, max_tokens: int = 0
) -> list[dict[str, Any]]:
    """Map persisted messages → SDK input items for cross-turn memory.

    The SDK's Responses-API `input` is a list of items. Three shapes:

      - `{role: "user"|"assistant", content}`            plain dialogue
      - `{type: "function_call", call_id, name, arguments}` the model's
        decision to call a tool
      - `{type: "function_call_output", call_id, output}`   what the
        tool returned

    A persisted tool message (role="tool", carrying tool_call_id +
    tool_name + tool_args + content=result) expands to BOTH items
    (call + output) — without them, the LLM has no memory of having
    invoked the tool, and the empty-content assistant turn that
    triggered it (its visible "output" was the tool_call, not text)
    is the only other trace, which the prior "drop if `not m.content`"
    filter also wiped. Net effect: the model woke up next turn with
    no idea step-divergence had ever run (see the May-30 export).

    Two windows, applied in order:
      - `max_messages` — a count cap (operator-comprehensible).
      - `max_tokens` — a TOKEN budget (issue #45). `0` disables it.
        Dropping is at MESSAGE granularity (whole tool call+output
        pairs stay together) from the OLDEST end, so the newest turns
        always survive even when a few huge tool outputs would
        otherwise overflow the context within `max_messages`.

    Duck-typed on `.role`/`.content`/`.tool_call_id`/`.tool_name`/
    `.tool_args` so RCA `Message` and KB `KbMessage` both fit."""
    msgs = list(messages)
    if max_messages:
        msgs = msgs[-max_messages:]
    if max_tokens:
        msgs = _fit_token_budget(msgs, max_tokens)
    items: list[dict[str, Any]] = []
    for m in msgs:
        if m.role == "error":
            # Issue #37 — terminal-failure markers are human-facing
            # diagnostics, kept OUT of the model's context BY KIND. The
            # one exception is a user cancellation: the model otherwise
            # has no idea its prior (possibly partial) answer was cut off
            # on purpose, which is exactly what the user's next message
            # leans on — so replay a compact system note. System/model
            # errors and the step-limit are NOT replayed (re-feeding a
            # connection error / "you ran out of turns" only derails a
            # small model).
            if getattr(m, "error_kind", None) == "cancelled":
                items.append(
                    {
                        "role": "system",
                        "content": "[Your previous response was interrupted by the user.]",
                    }
                )
            continue
        if m.role == "user" and m.content:
            items.append({"role": "user", "content": m.content})
        elif m.role == "assistant" and m.content:
            # Skip empty-content assistant turns: they marked a tool_call
            # decision that the following tool message will reconstruct.
            items.append({"role": "assistant", "content": m.content})
        elif m.role == "tool" and m.tool_call_id and m.tool_name:
            # The arguments field on the SDK item is JSON-encoded text
            # (matches the OpenAI Responses API shape). `tool_args` is
            # always a clean dict (or empty when `_map_event` couldn't
            # parse the model's raw `arguments` string — see its
            # except branch). The prior `{"_raw": <string>}` sentinel
            # is gone, so no peel-back projection is needed here.
            items.append(
                {
                    "type": "function_call",
                    "call_id": m.tool_call_id,
                    "name": m.tool_name,
                    "arguments": json.dumps(m.tool_args or {}),
                }
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.tool_call_id,
                    "output": m.content,
                }
            )
    return items


@dataclass
class TurnMessage:
    """A flavour-neutral message produced by a turn — an assistant answer, a
    tool call, or a terminal failure (issue #37). The caller maps these into
    its own model (RCA `Message` / KB `KbMessage`) when persisting in
    `on_complete`."""

    role: str  # "assistant" | "tool" | "error"
    content: str = ""
    reasoning: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # #62: the full display result (success-stderr kept) when it differs from
    # `content` (the cleaned, LLM-facing exec result). "" ⇒ render `content`.
    tool_display: str = ""
    created_at: int = field(default_factory=_now_ms)
    metrics: MessageMetrics | None = None
    error_kind: str | None = None  # role=error: error | cancelled | max_turns


def _error_message(item: RunError | RunCancelled | MaxTurnsExceeded) -> TurnMessage:
    """Render a terminal failure event as a persistable error message
    (issue #37). `error_kind` drives the next-turn history policy."""
    if isinstance(item, RunCancelled):
        return TurnMessage(
            role="error", content="The previous response was interrupted.", error_kind="cancelled"
        )
    if isinstance(item, MaxTurnsExceeded):
        return TurnMessage(
            role="error",
            content=f"The agent stopped after reaching its step limit ({item.turns}).",
            error_kind="max_turns",
        )
    return TurnMessage(role="error", content=item.message, error_kind="error")


@dataclass
class _TurnReducer:
    """Reduces a turn's `AgentEvent`s into persistable `TurnMessage`s. Shared by
    the per-requester `stream()` (KB chat) and the collaborative broadcast
    worker (#43) so both produce the SAME persisted shape from the same events."""

    produced: list[TurnMessage] = field(default_factory=list)
    _pending_tools: dict[str, ToolStart] = field(default_factory=dict)

    def _add_assistant(self, text: str, reasoning: bool) -> None:
        last = self.produced[-1] if self.produced else None
        # A tool message between answers starts a fresh assistant turn.
        if last is None or last.role != "assistant":
            last = TurnMessage(role="assistant")
            self.produced.append(last)
        if reasoning:
            last.reasoning = (last.reasoning or "") + text
        else:
            last.content += text

    def add(self, item: AgentEvent) -> None:
        if isinstance(item, MessageDelta):
            self._add_assistant(item.text, item.reasoning)
        elif isinstance(item, ToolStart):
            # call_id → its ToolStart, so the persisted tool message keeps the
            # tool's name + args (ToolEnd alone carries only the output).
            self._pending_tools[item.call_id] = item
        elif isinstance(item, ToolEnd):
            start = self._pending_tools.pop(item.call_id, None)
            self.produced.append(
                TurnMessage(
                    role="tool",
                    content=item.output,
                    tool_call_id=item.call_id,
                    tool_name=start.name if start else None,
                    tool_args=dict(start.args) if start else None,
                    tool_display=item.display,
                )
            )
        elif isinstance(item, AgentMetrics):
            # Pin the latest token usage onto the current assistant answer so the
            # ↑/↓ line survives a reload (the stream is live-only).
            for msg in reversed(self.produced):
                if msg.role == "assistant":
                    msg.metrics = MessageMetrics(
                        prompt_tokens=item.prompt_tokens,
                        completion_tokens=item.completion_tokens,
                        elapsed_ms=item.elapsed_ms,
                    )
                    break
        elif isinstance(item, (RunError, RunCancelled, MaxTurnsExceeded)):
            # Issue #37: a terminal failure is persisted as an error message so a
            # reloaded thread still shows it. Any partial output already in
            # `produced` is kept — the marker is appended, not a wipe.
            self.produced.append(_error_message(item))


@dataclass
class _TurnSession:
    """Per-conversation turn state: a lock serializing turns + the cancellable
    in-flight driver task."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_turn: asyncio.Task | None = None


_QueueItem = tuple[
    str, AgentToolContext, Callable[[list[TurnMessage]], None], "asyncio.Future[None]"
]


@dataclass
class _WorkspaceSession:
    """#43 collaborative turn state for one investigation: a FIFO queue of
    pending messages + a long-lived worker that runs them one at a time (shared
    sandbox/files ⇒ no concurrent turns) + the cancellable in-flight turn + the
    set of live broadcast subscribers (every viewer's SSE stream)."""

    queue: asyncio.Queue[_QueueItem] = field(default_factory=asyncio.Queue)
    worker: asyncio.Task | None = None
    current_turn: asyncio.Task | None = None
    subscribers: set[asyncio.Queue[AgentEvent]] = field(default_factory=set)

    def publish(self, event: AgentEvent) -> None:
        """Fan one event out to every live subscriber (#43 broadcast)."""
        for q in self.subscribers:
            q.put_nowait(event)


class ChatTurnEngine:
    """Runs one cancellable agent turn at a time per conversation key, streams
    its events over SSE, and reduces them into TurnMessages for the caller to
    persist. Shared by the RCA workspace and KB chat endpoints."""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner
        self._sessions: dict[str, _TurnSession] = {}
        # #43: collaborative per-investigation queue/worker sessions, separate
        # from the per-requester `stream()` sessions KB chat uses.
        self._ws_sessions: dict[str, _WorkspaceSession] = {}

    def _session(self, key: str) -> _TurnSession:
        return self._sessions.setdefault(key, _TurnSession())

    def _ws_session(self, key: str) -> _WorkspaceSession:
        return self._ws_sessions.setdefault(key, _WorkspaceSession())

    def forget(self, key: str) -> None:
        """Drop a conversation's turn session (on close / delete) so the
        registry doesn't grow without bound. Also tears down the collaborative
        worker + any in-flight turn (#43)."""
        self._sessions.pop(key, None)
        ws = self._ws_sessions.pop(key, None)
        if ws is None:
            return
        # Cancel the in-flight turn (if any) + the parked worker. `cancel()` on an
        # already-finished task is a harmless no-op, so no done-guard is needed.
        for task in (ws.current_turn, ws.worker):
            if task is not None:
                task.cancel()

    def enqueue(
        self,
        key: str,
        content: str,
        ctx: AgentToolContext,
        *,
        on_complete: Callable[[list[TurnMessage]], None],
    ) -> asyncio.Future[None]:
        """#43: append a message to the investigation's FIFO turn queue and
        ensure its worker is running. Unlike `stream()`, a new message does NOT
        cancel the in-flight turn — concurrent users serialize, they don't kill
        each other's work (Stop is the explicit `cancel_current`). Returns a
        future that resolves when THIS message's turn ends, so the caller can
        await its own turn while later messages queue behind it."""
        session = self._ws_session(key)
        if session.worker is None or session.worker.done():
            session.worker = asyncio.create_task(self._worker(session))
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        session.queue.put_nowait((content, ctx, on_complete, fut))
        return fut

    async def _worker(self, session: _WorkspaceSession) -> None:
        """Run the investigation's queued turns one at a time, in order. Parks on
        an empty queue (cheap); torn down by `forget`. Each turn is its own
        cancellable task so `cancel_current` stops only the running turn and the
        worker proceeds to the next; its completion future is then resolved."""
        while True:
            content, ctx, on_complete, fut = await session.queue.get()
            turn = asyncio.create_task(self._run_turn(content, ctx, on_complete, session.publish))
            session.current_turn = turn
            # The turn persists its own (partial) result via on_complete even
            # when cancelled; swallow the cancellation here so the worker lives.
            with contextlib.suppress(asyncio.CancelledError):
                await turn
            session.current_turn = None
            fut.set_result(None)  # wake the POST awaiting this message's turn
            session.queue.task_done()

    async def _run_turn(
        self,
        content: str,
        ctx: AgentToolContext,
        on_complete: Callable[[list[TurnMessage]], None],
        publish: Callable[[AgentEvent], None],
    ) -> None:
        """Drive one turn through the runner, reducing events into persistable
        messages AND broadcasting each raw event to the investigation's live
        subscribers. Cancellation / failure is recorded as a terminal message
        (and broadcast) and the (partial) result is always persisted."""
        reducer = _TurnReducer()
        try:
            async for ev in self._runner.run(content, ctx):
                reducer.add(ev)
                publish(ev)
        except asyncio.CancelledError:
            cancelled = RunCancelled()
            reducer.add(cancelled)
            publish(cancelled)
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a terminal error message
            err = RunError(message=f"{type(exc).__name__}: {exc}")
            reducer.add(err)
            publish(err)
        finally:
            on_complete(reducer.produced)

    async def cancel_current(self, key: str) -> None:
        """#43 Stop: interrupt the investigation's in-flight turn (anyone may do
        this). Queued messages are untouched — the worker runs the next one. A
        no-op when nothing is running."""
        session = self._ws_sessions.get(key)
        if session is None:
            return
        turn = session.current_turn
        if turn is not None and not turn.done():
            turn.cancel()
            with contextlib.suppress(BaseException):
                await turn

    def publish(self, key: str, event: AgentEvent) -> None:
        """#43: broadcast an externally-produced event (e.g. a human `UserMessage`
        or a `FileChanged`) on the investigation's stream. No-op if nobody has
        ever subscribed / enqueued for this key."""
        session = self._ws_sessions.get(key)
        if session is not None:
            session.publish(event)

    def subscribe(self, key: str) -> AsyncIterator[AgentEvent]:
        """#43: register a live broadcast subscriber and return an async iterator
        of its events. The endpoint wraps this in an SSE response; all viewers of
        an investigation share its single event stream. Live-only — the caller
        loads past messages via the conversation resource."""
        session = self._ws_session(key)
        q: asyncio.Queue[AgentEvent] = asyncio.Queue()
        session.subscribers.add(q)

        async def _gen() -> AsyncIterator[AgentEvent]:
            try:
                while True:
                    yield await q.get()
            finally:
                session.subscribers.discard(q)

        return _gen()

    def subscribe_sse(self, key: str) -> AsyncIterator[str]:
        """#43: like `subscribe`, but yields SSE-encoded frames so the endpoint is
        a trivial `StreamingResponse(turn_engine.subscribe_sse(id))` wrapper."""
        events = self.subscribe(key)

        async def _frames() -> AsyncIterator[str]:
            async for ev in events:
                yield to_sse(ev)

        return _frames()

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
            reducer = _TurnReducer()
            while True:
                item = await queue.get()
                if item is None:
                    on_complete(reducer.produced)
                    return
                reducer.add(item)  # build the persistable shape
                yield to_sse(item)  # stream the raw event to this requester

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def cancel(self, key: str) -> None:
        """Interrupt the conversation's in-flight turn (its stream gets
        RunCancelled, then closes). A no-op when nothing is running."""
        async with (session := self._session(key)).lock:
            await self._cancel_prior_turn(session)
