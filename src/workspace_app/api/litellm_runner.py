"""LitellmAgentRunner — drives a turn using OpenAI Agents SDK + LiteLLM.

Per grill-me Q7/feedback-llm-choice: default model is
`ollama/qwen2.5-coder:7b-instruct` served by a local Ollama. Swap to
hosted providers via the AgentConfig.model field — LiteLLM handles the
provider dispatch (openai/, anthropic/, ollama/, together/, ...).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import litellm
from agents import Agent, ModelSettings, RunConfig, Runner
from agents import MaxTurnsExceeded as _AgentsMaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel
from openai.types.shared import Reasoning

from ..agent.context import AgentToolContext
from ..agent.tools import build_tools
from ..resources import AgentConfig
from ..tooling.registry import PackageInfo, build_function_tools
from .events import (
    AgentEvent,
    AgentMetrics,
    MaxTurnsExceeded,
    MessageDelta,
    RunDone,
    RunError,
    ToolCallParseError,
    ToolEnd,
    ToolLog,
    ToolStart,
)

# Drop params a model doesn't support (e.g. reasoning_effort on a non-reasoning
# model) instead of erroring — the per-message reasoning-effort selector sends
# it to every model, and LiteLLM's support varies by provider.
litellm.drop_params = True


def _approx_tokens(chars: int) -> int:
    """Rough live token estimate from character count (~4 chars/token)."""
    return round(chars / 4)


def _build_input(history: list[dict[str, str]], prompt: str) -> str | list[dict[str, str]]:
    """The SDK `input` for this turn: a plain string when there's no history,
    else the prior dialogue items followed by this turn's user message — so the
    agent has cross-turn memory (#17)."""
    if not history:
        return prompt
    return [*history, {"role": "user", "content": prompt}]


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
    config: AgentConfig,
    packages: list[PackageInfo] | None = None,
    extra_instructions: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    template_profile: str | None = None,
) -> Agent[AgentToolContext]:
    base = config.system_prompt or ""
    if extra_instructions:
        base = f"{base}\n\n{extra_instructions}".strip()
    # `template_profile` opts in `read_skill` when the profile ships skills
    # (issue #29 / §A). build_tools handles the conditional internally.
    tools = list(build_tools(config.allowed_tools or None, profile=template_profile))
    # Expand the package selection (allowed_tools colon syntax) into
    # FunctionTools; the sandbox-side launcher gets execed when the LLM
    # calls one. build_function_tools handles `"pkg"` (all commands) and
    # `"pkg:cmd"` (single command), and silently skips unknown names.
    if packages:
        tools.extend(build_function_tools(packages, allowed=config.allowed_tools or []))
    # Per-message reasoning effort (the UI selector). Only set when chosen —
    # absent leaves the model's default. drop_params (above) drops it on models
    # that don't support it, so it's safe to send to any model.
    model_settings = (
        # effort is validated to low/medium/high by the request body.
        ModelSettings(reasoning=Reasoning(effort=reasoning_effort))  # ty: ignore[invalid-argument-type]
        if reasoning_effort
        else ModelSettings()
    )
    return Agent[AgentToolContext](
        name=config.name,
        instructions=base or None,
        model=LitellmModel(model=config.model, base_url=base_url, api_key=api_key),
        model_settings=model_settings,
        tools=tools,  # ty: ignore[invalid-argument-type]  # list[FunctionTool] ⊂ list[Tool]
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


def _should_retry(*, progress_made: bool, attempt: int, max_retries: int) -> bool:
    """Decide whether to restart the turn after `_run_once` raised.

    Issue #26: the agents-SDK can't resume a stream mid-turn — a restart
    re-runs the prompt from scratch, throwing away any text the user has
    already seen + any tool calls already executed. So only retry when
    nothing user-visible has streamed yet (the early small-model JSON-parse
    failures we hand a hint back for). Once there's progress, showing the
    error wins over clobbering the chat.
    """
    if progress_made:
        return False
    return attempt <= max_retries


class LitellmAgentRunner:
    """Runs one user turn through agents-SDK + LiteLLM, retrying once on
    recognised small-model failures and surfacing the diagnosis to the
    model on each retry. Caps retries so a wedged turn can't loop forever.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        max_retries: int = 2,
        max_turns: int = 10,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._config = config or AgentConfig(name="workspace-agent")
        self._max_retries = max_retries
        self._max_turns = max_turns
        # Chat LLM endpoint (global; see factories.Settings). None → LiteLLM's
        # own provider env / Ollama defaults.
        self._base_url = base_url
        self._api_key = api_key

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        feedback: str | None = None
        attempt = 0
        while True:
            # Tracks whether anything user-visible has streamed this attempt.
            # If yes, a restart on failure would clobber it (the SDK can't
            # resume) — see #26 + _should_retry.
            progress_made = False
            try:
                async for ev in self._run_once(prompt, ctx, feedback):
                    if isinstance(ev, MessageDelta) and ev.text or isinstance(ev, ToolEnd):
                        progress_made = True
                    yield ev
                yield RunDone()
                return
            except _AgentsMaxTurnsExceeded:
                # The agent burned through its turn budget — terminal, no
                # retry would help. The SDK exception only carries a message,
                # so we report our own configured ceiling (never a bare 0).
                yield MaxTurnsExceeded(turns=self._max_turns)
                yield RunDone()
                return
            except Exception as exc:  # noqa: BLE001 — every other failure becomes a hint or final error
                attempt += 1
                if not _should_retry(
                    progress_made=progress_made, attempt=attempt, max_retries=self._max_retries
                ):
                    if progress_made:
                        # Don't pretend "giving up after N attempts" — we
                        # made exactly one attempt; it produced output and
                        # then failed. The user keeps the partial output.
                        yield RunError(message=f"{type(exc).__name__}: {exc}")
                    else:
                        yield RunError(
                            message=f"giving up after {attempt} attempts: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    yield RunDone()
                    return
                feedback = diagnose_error(exc)
                yield classify_retry_event(exc, feedback)

    async def _run_once(  # pragma: no cover — exercised only by the live Ollama test
        self, prompt: str, ctx: AgentToolContext, feedback: str | None
    ) -> AsyncIterator[AgentEvent]:
        agent = _agent_for(
            ctx.agent_config or self._config,
            ctx.packages,
            extra_instructions=feedback,
            base_url=self._base_url,
            api_key=self._api_key,
            reasoning_effort=ctx.reasoning_effort,
            template_profile=ctx.template_profile,
        )
        t0 = time.monotonic()
        prompt_tok = _approx_tokens(len(prompt))

        # The SDK delivers model output via stream_events(), but a running
        # tool (a long exec) produces stdout *between* those events with no
        # SDK channel to surface it. So we fan both into one queue: a producer
        # task drives stream_events(), and the exec tool pushes ToolLog chunks
        # via ctx.on_exec_output — the drain loop yields whichever arrives
        # first, so tool output shows up live while the command is still
        # running.
        queue: asyncio.Queue[AgentEvent | object] = asyncio.Queue()
        done = object()
        ctx.on_exec_output = lambda b: queue.put_nowait(ToolLog(text=b.decode("utf-8", "replace")))

        # Tag the SDK trace with the investigation id (group_id) so the live
        # monitor can attribute every span to the run that produced it.
        run_config = RunConfig(workflow_name="RCA turn", group_id=ctx.investigation_id)
        streamed = Runner.run_streamed(
            agent,
            input=_build_input(ctx.history, prompt),  # ty: ignore[invalid-argument-type]
            context=ctx,
            max_turns=self._max_turns,
            run_config=run_config,
        )

        async def produce() -> None:
            try:
                # ↑ sending the prompt
                queue.put_nowait(AgentMetrics(phase="up", prompt_tokens=prompt_tok, elapsed_ms=0))
                completion_chars = 0
                last_emit = 0.0
                splitter = ThinkSplitter()
                async for event in streamed.stream_events():
                    if getattr(event, "type", None) == "raw_response_event":
                        data = getattr(event, "data", None)
                        delta = getattr(data, "delta", None)
                        channel = _delta_channel(getattr(data, "type", "") or "")
                        if isinstance(delta, str) and delta and channel != "ignore":
                            completion_chars += len(delta)
                            if channel == "reasoning":
                                queue.put_nowait(MessageDelta(text=delta, reasoning=True))
                            else:  # content — still split any inline <think> tags
                                content, reasoning = splitter.feed(delta)
                                if reasoning:
                                    queue.put_nowait(MessageDelta(text=reasoning, reasoning=True))
                                if content:
                                    queue.put_nowait(MessageDelta(text=content))
                            now = time.monotonic()
                            if now - last_emit >= 0.2:  # throttle live metric updates
                                last_emit = now
                                queue.put_nowait(
                                    AgentMetrics(
                                        phase="down",
                                        prompt_tokens=prompt_tok,
                                        completion_tokens=_approx_tokens(completion_chars),
                                        elapsed_ms=round((now - t0) * 1000),
                                    )
                                )
                        continue
                    mapped = _map_event(event)
                    if mapped is not None:
                        queue.put_nowait(mapped)

                tail_content, tail_reasoning = splitter.flush()
                if tail_reasoning:
                    queue.put_nowait(MessageDelta(text=tail_reasoning, reasoning=True))
                if tail_content:
                    queue.put_nowait(MessageDelta(text=tail_content))

                prompt_final, completion_final = _final_tokens(
                    _exact_usage(streamed), prompt_tok, completion_chars
                )
                queue.put_nowait(
                    AgentMetrics(
                        phase="final",
                        prompt_tokens=prompt_final,
                        completion_tokens=completion_final,
                        elapsed_ms=round((time.monotonic() - t0) * 1000),
                    )
                )
            finally:
                queue.put_nowait(done)

        task = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                yield item  # ty: ignore[invalid-yield]  # not the sentinel → AgentEvent
        finally:
            if not task.done():
                task.cancel()
            # Re-raise producer failures (e.g. MaxTurnsExceeded) so run()'s
            # retry/terminal handling still fires.
            with contextlib.suppress(asyncio.CancelledError):
                await task
