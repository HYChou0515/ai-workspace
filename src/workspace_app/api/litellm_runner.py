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
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, Literal

import litellm
from agents import (
    Agent,
    FunctionTool,
    ItemHelpers,
    MessageOutputItem,
    Model,
    ModelSettings,
    RunConfig,
    Runner,
)
from agents import MaxTurnsExceeded as _AgentsMaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel
from openai.types.shared import Reasoning

from ..agent.args_recovery import (
    ConcatenatedToolCallsError,
    MalformedToolArgsError,
    NonObjectToolArgsError,
)
from ..agent.context import AgentToolContext
from ..agent.repairing_model import RepairingModel
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
from .llm_trace import build_trace, format_trace_line, redact_endpoint, trace_enabled

_LOGGER = logging.getLogger(__name__)

# Drop params a model doesn't support (e.g. reasoning_effort on a non-reasoning
# model) instead of erroring — the per-message reasoning-effort selector sends
# it to every model, and LiteLLM's support varies by provider.
litellm.drop_params = True

# Human-readable label for the SDK trace, by run flavour, so telemetry can tell
# a wiki maintenance pass from a chat turn from an RCA turn. The three wiki
# configs have FIXED names (set in our code, not user config) so we match them
# precisely; the wiki context flags are belt-and-suspenders; otherwise a
# retriever-bearing context is a KB-style lookup and the rest is the RCA turn.
_WIKI_TRACE_NAMES = {
    "Wiki Maintainer": "Wiki maintainer",
    "Wiki Reader": "Wiki reader",
    "Wiki Merge": "Wiki merge",
}


def _trace_workflow_name(ctx: AgentToolContext) -> str:
    cfg_name = ctx.agent_config.name if ctx.agent_config is not None else ""
    if cfg_name in _WIKI_TRACE_NAMES:
        return _WIKI_TRACE_NAMES[cfg_name]
    if ctx.wiki_new_source is not None:
        return "Wiki maintainer"
    if ctx.wiki_cite_sources:
        return "Wiki reader"
    if ctx.retriever is not None:
        return "KB chat"
    return "RCA turn"


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
    app_slug: str | None = None,
    template_profile: str | None = None,
) -> Agent[AgentToolContext]:
    base = config.system_prompt or ""
    if extra_instructions:
        base = f"{base}\n\n{extra_instructions}".strip()
    # `template_profile` opts in `read_skill` when the profile ships skills
    # (issue #29 / §A). build_tools handles the conditional internally.
    #
    # Tri-state contract on `allowed_tools` (Q4-followup of the config
    # grill): ``None`` = "haven't specified" → defaults; ``[]`` =
    # explicit zero; ``[...]`` = exact. We pass through verbatim — the
    # earlier ``or None`` alias collapsed ``[]`` into "use defaults",
    # which silently turned the "kb_chat behind an RCA preset" misconfig
    # into "KB sub-agent suddenly has every workspace tool except
    # kb_search". The new behaviour: ``[]`` registers zero tools, and
    # the catalog-build validator separately rejects a kb_chat whose
    # resolved allowed_tools doesn't contain `kb_search`.
    tools = list(build_tools(config.allowed_tools, app_slug=app_slug, profile=template_profile))
    # Same tri-state for package tools (the colon-syntax expansion):
    # symmetric with build_tools above so bundled RCA presets
    # (allowed_tools=None) still expose every package command.
    if packages:
        tools.extend(build_function_tools(packages, allowed=config.allowed_tools))
    # Append the resolved tool inventory (name + description + JSON args
    # schema) to the system prompt so small local LLMs don't confuse
    # provisioned function tools with shell binaries (see plan §B.10 /
    # agent/tool_prompt.py).
    from ..agent.tool_prompt import format_tools_for_prompt

    tools_section = format_tools_for_prompt(tools)
    if tools_section:
        base = f"{base}\n\n{tools_section}".strip() if base else tools_section
    # Defend against the streaming-aggregator bug (small models emit two
    # parallel tool_calls; LiteLLM merges their `arguments` into one JSON-
    # concatenated mess the SDK can't parse). Each tool is wrapped to peel
    # off the first valid JSON object, run on that, and warn the model in
    # the result string so it self-corrects instead of getting blocked.
    from ..agent.args_recovery import wrap_with_args_recovery

    tools = [wrap_with_args_recovery(t) for t in tools]
    # Per-message reasoning effort (the UI selector). Only set when chosen —
    # absent leaves the model's default. drop_params (above) drops it on models
    # that don't support it, so it's safe to send to any model.
    #
    # We deliberately do NOT force `parallel_tool_calls=False` (#69). It was
    # the ONLY wire-level difference from the Replay path — which sends the
    # same tools with no such flag and reliably gets a structured tool_call,
    # while the live turn (which sent the flag) had the model emit the call
    # as plain text instead. Some providers reject the flag outright
    # (litellm `ollama_chat` → UnsupportedParamsError). The `args_recovery`
    # wrap below remains the single defence against the concatenated-args
    # streaming bug, so dropping the flag loses no safety.
    # Reasoning level → ModelSettings:
    #   - low|medium|high ⇒ reasoning ON via Reasoning(effort=...).
    #   - "none" (the OFF signal) ⇒ the OpenAI effort="none" only disables
    #     thinking on Ollama, so set the provider-correct disable param: vLLM via
    #     extra_body chat_template_kwargs enable_thinking=False; Ollama via
    #     extra_args think=False (the SDK LitellmModel splats extra_args as
    #     top-level completion kwargs and extra_body into the call's extra_body).
    #   - None (unset) ⇒ leave the model's default.
    if reasoning_effort == "none":
        from ..agent.reasoning import reasoning_off_kwargs

        off = reasoning_off_kwargs(config.model)
        # think → extra_args (top-level kwarg); extra_body → extra_body.
        model_settings = ModelSettings(
            extra_args={"think": off["think"]} if "think" in off else None,
            extra_body=off.get("extra_body"),
        )
    elif reasoning_effort:
        # effort is validated to low/medium/high by the request body.
        model_settings = ModelSettings(
            reasoning=Reasoning(effort=reasoning_effort),  # ty: ignore[invalid-argument-type]
        )
    else:
        model_settings = ModelSettings()
    # Per-config LLM endpoint (new schema's agents.presets.<x>.llm) wins
    # over the runner's constructor default — empty strings mean
    # "inherit from runner" so a single-endpoint deploy still works.
    eff_base_url = config.llm_base_url or base_url
    eff_api_key = config.llm_api_key or api_key
    model: Model = LitellmModel(model=config.model, base_url=eff_base_url, api_key=eff_api_key)
    if _decide_then_act_enabled():
        # Structured decide-then-act (WORKSPACE_AGENT_DECIDE_THEN_ACT): replace the
        # native-tool-call path (+ guess-based repair) with grammar-constrained
        # structured output — valid by construction, no fabrication. Provider-uniform.
        from ..agent.decide_then_act import DecideThenActModel

        model = DecideThenActModel(
            model,
            model=config.model,
            base_url=eff_base_url,
            api_key=eff_api_key,
            reasoning_effort=reasoning_effort,
        )
    else:
        # #76 BACKSTOP (always on): sanitize malformed tool-call JSON at the output
        # boundary so a probabilistic small model can't crash/poison/abort the turn.
        # Leave this line on. To toggle the *self-repair* layer (recovering the
        # model's intent), comment the marked `repair_tool_args(...)` line inside
        # agent/repairing_model.py — the backstop still sanitizes either way.
        model = RepairingModel(model)
    return Agent[AgentToolContext](
        name=config.name,
        instructions=base or None,
        model=model,
        model_settings=model_settings,
        tools=tools,  # ty: ignore[invalid-argument-type]  # list[FunctionTool] ⊂ list[Tool]
    )


# Retry hints for the three ways a small model botches a tool call (#76).
# Each addresses a DISTINCT failure so the model can actually self-correct;
# the old code collapsed all of them into the "one tool call" hint, which
# is only right for the concatenation case and left the model guessing for
# malformed / wrong-shape JSON.
_CONCAT_HINT = (
    "Tool-call format error: your previous response combined multiple "
    "tool calls in one turn, which the framework cannot parse. "
    "Emit exactly ONE tool call per response and wait for its result "
    "before issuing the next one."
)
_MALFORMED_HINT = (
    "Tool-call arguments error: your previous tool call's arguments were not "
    "valid JSON. Re-send the call with exactly one complete JSON object — put "
    "every key and string value in double quotes, and write nothing after the "
    "final closing brace."
)
_NON_OBJECT_HINT = (
    "Tool-call arguments error: your previous tool call's arguments must be a "
    'single JSON object (like {"path": "file.csv"}), not a list or a bare '
    "value. Re-send one JSON object matching the tool's schema."
)


def diagnose_error(exc: BaseException) -> str:
    """Translate a LiteLLM/agents-SDK exception into a hint we can hand back
    to the model on retry.

    Our own tool-arg guard (`args_recovery`) raises typed `ToolArgsError`s,
    so route those by `isinstance` for an exact hint. Everything else is an
    upstream LiteLLM error (which wraps the cause in APIConnectionError), so
    fall back to substring matching: `Extra data` is specifically the
    chunk-parser concatenation signal, a generic json+tool parse failure
    means a malformed single object, then timeout, then a generic retry.
    """
    if isinstance(exc, ConcatenatedToolCallsError):
        return _CONCAT_HINT
    if isinstance(exc, MalformedToolArgsError):
        return _MALFORMED_HINT
    if isinstance(exc, NonObjectToolArgsError):
        return _NON_OBJECT_HINT
    msg = str(exc)
    low = msg.lower()
    if "extra data" in low:
        return _CONCAT_HINT
    if "json" in low and "tool" in low:
        return _MALFORMED_HINT
    if "timeout" in low or "timed out" in low:
        return "The previous step timed out. Take a smaller step and try again."
    return f"The previous attempt failed: {msg[:200]}. Try again."


def classify_retry_event(exc: BaseException, hint: str) -> AgentEvent:
    """Decide which AgentEvent best represents this retry-able failure.

    ToolCallParseError is a first-class signal — the FE can render it
    distinctly so users know a model-format glitch is being handled,
    not a real error. All three tool-arg failures (malformed / non-object /
    concatenated) are model-format glitches, as is an upstream parse error.
    Everything else stays as the generic RunError catch-all.
    """
    if isinstance(
        exc, (MalformedToolArgsError, NonObjectToolArgsError, ConcatenatedToolCallsError)
    ):
        # Surface WHAT the model emitted (raw) + which call, so the FE can show
        # the user the actual mistake — not just the coaching hint (#76).
        return ToolCallParseError(hint=hint, raw=exc.raw_args, call_id=exc.call_id)
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
                # Streaming aggregator merged concat tool_calls (or model
                # emitted garbage) → leave args empty. Fabricating a
                # `{"_raw": <string>}` sentinel here used to leak through
                # history into the model's NEXT prompt, where it mimicked
                # the shape (`read_file(_raw="…")`); args_recovery already
                # raises at invoke-time so the run retries cleanly.
                args_obj = {}
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


def _emit_llm_trace(
    agent: Agent[AgentToolContext],
    ctx: AgentToolContext,
    *,
    runner_base_url: str | None,
    tool_calls: int,
    content_text: str,
) -> None:
    """Log this turn's LLM trace (#69): what we sent + the response shape.
    Best-effort — a trace must never break a turn, so any extraction hiccup
    is swallowed to a debug line."""
    try:
        cfg = ctx.agent_config
        model = getattr(getattr(agent, "model", None), "model", "") or (cfg.model if cfg else "")
        endpoint = redact_endpoint((cfg.llm_base_url if cfg else "") or runner_base_url)
        tools = [t.name for t in agent.tools if isinstance(t, FunctionTool)]
        ms = agent.model_settings
        trace = build_trace(
            model=model,
            endpoint=endpoint,
            tools=tools,
            parallel_tool_calls=ms.parallel_tool_calls,
            tool_choice=ms.tool_choice if isinstance(ms.tool_choice, str) else None,
            reasoning_effort=ctx.reasoning_effort,
            tool_calls=tool_calls,
            content_text=content_text,
        )
        _LOGGER.info(format_trace_line(trace))
    except Exception:  # noqa: BLE001 — observability must never break a turn
        _LOGGER.debug("llm trace emission failed", exc_info=True)


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


# ── non-streaming escape hatch (WORKSPACE_AGENT_STREAM=0) ─────────────────────
# The streaming aggregator (LiteLLM merging tool_call deltas) can corrupt or drop
# the tool_call so the model "emits the call as plain text" and the turn loops to
# MaxTurns. Measured against qwen3:14b on the same classify prompt: 0/4 streamed
# trials produced a real tool call vs 3/4 non-streamed (the #69 'replay works,
# live emits text' gap). Set WORKSPACE_AGENT_STREAM=0 to fetch the whole response
# in one shot (get_response) instead — a clean structured tool_call at the cost of
# live token output. Default stays streamed (observability; feedback_always_stream).
def _stream_enabled() -> bool:
    return os.environ.get("WORKSPACE_AGENT_STREAM", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _decide_then_act_enabled() -> bool:
    """Opt into the structured decide-then-act Model (DecideThenActModel) — a
    provider-uniform replacement for native tool-calling + guess-based repair.
    Only the tool-call generation (decision + args) is non-streaming; the final
    text answer streams via DecideThenActModel.stream_response (so the turn runs
    through the normal streaming path)."""
    return os.environ.get("WORKSPACE_AGENT_DECIDE_THEN_ACT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class _ItemEvent:
    """Adapt a completed ``RunItem`` to the ``run_item_stream_event`` shape that
    ``_map_event`` expects, so the non-streaming path reuses the SAME tool
    start/end mapping as the streamed path."""

    type = "run_item_stream_event"

    def __init__(self, name: str, item: Any) -> None:
        self.name = name
        self.item = item


# RunItem.type → the stream-event name _map_event keys on.
_NONSTREAM_TOOL_EVENT = {
    "tool_call_item": "tool_called",
    "tool_call_output_item": "tool_output",
}


class LitellmAgentRunner:
    """Runs one user turn through agents-SDK + LiteLLM, retrying once on
    recognised small-model failures and surfacing the diagnosis to the
    model on each retry. Caps retries so a wedged turn can't loop forever.
    """

    def __init__(
        self,
        max_retries: int = 2,
        max_turns: int = 10,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        # #94: no runner-level default config. Every turn's config arrives on
        # ctx.agent_config (resolved per-item via the AppCatalog / KB / wiki
        # catalogs); run() fails loud if it's missing.
        self._max_retries = max_retries
        self._max_turns = max_turns
        # Chat LLM endpoint (global; see factories.Settings). None → LiteLLM's
        # own provider env / Ollama defaults.
        self._base_url = base_url
        self._api_key = api_key

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        # #94: no silent fallback. Every turn must arrive with a resolved
        # agent_config (App items resolve via the AppCatalog; KB/wiki via their
        # catalogs). A None here means resolution failed upstream — fail loud
        # rather than run some default agent the operator never picked.
        if ctx.agent_config is None:
            yield RunError(
                message="no agent config resolved for this turn — the item could not "
                "be matched to an App (check the item id / App registration)"
            )
            yield RunDone()
            return
        feedback: str | None = None
        attempt = 0
        while True:
            # Tracks whether anything user-visible has streamed this attempt.
            # If yes, a restart on failure would clobber it (the SDK can't
            # resume) — see #26 + _should_retry.
            #
            # Reasoning-channel deltas (collapsed in the FE) are NOT visible
            # progress — they're internal monologue. A model that thinks
            # for 500 tokens, then emits a malformed tool_call, must still
            # be eligible for retry: there's nothing user-visible to clobber.
            # Without this gate the production transcript stalled on
            # 'Extra data on `plot`' without ever retrying.
            progress_made = False
            try:
                async for ev in self._run_once(prompt, ctx, feedback):
                    visible_progress = (
                        isinstance(ev, MessageDelta) and ev.text and not ev.reasoning
                    ) or isinstance(ev, ToolEnd)
                    if visible_progress:
                        progress_made = True
                    yield ev
                yield RunDone()
                return
            except _AgentsMaxTurnsExceeded:
                # The agent burned through its turn budget — terminal, no
                # retry would help. The SDK exception only carries a message,
                # so we report our own configured ceiling (never a bare 0).
                yield MaxTurnsExceeded(turns=ctx.max_turns or self._max_turns)
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
        assert ctx.agent_config is not None  # run() guards None before _run_once
        # Non-streaming path: the escape hatch (WORKSPACE_AGENT_STREAM=0).
        # decide-then-act now uses the STREAMING path too — its
        # DecideThenActModel.stream_response keeps only the tool-call generation
        # (decision + args) non-streaming and streams the final text answer.
        if not _stream_enabled():
            async for ev in self._run_once_nonstream(prompt, ctx, feedback):
                yield ev
            return
        agent = _agent_for(
            ctx.agent_config,
            ctx.packages,
            extra_instructions=feedback,
            base_url=self._base_url,
            api_key=self._api_key,
            reasoning_effort=ctx.reasoning_effort,
            app_slug=ctx.app_slug,
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

        # Tag the SDK trace with the run flavour (workflow_name) + the
        # investigation/collection id (group_id) so the live monitor can
        # attribute every span to the run that produced it AND tell wiki
        # maintenance / reader / merge apart from chat and RCA turns.
        run_config = RunConfig(
            workflow_name=_trace_workflow_name(ctx), group_id=ctx.investigation_id
        )
        streamed = Runner.run_streamed(
            agent,
            input=_build_input(ctx.history, prompt),  # ty: ignore[invalid-argument-type]
            context=ctx,
            max_turns=ctx.max_turns or self._max_turns,
            run_config=run_config,
        )

        async def produce() -> None:
            try:
                # ↑ sending the prompt
                queue.put_nowait(AgentMetrics(phase="up", prompt_tokens=prompt_tok, elapsed_ms=0))
                completion_chars = 0
                last_emit = 0.0
                splitter = ThinkSplitter()
                # #69 trace: accumulate the visible content + count tool
                # starts so we can label the turn's outcome (a real tool
                # call vs text that merely looks like one).
                content_buf: list[str] = []
                tool_calls_seen = 0
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
                                    content_buf.append(content)
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
                        if isinstance(mapped, ToolStart):
                            tool_calls_seen += 1
                        if isinstance(mapped, ToolEnd):
                            # #62: attach the full display result (success-stderr
                            # kept) the exec tool stashed under its cleaned output,
                            # so the FE card shows the error that streamed live
                            # instead of a clean "exit_code=0". Keyed by output
                            # (RunContextWrapper tools have no call id).
                            disp = ctx.tool_displays.get(mapped.output, "")
                            if disp:
                                mapped = replace(mapped, display=disp)
                        queue.put_nowait(mapped)

                tail_content, tail_reasoning = splitter.flush()
                if tail_reasoning:
                    queue.put_nowait(MessageDelta(text=tail_reasoning, reasoning=True))
                if tail_content:
                    content_buf.append(tail_content)
                    queue.put_nowait(MessageDelta(text=tail_content))

                # #69 observability: one grep-friendly line per turn — what
                # we sent (model / endpoint / tool knobs) + what shape came
                # back. Lets an operator compare a live turn to a Replay and
                # spot a config-induced 'text instead of tool_call'. Opt-in
                # (WORKSPACE_LLM_TRACE) so production logs stay quiet.
                if trace_enabled():
                    _emit_llm_trace(
                        agent,
                        ctx,
                        runner_base_url=self._base_url,
                        tool_calls=tool_calls_seen,
                        content_text="".join(content_buf),
                    )

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

    async def _run_once_nonstream(  # pragma: no cover — exercised only by the live Ollama test
        self, prompt: str, ctx: AgentToolContext, feedback: str | None
    ) -> AsyncIterator[AgentEvent]:
        """One turn with the model NOT streaming (WORKSPACE_AGENT_STREAM=0): fetch
        the whole response in a single ``Runner.run`` (``get_response``), then emit
        the tool calls + final message from the completed result. Trades live token
        output (and live exec logs) for a clean structured tool_call — the streaming
        aggregator's tool_call corruption can't happen on a one-shot response.

        Same retry/terminal contract as the streamed path: this raises (MaxTurns,
        recovery errors) straight up to ``run()`` which maps them as usual. The
        RepairingModel + args_recovery backstops still apply (``_agent_for``)."""
        assert ctx.agent_config is not None
        agent = _agent_for(
            ctx.agent_config,
            ctx.packages,
            extra_instructions=feedback,
            base_url=self._base_url,
            api_key=self._api_key,
            reasoning_effort=ctx.reasoning_effort,
            app_slug=ctx.app_slug,
            template_profile=ctx.template_profile,
        )
        t0 = time.monotonic()
        prompt_tok = _approx_tokens(len(prompt))
        # No stream → exec stdout can't interleave live; it still lands in the
        # tool's result. Swallow mid-turn pushes so a long exec doesn't error.
        ctx.on_exec_output = lambda b: None
        yield AgentMetrics(phase="up", prompt_tokens=prompt_tok, elapsed_ms=0)
        run_config = RunConfig(
            workflow_name=_trace_workflow_name(ctx), group_id=ctx.investigation_id
        )
        result = await Runner.run(
            agent,
            input=_build_input(ctx.history, prompt),  # ty: ignore[invalid-argument-type]
            context=ctx,
            max_turns=ctx.max_turns or self._max_turns,
            run_config=run_config,
        )
        content_buf: list[str] = []
        tool_calls_seen = 0
        splitter = ThinkSplitter()  # split any inline <think> out of the content
        for item in result.new_items:
            itype = getattr(item, "type", "")
            if itype in _NONSTREAM_TOOL_EVENT:
                mapped = _map_event(_ItemEvent(_NONSTREAM_TOOL_EVENT[itype], item))
                if mapped is None:
                    continue
                if isinstance(mapped, ToolStart):
                    tool_calls_seen += 1
                if isinstance(mapped, ToolEnd):
                    disp = ctx.tool_displays.get(mapped.output, "")
                    if disp:
                        mapped = replace(mapped, display=disp)
                yield mapped
            elif isinstance(item, MessageOutputItem):
                content, reasoning = splitter.feed(ItemHelpers.text_message_output(item))
                if reasoning:
                    yield MessageDelta(text=reasoning, reasoning=True)
                if content:
                    content_buf.append(content)
                    yield MessageDelta(text=content)
        tail_content, tail_reasoning = splitter.flush()
        if tail_reasoning:
            yield MessageDelta(text=tail_reasoning, reasoning=True)
        if tail_content:
            content_buf.append(tail_content)
            yield MessageDelta(text=tail_content)
        if trace_enabled():
            _emit_llm_trace(
                agent,
                ctx,
                runner_base_url=self._base_url,
                tool_calls=tool_calls_seen,
                content_text="".join(content_buf),
            )
        prompt_final, completion_final = _final_tokens(
            _exact_usage(result), prompt_tok, len("".join(content_buf))
        )
        yield AgentMetrics(
            phase="final",
            prompt_tokens=prompt_final,
            completion_tokens=completion_final,
            elapsed_ms=round((time.monotonic() - t0) * 1000),
        )
