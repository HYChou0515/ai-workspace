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
from typing import Any

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


def _exact_usage(streamed: Any) -> tuple[int, int] | None:
    """The provider's exact (prompt, completion) token usage if reported."""
    try:
        usage = streamed.context_wrapper.usage
        return int(usage.input_tokens), int(usage.output_tokens)
    except Exception:  # noqa: BLE001 — usage shape varies / may be absent
        return None


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
        raw_args = getattr(raw_call, "arguments", None)
        if isinstance(raw_args, str) and raw_args:
            try:
                args_obj = json.loads(raw_args)
            except json.JSONDecodeError:
                args_obj = {"_raw": raw_args}
        return ToolStart(
            call_id=getattr(raw_call, "call_id", "") or getattr(raw_call, "id", ""),
            name=getattr(raw_call, "name", "unknown"),
            args=args_obj,
        )
    if name == "tool_output":
        raw = item.raw_item
        return ToolEnd(
            call_id=getattr(raw, "call_id", "") or getattr(raw, "id", ""),
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
        streamed = Runner.run_streamed(agent, input=prompt, context=ctx)
        async for event in streamed.stream_events():
            if getattr(event, "type", None) == "raw_response_event":
                delta = getattr(getattr(event, "data", None), "delta", None)
                if isinstance(delta, str) and delta:
                    completion_chars += len(delta)
                    yield MessageDelta(text=delta)  # ↓ token-by-token reply
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

        usage = _exact_usage(streamed)
        yield AgentMetrics(
            phase="final",
            prompt_tokens=usage[0] if usage else prompt_tok,
            completion_tokens=usage[1] if usage else _approx_tokens(completion_chars),
            elapsed_ms=round((time.monotonic() - t0) * 1000),
        )
