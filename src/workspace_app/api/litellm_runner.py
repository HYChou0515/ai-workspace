"""LitellmAgentRunner — drives a turn using OpenAI Agents SDK + LiteLLM.

Per grill-me Q7/feedback-llm-choice: default model is
`ollama/qwen2.5-coder:7b-instruct` served by a local Ollama. Swap to
hosted providers via the AgentConfig.model field — LiteLLM handles the
provider dispatch (openai/, anthropic/, ollama/, together/, ...).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from agents import Agent, Runner
from agents import MaxTurnsExceeded as _AgentsMaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel

from ..agent.context import AgentToolContext
from ..agent.tools import build_tools
from ..resources import AgentConfig
from .events import (
    AgentEvent,
    AgentMetrics,
    MaxTurnsExceeded,
    MessageDelta,
    RunDone,
    RunError,
    ToolCallParseError,
    ToolEnd,
    ToolStart,
)


def _approx_tokens(chars: int) -> int:
    """Rough live token estimate from character count (~4 chars/token)."""
    return round(chars / 4)


def _delta_channel(event_type: str) -> Literal["content", "reasoning", "ignore"]:
    """Classify a raw Responses ``*.delta`` event by its type. FIVE event
    types carry a ``.delta`` string on the LiteLLM/Qwen path, so routing by
    "has .delta" alone is wrong:

      - response.output_text.delta            → the visible answer
      - response.refusal.delta                → a refusal (still user-facing)
      - response.reasoning_summary_text.delta → thinking (reasoning channel)
      - response.reasoning_text.delta         → thinking (reasoning channel)
      - response.function_call_arguments.delta→ streaming tool-call JSON; we
        IGNORE it (the complete args arrive via the tool_called run item),
        otherwise the args would leak into the answer text.
    """
    if event_type in ("response.output_text.delta", "response.refusal.delta"):
        return "content"
    if "reasoning" in event_type:
        return "reasoning"
    return "ignore"


def _partial_suffix(s: str, tag: str) -> int:
    """Length of the longest suffix of `s` that is a proper prefix of `tag`
    — held back so a tag split across chunks isn't emitted mid-tag."""
    for k in range(min(len(s), len(tag) - 1), 0, -1):
        if s.endswith(tag[:k]):
            return k
    return 0


class ThinkSplitter:
    """Streaming splitter that separates Qwen3-style ``<think>…</think>``
    reasoning from the visible answer. Feed it text chunks; each call
    returns the (content, reasoning) deltas decoded so far, buffering any
    partial tag at the tail until the next chunk completes it."""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._in_think = False
        self._buf = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        self._buf += chunk
        content: list[str] = []
        reasoning: list[str] = []
        while True:
            if not self._in_think:
                idx = self._buf.find(self.OPEN)
                if idx == -1:
                    keep = _partial_suffix(self._buf, self.OPEN)
                    content.append(self._buf[: len(self._buf) - keep])
                    self._buf = self._buf[len(self._buf) - keep :]
                    break
                content.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self.OPEN) :]
                self._in_think = True
            else:
                idx = self._buf.find(self.CLOSE)
                if idx == -1:
                    keep = _partial_suffix(self._buf, self.CLOSE)
                    reasoning.append(self._buf[: len(self._buf) - keep])
                    self._buf = self._buf[len(self._buf) - keep :]
                    break
                reasoning.append(self._buf[:idx])
                self._buf = self._buf[idx + len(self.CLOSE) :]
                self._in_think = False
        return "".join(content), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """Emit any buffered tail at stream end (an unclosed <think> is
        treated as reasoning)."""
        rest, self._buf = self._buf, ""
        return ("", rest) if self._in_think else (rest, "")


def _exact_usage(streamed: Any) -> tuple[int, int] | None:
    """The provider's exact (prompt, completion) token usage if reported."""
    try:
        usage = streamed.context_wrapper.usage
        return int(usage.input_tokens), int(usage.output_tokens)
    except Exception:  # noqa: BLE001 — usage shape varies / may be absent
        return None


def _final_tokens(
    usage: tuple[int, int] | None, prompt_tok: int, completion_chars: int
) -> tuple[int, int]:
    """Settle the final token counts: prefer the provider's exact usage, but
    keep the live approximations when it's absent or reports 0 (Ollama often
    streams usage as 0 — otherwise the final line would flip to ↑0 ↓0)."""
    approx_completion = _approx_tokens(completion_chars)
    if usage is None:
        return prompt_tok, approx_completion
    return (usage[0] or prompt_tok, usage[1] or approx_completion)


def _agent_for(
    config: AgentConfig, extra_instructions: str | None = None
) -> Agent[AgentToolContext]:
    base = config.system_prompt or ""
    if extra_instructions:
        base = f"{base}\n\n{extra_instructions}".strip()
    return Agent[AgentToolContext](
        name=config.name,
        instructions=base or None,
        model=LitellmModel(model=config.model),
        tools=list(build_tools(config.allowed_tools or None)),
    )


def diagnose_error(exc: BaseException) -> str:
    """Translate a LiteLLM/agents-SDK exception into a hint we can hand back
    to the model on retry. Pattern-match on substrings rather than exception
    types because LiteLLM wraps everything in APIConnectionError.

    The hints address the known small-model failure modes from grill-me Q11:
    malformed JSON in tool args, multiple-tool-calls-per-turn confusion,
    timeout. Falls back to a generic "try again" hint.
    """
    msg = str(exc)
    low = msg.lower()
    if "extra data" in low or "json" in low and "tool" in low:
        # LiteLLM Ollama chunk_parser concatenates multiple tool_calls'
        # arguments when the model emits more than one in a single
        # streaming response. Coaching the model to serialize fixes the
        # symptom without depending on an upstream LiteLLM patch.
        return (
            "Tool-call format error: your previous response combined multiple "
            "tool calls in one turn, which the framework cannot parse. "
            "Emit exactly ONE tool call per response and wait for its result "
            "before issuing the next one."
        )
    if "timeout" in low or "timed out" in low:
        return "The previous step timed out. Take a smaller step and try again."
    return f"The previous attempt failed: {msg[:200]}. Try again."


def classify_retry_event(exc: BaseException, hint: str) -> AgentEvent:
    """Decide which AgentEvent best represents this retry-able failure.

    ToolCallParseError is a first-class signal — the FE can render it
    distinctly so users know a model-format glitch is being handled,
    not a real error. Everything else stays as the generic RunError
    catch-all.
    """
    low = str(exc).lower()
    if "extra data" in low or ("json" in low and "tool" in low):
        return ToolCallParseError(hint=hint)
    return RunError(message=f"retry: {hint}")


def _raw_field(raw: Any, key: str) -> Any:
    """Read a field off a run item's raw_item, which may be an object
    (ResponseFunctionToolCall) OR a TypedDict (FunctionCallOutput) depending
    on the provider — the tool-output raw_item is a dict on the LiteLLM path,
    so plain getattr would silently return nothing (call_id lost → the FE
    tool stays "running" forever)."""
    if isinstance(raw, dict):
        return raw.get(key)
    return getattr(raw, key, None)


def _call_id(raw: Any) -> str:
    return _raw_field(raw, "call_id") or _raw_field(raw, "id") or ""


def _map_event(event: Any) -> AgentEvent | None:
    """Translate one openai-agents stream event into our AgentEvent shape.

    Returns None for events we deliberately drop (raw token chunks, handoffs,
    etc.) — keeps the SSE stream focused on what the UI cares about.
    """
    if getattr(event, "type", None) != "run_item_stream_event":
        return None
    name = event.name
    item = event.item
    if name == "tool_called":
        raw_call = item.raw_item
        args_obj: dict[str, object] = {}
        raw_args = _raw_field(raw_call, "arguments")
        if isinstance(raw_args, str) and raw_args:
            try:
                args_obj = json.loads(raw_args)
            except json.JSONDecodeError:
                args_obj = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            args_obj = raw_args
        return ToolStart(
            call_id=_call_id(raw_call),
            name=_raw_field(raw_call, "name") or "unknown",
            args=args_obj,
        )
    if name == "tool_output":
        # raw_item is a FunctionCallOutput TypedDict (dict) on the LiteLLM
        # path — read call_id via _raw_field, not getattr.
        return ToolEnd(
            call_id=_call_id(item.raw_item),
            output=str(getattr(item, "output", "")),
        )
    # message_output_created carries the FULL assistant message; we stream
    # the incremental token deltas (raw_response_event) in _run_once instead,
    # so dropping it here avoids emitting the reply twice.
    return None


class LitellmAgentRunner:
    """Runs one user turn through agents-SDK + LiteLLM, retrying once on
    recognised small-model failures and surfacing the diagnosis to the
    model on each retry. Caps retries so a wedged turn can't loop forever.
    """

    def __init__(self, config: AgentConfig | None = None, max_retries: int = 2) -> None:
        self._config = config or AgentConfig(name="workspace-agent")
        self._max_retries = max_retries

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        feedback: str | None = None
        attempt = 0
        while True:
            try:
                async for ev in self._run_once(prompt, ctx, feedback):
                    yield ev
                yield RunDone()
                return
            except _AgentsMaxTurnsExceeded as exc:
                # The agent burned through its turn budget — terminal,
                # no retry would help.
                turns = getattr(exc, "turns_run", getattr(exc, "max_turns", 0))
                yield MaxTurnsExceeded(turns=int(turns) if turns else 0)
                yield RunDone()
                return
            except Exception as exc:  # noqa: BLE001 — every other failure becomes a hint or final error
                attempt += 1
                if attempt > self._max_retries:
                    yield RunError(
                        message=f"giving up after {attempt} attempts: {type(exc).__name__}: {exc}"
                    )
                    yield RunDone()
                    return
                feedback = diagnose_error(exc)
                yield classify_retry_event(exc, feedback)

    async def _run_once(  # pragma: no cover — exercised only by the live Ollama test
        self, prompt: str, ctx: AgentToolContext, feedback: str | None
    ) -> AsyncIterator[AgentEvent]:
        agent = _agent_for(ctx.agent_config or self._config, extra_instructions=feedback)
        t0 = time.monotonic()
        prompt_tok = _approx_tokens(len(prompt))
        # ↑ sending the prompt
        yield AgentMetrics(phase="up", prompt_tokens=prompt_tok, elapsed_ms=0)

        completion_chars = 0
        last_emit = 0.0
        splitter = ThinkSplitter()
        streamed = Runner.run_streamed(agent, input=prompt, context=ctx)
        async for event in streamed.stream_events():
            if getattr(event, "type", None) == "raw_response_event":
                data = getattr(event, "data", None)
                delta = getattr(data, "delta", None)
                channel = _delta_channel(getattr(data, "type", "") or "")
                if isinstance(delta, str) and delta and channel != "ignore":
                    completion_chars += len(delta)
                    if channel == "reasoning":
                        yield MessageDelta(text=delta, reasoning=True)
                    else:  # content — still split any inline <think> tags
                        content, reasoning = splitter.feed(delta)
                        if reasoning:
                            yield MessageDelta(text=reasoning, reasoning=True)
                        if content:
                            yield MessageDelta(text=content)
                    now = time.monotonic()
                    if now - last_emit >= 0.2:  # throttle live metric updates
                        last_emit = now
                        yield AgentMetrics(
                            phase="down",
                            prompt_tokens=prompt_tok,
                            completion_tokens=_approx_tokens(completion_chars),
                            elapsed_ms=round((now - t0) * 1000),
                        )
                continue
            mapped = _map_event(event)
            if mapped is not None:
                yield mapped

        tail_content, tail_reasoning = splitter.flush()
        if tail_reasoning:
            yield MessageDelta(text=tail_reasoning, reasoning=True)
        if tail_content:
            yield MessageDelta(text=tail_content)

        prompt_final, completion_final = _final_tokens(
            _exact_usage(streamed), prompt_tok, completion_chars
        )
        yield AgentMetrics(
            phase="final",
            prompt_tokens=prompt_final,
            completion_tokens=completion_final,
            elapsed_ms=round((time.monotonic() - t0) * 1000),
        )
