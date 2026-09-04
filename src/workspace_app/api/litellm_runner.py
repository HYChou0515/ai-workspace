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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

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
    ToolOutputImage,
)
from agents import MaxTurnsExceeded as _AgentsMaxTurnsExceeded
from agents.agent import StopAtTools
from agents.extensions.models.litellm_model import LitellmModel
from openai.types.shared import Reasoning

from ..agent.args_recovery import (
    ConcatenatedToolCallsError,
    MalformedToolArgsError,
    NonObjectToolArgsError,
)
from ..agent.context import AgentToolContext
from ..agent.header_model import HeaderModel
from ..agent.repairing_model import RepairingModel
from ..agent.tools import build_tools, dedupe_tools, delegation_is_available
from ..apps.subagents import subagents_block
from ..context_budget import (
    ContextLimit,
    LimitLearner,
    deferred_lookup,
    detect_truncation,
    estimate_messages,
    estimate_tokens,
    halve_history,
    history_budget,
    is_context_overflow,
    parse_limit_from_error,
)
from ..context_probe import probe_context_limit, probe_endpoint_limits
from ..failover.rate_limit import is_rate_limited, rate_limit_wait_s
from ..resources import AgentConfig
from ..tokens import ITokenService, LlmCredential
from ..tooling.registry import PackageInfo, build_function_tools
from ..users.labels import speaker_note
from .events import (
    AgentEvent,
    AgentMetrics,
    ContextTrimmed,
    FailoverSwitch,
    MaxTurnsExceeded,
    MessageDelta,
    RateLimited,
    RestoreProgress,
    RunDone,
    RunError,
    TodosUpdated,
    ToolCallParseError,
    ToolEnd,
    ToolLog,
    ToolStart,
)
from .llm_trace import build_trace, format_trace_line, redact_endpoint, trace_enabled

if TYPE_CHECKING:
    from ..factories import LlmEndpoint, SubagentModel
    from ..failover.cooldown import CooldownRegistry

    # #196: per-config busy-aware failover chains, keyed by the primary endpoint
    # (model, base_url) so a config's chain is found without touching the
    # persisted AgentConfig. Built once at startup from the presets.
    FallbackChains = dict[tuple[str, str | None], list[LlmEndpoint]]

_LOGGER = logging.getLogger(__name__)

#: Window decisions already reported (see `_agent_for`), as `(model, window)`.
#: Keyed on the DECISION rather than the model: a deploy states its window once
#: instead of every turn, but a window that later changes — the learner supplying
#: what a rejection taught it — is stated again instead of hiding behind the first
#: turn's line.
_NUM_CTX_SEEN: set[tuple[str, int | None]] = set()

#: How long "the proxy did not say" is believed before asking again.
#:
#: Every other cached lookup in this file caches a fact about software we ship.
#: This one caches a fact about a config file on ANOTHER service — and the
#: documented remedy for a deployment stuck on an `unknown` ceiling is that
#: somebody adds `max_input_tokens` to exactly that file. Remembering the
#: negative for the life of the pod would make their fix take effect only when
#: WE are next redeployed: a dependency between two services that neither of
#: them is told about, presenting as "I added it and nothing happened".
#:
#: It also bounds the other way a negative gets recorded: a probe that lands
#: while the proxy is restarting gets a legitimate-looking "no", and nothing
#: about that answer says it was taken from a dead endpoint.
#:
#: Ten minutes because the cost is one GET per (model, endpoint) per pod per
#: window — nothing — while the thing it buys is that a correct proxy config
#: starts working on its own. An ANSWER never expires; only silence does.
DECLARED_RETRY_S = 600.0

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


def _build_input(
    history: list[dict[str, str]],
    prompt: str,
    image_urls: Sequence[str] = (),
) -> str | list[dict[str, Any]]:
    """The SDK `input` for this turn: a plain string when there's no history,
    else the prior dialogue items followed by this turn's user message — so the
    agent has cross-turn memory (#17).

    `image_urls` (data: URLs) are inlined into THIS turn's user message as
    `input_image` parts so a vision-capable main model sees the attached images
    directly — no `read_image` round-trip through the separate VLM. Only the
    live turn's message is multimodal; the persisted history stays text (the
    image also lives on as a workspace file the model can `read_image` later).
    Empty ⇒ the text-only path is byte-for-byte unchanged."""
    user_content: str | list[dict[str, Any]] = prompt
    if image_urls:
        user_content = [
            {"type": "input_text", "text": prompt},
            *({"type": "input_image", "image_url": url} for url in image_urls),
        ]
    if not history:
        # No history + no images → keep the bare-string fast path; multimodal
        # content still needs a message list even without history.
        return prompt if not image_urls else [{"role": "user", "content": user_content}]
    # #199 — the SDK prepends the system prompt itself, so the replayed history
    # must never carry a `system` item; a mid-conversation one makes providers
    # reject the call with "system message must be at the beginning". Fail loud
    # at our boundary rather than as an opaque provider error.
    assert not any(m.get("role") == "system" for m in history), (
        "history must not contain a system message"
    )
    return [*history, {"role": "user", "content": user_content}]


# #748: the deltas that ARE the model's output — what the provider counts in
# `completion_tokens`, and therefore what both sides of tok/s must agree on. This
# is a whitelist rather than "anything `_delta_channel` does not ignore", because
# that classifier ends in a catch-all: audio deltas carry base64 bytes that are
# in no token count, and would inflate the denominator with nothing on top.
_GENERATED_OUTPUT = frozenset(
    {
        "response.output_text.delta",
        "response.refusal.delta",
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
        # The model wrote these too, and the provider bills them; they are kept
        # out of the answer TEXT (see `_delta_channel`) but not out of the count.
        "response.function_call_arguments.delta",
    }
)


def _is_generated_output(event_type: str) -> bool:
    """Whether this delta is model output the provider counts as completion."""
    return event_type in _GENERATED_OUTPUT


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


class _GenerationClock:
    """How long the model spent GENERATING this turn (#748) — the denominator
    tok/s needs, which is not the turn's wall clock.

    The wall clock also carries TTFT, every tool call, every retry and every
    rate-limit hold. TTFT is the damaging one: it grows with the prompt, so
    dividing by it makes the SAME model look slower as the conversation gets
    longer — the misreading a person is most likely to make. llama.cpp and vLLM
    report prompt-eval and generation separately for this reason.

    So the clock runs from the first token to the last, and a turn with several
    round trips sums its stretches. Where a stretch ENDS is told to it via
    :meth:`pause` rather than inferred from how big a gap looks: the runner
    already knows when the model stopped to call a tool, and a duration
    threshold would be a rule invented here that nothing else agrees with.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._started: float | None = None  # first token of the open stretch
        self._last: float | None = None  # most recent token of it
        self._closed_s = 0.0  # stretches already ended

    def token(self) -> None:
        """A token arrived. Opens a stretch if none is open."""
        t = self._now()
        if self._started is None:
            self._started = t
        self._last = t

    def pause(self) -> None:
        """Generation stopped (the model went off to call a tool). Banks the
        open stretch so the gap that follows is not counted."""
        if self._started is not None and self._last is not None:
            self._closed_s += self._last - self._started
        self._started = None
        self._last = None

    def elapsed_ms(self) -> int | None:
        """Total generation time, or None when there is no span to report.

        Two timestamps are the minimum: a single delta gives one, so a stretch
        of one token has no duration. The earlier form inferred "nothing ever
        arrived" from the accumulator being 0.0, which cannot tell that apart
        from stretches that each measured exactly zero — so the same situation
        answered `None` when the stretch had been banked and `0` when it was
        still open. Zero is the worse of the two: it divides into an infinite
        rate, and the record's own contract says a number means measured.
        """
        open_s = (
            self._last - self._started
            if self._started is not None and self._last is not None
            else 0.0
        )
        total = self._closed_s + open_s
        # Guard the ROUNDED value: a guard on seconds let a sub-millisecond span
        # through as `0`, the one number this field documents as impossible.
        ms = round(total * 1000)
        return ms if ms > 0 else None


def _effective_model(model: Any, configured: str) -> str | None:
    """The model that actually served, falling back to the configured name.

    #748. A single-endpoint turn hands us a `LitellmModel`, which carries its
    own `.model`. A failover turn hands us a `FallbackModel`, which does not —
    so the previous form of this lookup (inline in the #69 trace) silently
    returned the CONFIGURED name, making it wrong in exactly the case where the
    two differ and the question is worth asking. `served_model` is that chain's
    answer, and it is None until something has actually answered, which falls
    through here rather than claiming the head.
    """
    served = getattr(model, "served_model", None)
    if served:
        return str(served)
    # "" is neither a model name nor the documented "we don't know", and it is
    # what an absent `ctx.agent_config` produced. None says the honest thing.
    return str(getattr(model, "model", "") or configured) or None


def _measured_tokens(usage: tuple[int, int] | None) -> tuple[int | None, int | None]:
    """The provider's own counts for the RECORD, or None where it gave none.

    #748. Distinct from `_final_tokens`, which settles what to DISPLAY and
    deliberately substitutes an estimate so the live line never reads "↑0 ↓0".
    A record has the opposite requirement: a figure nobody can tell apart from
    a measurement is worse than no figure at all.

    A reported `0` counts as "did not count", not as a measured zero — a reply
    exists, so something was read and something was written. Local Ollama
    streams zeros routinely; storing them would drag every average computed
    over this column toward nothing. Applied per field, because a provider can
    report one and not the other.
    """
    if usage is None:
        return None, None
    return (usage[0] or None), (usage[1] or None)


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


def _live_prompt_tokens(ctx: AgentToolContext, prompt: str) -> int:
    """The ↑ figure while the request is still in flight (#739).

    It used to be `len(prompt) / 4` — the user's own message, which is not the
    context size and does not grow as the window fills. The final phase then
    reported the provider's count of the WHOLE request, so the number jumped by
    an order of magnitude the moment the turn ended and the bar meant two
    different things within one turn.

    Same figure the silent-truncation check compares against, deliberately: one
    meaning, one arithmetic, two readers."""
    return _sent_estimate(ctx, prompt)


def _turn_instructions(ctx: AgentToolContext, feedback: str | None) -> str | None:
    """Per-turn additions to the system prompt: the #242 speaker note (who the
    agent is replying to in a shared workspace), the #537 knowledge-source
    allowance (how many times this reply may consult each source — stated up
    front so the agent budgets deliberately instead of discovering the ceiling
    by being refused), the #pm entity-schema brief (the item's record types +
    fields + status vocab, so the agent creates valid records without guessing),
    then any retry feedback. `None` when none is present, so `_agent_for` leaves
    the base prompt unchanged."""
    parts = [
        s
        for s in (
            speaker_note(ctx.speaker),
            # Only when the turn actually holds `run_agent` — advertising an
            # index for a tool that was never built spends the model's next step
            # on a call that cannot resolve.
            subagents_block(ctx.subagent_defs)
            if delegation_is_available(
                ctx.agent_config.allowed_tools if ctx.agent_config else None,
                bool(ctx.subagent_defs),
            )
            else "",
            ctx.search_allowance_note,
            ctx.entity_schema_note,
            feedback,
        )
        if s
    ]
    return "\n\n".join(parts) if parts else None


def _agent_for(
    config: AgentConfig,
    packages: list[PackageInfo] | None = None,
    unavailable_tools: Mapping[str, str] | None = None,
    extra_instructions: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    context_window: int | None = None,
    app_slug: str | None = None,
    template_profile: str | None = None,
    has_subagents: bool = False,
    skills_reachable: bool | None = None,
    subagent_models: tuple[SubagentModel, ...] = (),
    fallback_chains: FallbackChains | None = None,
    cooldown_registry: CooldownRegistry | None = None,
    on_failover_switch: Callable[[str, str], None] | None = None,
    on_rate_limit_hold: Callable[[str, float], None] | None = None,
    resolve_credential: Callable[[str | None], LlmCredential] = LlmCredential,
    stream_deadlines: tuple[float, float] | None = None,
) -> Agent[AgentToolContext]:
    base = config.system_prompt or ""
    if extra_instructions:
        base = f"{base}\n\n{extra_instructions}".strip()
    # `read_skill` is opted in by whether this turn can load any skill (issue
    # #29 / §A) — `ctx.skills_reachable` when the turn context answered it,
    # otherwise derived from the profile. build_tools decides once.
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
    tools = list(
        build_tools(
            config.allowed_tools,
            app_slug=app_slug,
            profile=template_profile,
            has_subagents=has_subagents,
            skills_reachable=skills_reachable,
            subagent_models=subagent_models,
        )
    )
    # Same tri-state for package tools (the colon-syntax expansion):
    # symmetric with build_tools above so bundled RCA presets
    # (allowed_tools=None) still expose every package command.
    if packages:
        tools.extend(build_function_tools(packages, allowed=config.allowed_tools))
    # Last stop before the model sees them, and the only place every source is in
    # one list: built-ins, the conditional grants, and the package commands —
    # which `build_function_tools` rejects collisions AMONG, but not against
    # ours. Two definitions of one name is a payload the provider refuses
    # wholesale, so the turn dies for a reason nothing in it explains.
    tools = dedupe_tools(tools)  # first-wins: the built-in outranks a package's copy
    # Append the resolved tool inventory (name + description + JSON args
    # schema) to the system prompt so small local LLMs don't confuse
    # provisioned function tools with shell binaries (see plan §B.10 /
    # agent/tool_prompt.py).
    from ..agent.tool_prompt import (
        format_disabled_tools_for_prompt,
        format_tools_for_prompt,
        format_unavailable_tools_for_prompt,
    )

    tools_section = format_tools_for_prompt(tools)
    if tools_section:
        base = f"{base}\n\n{tools_section}".strip() if base else tools_section
    # #480: after the callable inventory, advertise the App-declared tools that
    # resolved OFF for this turn — name + one-line description only, NOT added to
    # `tools`. The agent learns they exist (so it avoids them by default) and can
    # ask the user to enable one in the tool picker. Metas come from the same
    # display catalog the picker uses, so built-in + package selectors both work.
    # `run_agent` is left out unless turning it on would actually do something.
    # This section's promise is "ask the user to enable one" — but enabling
    # `run_agent` alone, on an item with no `.agent/` files, leaves the picker
    # showing it ON while `build_tools` still strips it, and it drops off this
    # list too, so nothing anywhere explains the dead switch. Advertising a knob
    # that silently does nothing is worse than not advertising it.
    offerable = [
        t
        for t in config.disabled_tools
        if t != "run_agent"
        or delegation_is_available([*(config.allowed_tools or []), t], has_subagents)
    ]
    if offerable:
        from ..tooling.catalog import picker_units

        # `picker_units` yields one meta per name and the renderer is non-empty
        # for non-empty metas, so this is always a real section here.
        disabled_section = format_disabled_tools_for_prompt(picker_units(offerable, packages or []))
        base = f"{base}\n\n{disabled_section}".strip() if base else disabled_section
    # #674: and the third-party tools that could not be loaded at all. Kept
    # separate from the section above: there is no switch for the user to
    # flip here, so sending the agent to the tool picker would be a dead end.
    unavailable_section = format_unavailable_tools_for_prompt(unavailable_tools or {})
    if unavailable_section:
        base = f"{base}\n\n{unavailable_section}".strip() if base else unavailable_section
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
    # #113 Layer 1: per-config anti-repetition sampling penalties. freq/presence
    # are native ModelSettings fields; repetition_penalty is non-standard so it
    # rides extra_body (litellm forwards it). All default None = inherit (the SDK
    # omits None params); honoured by vLLM, silently dropped by Ollama's Go
    # runner — the stream guard is the backend-independent backstop.
    freq, pres = config.frequency_penalty, config.presence_penalty
    rep_body = (
        {"repetition_penalty": config.repetition_penalty}
        if config.repetition_penalty is not None
        else {}
    )
    # Ollama opens its OWN default window (4,096), not the model's, and drops the
    # overflow silently from the FRONT — so an over-long turn loses the App's
    # identity and instructions and keeps the tool inventory that sits at the end,
    # then answers fluently from a prompt it never saw the start of. Measured: a
    # turn sending ~11,000 tokens (6.5k system prompt + 4.5k tool schemas) was
    # reported back as exactly 4,096 read, three calls out of three.
    #
    # `context_window` is the SAME figure `resolve_context_limit` already gives the
    # turn builder to size the history budget, so what we plan for and what we ask
    # the endpoint to open are one number, not two. `None` (nothing could answer
    # how big this endpoint is) leaves the endpoint's own default alone — a guess
    # here would be the invented-constant defect #624 exists for.
    #
    # It must ride `extra_args` (splatted as a top-level completion kwarg), NOT
    # `extra_body`: measured against the daemon on an over-long prompt, top-level
    # read 9,023 tokens where the same value in `extra_body` still read 4,096.
    is_ollama = config.model.startswith(("ollama/", "ollama_chat/"))
    ollama_args = {"num_ctx": context_window} if is_ollama and context_window else {}
    # Report the decision once — an operator's first question is how big a window
    # this deploy settled on, and answering only on failure leaves the working case
    # unknowable. Non-Ollama endpoints size their own window, so there is no
    # decision of ours to report and their silence is correct on every turn.
    if is_ollama and (key := (config.model, context_window)) not in _NUM_CTX_SEEN:
        _NUM_CTX_SEEN.add(key)
        if context_window:
            _LOGGER.info(
                "litellm_runner: opening a %d-token window on %s (num_ctx)",
                context_window,
                config.model,
            )
        else:
            # The one combination that still truncates in silence: Ollama WILL
            # impose a window, and nothing could tell us how big to ask for — so it
            # serves its own default and drops the rest, from the front. Unsaid,
            # that reads as a model which stopped following its prompt.
            _LOGGER.warning(
                "litellm_runner: no context window resolved for %s, so no num_ctx "
                "is sent — Ollama will serve its own default and silently drop "
                "anything over it. Set agents.context_limit, or bake num_ctx into "
                "the model's Modelfile.",
                config.model,
            )
    if reasoning_effort == "none":
        from ..agent.reasoning import reasoning_off_kwargs

        off = reasoning_off_kwargs(config.model)
        # think → extra_args (top-level kwarg); extra_body → extra_body.
        model_settings = ModelSettings(
            extra_args={**ollama_args, **({"think": off["think"]} if "think" in off else {})}
            or None,
            extra_body={**(off.get("extra_body") or {}), **rep_body} or None,
            frequency_penalty=freq,
            presence_penalty=pres,
            # #748/#751: ask for usage ONLY where an operator has vouched for
            # the endpoint. Asking everywhere lets litellm answer on a silent
            # provider's behalf, and its tokenizer's guess is indistinguishable
            # from a measurement once it lands in the record.
            include_usage=True if config.reports_usage else None,
        )
    elif reasoning_effort:
        # effort is validated to low/medium/high by the request body.
        model_settings = ModelSettings(
            reasoning=Reasoning(effort=reasoning_effort),  # ty: ignore[invalid-argument-type]
            extra_args=ollama_args or None,
            extra_body=rep_body or None,
            frequency_penalty=freq,
            presence_penalty=pres,
            # #748/#751: ask for usage ONLY where an operator has vouched for
            # the endpoint. Asking everywhere lets litellm answer on a silent
            # provider's behalf, and its tokenizer's guess is indistinguishable
            # from a measurement once it lands in the record.
            include_usage=True if config.reports_usage else None,
        )
    else:
        model_settings = ModelSettings(
            extra_args=ollama_args or None,
            extra_body=rep_body or None,
            frequency_penalty=freq,
            presence_penalty=pres,
            # #748/#751: ask for usage ONLY where an operator has vouched for
            # the endpoint. Asking everywhere lets litellm answer on a silent
            # provider's behalf, and its tokenizer's guess is indistinguishable
            # from a measurement once it lands in the record.
            include_usage=True if config.reports_usage else None,
        )
    # Per-config LLM endpoint (new schema's agents.presets.<x>.llm) wins
    # over the runner's constructor default — empty strings mean
    # "inherit from runner" so a single-endpoint deploy still works.
    eff_base_url = config.llm_base_url or base_url
    # Per-user credential seam: resolve this endpoint's api_key + headers on the
    # acting user's behalf (identity when no user / no service — see
    # LitellmAgentRunner._credential_resolver).
    eff_credential = resolve_credential(config.llm_api_key or api_key)

    def _build_model(
        model_id: str,
        b_url: str | None,
        credential: LlmCredential,
        timeout: float | None = None,
    ) -> Model:
        """Build one inner SDK model for one endpoint — the #76 repairing
        backstop over the raw LiteLLM model. Shared by the
        single-endpoint case and each entry of a busy-aware FallbackModel
        (``timeout`` bounds a non-streaming decide/args call so it can fail over)."""
        m: Model = LitellmModel(model=model_id, base_url=b_url, api_key=credential.api_key)
        # The credential's headers are PER ENDPOINT, so they are applied here rather
        # than on the turn's ModelSettings: a FallbackModel hands the same settings
        # to whichever endpoint it switches to, which would send one gateway's
        # session cookie to another host.
        if credential.headers:
            m = HeaderModel(m, credential.headers)
        # #76 BACKSTOP (always on): sanitize malformed tool-call JSON at the output
        # boundary so a probabilistic small model can't crash/poison/abort the turn.
        # Leave this line on. To toggle the *self-repair* layer (recovering the
        # model's intent), comment the marked `repair_tool_args(...)` line inside
        # agent/repairing_model.py — the backstop still sanitizes either way.
        return RepairingModel(m)

    # #196 busy-aware failover: when this config's endpoint heads a fallback chain
    # (preset.fallbacks), wrap the per-endpoint models in a FallbackModel that
    # switches on a busy/slow/failed model. Single-endpoint configs take the plain
    # path (chain absent / length 1) — byte-for-byte the prior behaviour.
    chain = (fallback_chains or {}).get((config.model, eff_base_url))
    if chain is not None and len(chain) >= 2 and cooldown_registry is not None:
        from ..failover.model import FallbackModel
        from ..failover.observe import make_switch_logger

        log_switch = make_switch_logger("agent")

        def on_switch(model_label: str, cause: BaseException) -> None:
            log_switch(model_label, cause)
            # #249/#131: also surface the switch live so the FE can show a
            # transient "model busy, switched" notice (the deferred in-chat
            # degradation note). Logging stays regardless.
            if on_failover_switch is not None:
                on_failover_switch(model_label, type(cause).__name__)

        model: Model = FallbackModel(
            chain,
            cooldown_registry,
            make_model=lambda e: _build_model(
                e.model, e.base_url, resolve_credential(e.api_key), e.idle_s
            ),
            on_switch=on_switch,
            # #742's visibility rule, inside the chain: the hold happens mid-turn
            # and can last minutes, so it must SAY so — the FE shows the same
            # transient "rate limited, retrying in Ns" notice the turn loop's own
            # holds get.
            on_hold=on_rate_limit_hold,
        )
    else:
        model = _build_model(config.model, eff_base_url, eff_credential)
        # #493: a turn must always end. FallbackModel carries deadlines, but it
        # is only built for a chain of two or more endpoints — so the DEFAULT
        # single-endpoint deploy had no bound at all, and a provider that
        # accepted the request then went quiet hung the turn forever with no
        # event, nothing persisted and no watchdog.
        #
        # The first-event bound is the turn GIVE-UP deadline, NOT the failover
        # ttft: 8s is a "this endpoint is busy, switch" signal, and with a single
        # endpoint there is nowhere to switch, so applying it would only kill
        # turns for being slow (14.7s median / 28.5s p90 on this deploy). Slow is
        # not dead. `idle_s` stays the real death signal — output started, then
        # stopped.
        if stream_deadlines is not None:
            from ..agent.deadline_model import DeadlineModel

            give_up_s, idle_s = stream_deadlines
            model = DeadlineModel(model, first_event_s=give_up_s, idle_s=idle_s)
    return Agent[AgentToolContext](
        name=config.name,
        instructions=base or None,
        model=model,
        model_settings=model_settings,
        tools=tools,  # ty: ignore[invalid-argument-type]  # list[FunctionTool] ⊂ list[Tool]
        tool_use_behavior=ask_user_stop_behaviour([t.name for t in tools]),
    )


ASK_USER_TOOL = "ask_user"


def ask_user_stop_behaviour(
    tool_names: Sequence[str] | None,
) -> StopAtTools | Literal["run_llm_again"]:
    """End the turn when the agent asks the user something.

    `ask_user` posts a question and does not wait for it — the answer arrives
    as the user's next message, in the next turn. Without stopping here the
    model keeps generating with no answer in hand, and what it generates is an
    answer it made up. `StopAtTools` is the SDK's own mechanism for this, so
    the guarantee is structural rather than an instruction the model may
    ignore (local models routinely do).

    Only applied when the turn actually has the tool: a blanket stop would end
    every turn at its first tool call."""
    if tool_names and ASK_USER_TOOL in tool_names:
        return StopAtTools(stop_at_tool_names=[ASK_USER_TOOL])
    return "run_llm_again"


def _rate_limit_hold_emitter(
    queue: asyncio.Queue[AgentEvent | object],
) -> Callable[[str, float], None]:
    """A per-turn sink that turns a FallbackModel rate-limit hold into a live
    ``RateLimited`` event on this turn's stream — the same transient notice the
    turn loop's own holds emit (#742: a held turn must say it is held, because a
    stated window can be minutes and silent minutes read as a hang).
    ``put_nowait`` is safe — the hold fires on the same event loop that drains
    the queue."""

    def emit(_from_model: str, seconds: float) -> None:
        queue.put_nowait(RateLimited(seconds=seconds))

    return emit


def _failover_emitter(queue: asyncio.Queue[AgentEvent | object]) -> Callable[[str, str], None]:
    """#249/#131: a per-turn sink that turns a FallbackModel switch into a live
    ``FailoverSwitch`` event on this turn's stream (so the FE shows a transient
    'model busy, switched' notice). ``put_nowait`` is safe — the switch fires on
    the same event loop that drains the queue."""

    def emit(from_model: str, reason: str) -> None:
        queue.put_nowait(FailoverSwitch(from_model=from_model, reason=reason))

    return emit


def _todos_emitter(queue: asyncio.Queue[AgentEvent | object]) -> Callable[[list[Any]], None]:
    """#613: a per-turn sink that turns the `update_todos` tool's freshly-written
    list into a live ``TodosUpdated`` event on this turn's stream, so the FE's
    pinned checklist panel updates while the turn is still running. Items become
    plain ``{"text", "status"}`` dicts (the wire shape). ``put_nowait`` is safe —
    the tool fires on the same event loop that drains the queue."""

    def emit(items: list[Any]) -> None:
        queue.put_nowait(TodosUpdated(items=[{"text": t.text, "status": t.status} for t in items]))

    return emit


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
    # #624: a length rejection must NOT get the catch-all "try again" — that
    # hint is appended to the very prompt that was too long, so it makes the
    # next attempt bigger. Say what would actually help instead.
    from ..context_budget import is_context_overflow

    if is_context_overflow(low):
        return (
            "The previous attempt was rejected because the request was too long "
            "for this model's context window. Work with less material at a time "
            "and keep your answer shorter."
        )
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
        raw_out = getattr(item, "output", "")
        # `read_image` hands a vision main model the raw image as a
        # `ToolOutputImage`; the model sees the pixels via the SDK's own
        # function_call_output (an input_image part). Our event/persistence layer
        # must NOT stringify it — a `str(ToolOutputImage)` repr embeds the whole
        # base64 data URL, which would bloat the SSE stream and, worse, replay as
        # a giant text blob into the next turn's context. Surface a concise note.
        out_text = (
            "[image read directly by the vision model]"
            if isinstance(raw_out, ToolOutputImage)
            else str(raw_out)
        )
        return ToolEnd(
            call_id=_call_id(item.raw_item),
            output=out_text,
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
        # The trace is a log line, not a record: "" would be an empty column, so
        # say what happened. The RECORD keeps None — see `_effective_model`.
        model = (
            _effective_model(getattr(agent, "model", None), cfg.model if cfg else "") or "(unknown)"
        )
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


def _shrink_history(
    history: list[Any], stated_limit: int | None, *, overhead_tokens: int = 0
) -> list[Any]:
    """Make the next attempt smaller (#624).

    When the rejection stated a ceiling we know the budget, so the reduction
    algorithm decides what to give up — the same decision, derived the same way,
    as the pre-send path. "The same way" has to include the arithmetic, not just
    the algorithm: a rejection states the model's TOTAL ceiling, while the
    request also carries the system prompt and every tool schema (74,222
    characters on a measured item). Aiming history at the raw stated figure
    judges an over-long thread to "fit", so the algorithm returns it untouched
    and we fall through to blind halving — several more rejected round-trips,
    ending in an error that blames the user's message for what the prompt spent.

    Without a stated ceiling there is no budget to aim at, so a blind halving is
    all that is available; it converges in a few rounds and floors at one
    message."""
    from ..context_reducers import default_reducer

    if stated_limit is None:
        return halve_history(history)
    # `stated_limit` is a number, so a budget always comes back. 0 is its honest
    # floor — the overhead alone already fills the window — and drives the last
    # stage rather than needing a special case.
    budget = (
        history_budget(
            ContextLimit(tokens=stated_limit, source="learned"),
            overhead_tokens=overhead_tokens,
        )
        or 0
    )
    result = default_reducer().reduce(history, budget=budget, estimate=estimate_messages)
    # Progress must be measured in TOKENS, not messages: the cheapest stage
    # folds bulky tool output and keeps every message, so a "are there fewer
    # messages?" guard would discard a fold that already freed most of the
    # budget and blindly halve instead — throwing away the task to keep a data
    # dump, which is the behaviour this issue exists to remove. Something must
    # still shrink, or the retry would resend an identical request.
    if estimate_messages(result.messages) < estimate_messages(history):
        return result.messages
    return halve_history(history)


def _sent_estimate(ctx: AgentToolContext, prompt: str) -> int:
    """Roughly how many tokens this request carried: the per-turn overhead the
    context builder already measured (system prompt + tool schemas), plus the
    replayed history and this turn's message (#624).

    Only ever compared against the provider's own figure with a wide ratio, so
    it needs to be the right order of magnitude, not exact — and the overhead
    term is reused rather than recomputed so the comparison costs nothing."""
    total = max(0, ctx.context_overhead_tokens)
    if ctx.history:
        total += estimate_tokens(str(ctx.history))
    return total + estimate_tokens(prompt or "")


def _should_retry(
    *, progress_made: bool, attempt: int, max_retries: int, error_text: str = ""
) -> bool:
    """Decide whether to restart the turn after `_run_once` raised.

    Issue #26: the agents-SDK can't resume a stream mid-turn — a restart
    re-runs the prompt from scratch, throwing away any text the user has
    already seen + any tool calls already executed. So only retry when
    nothing user-visible has streamed yet (the early small-model JSON-parse
    failures we hand a hint back for). Once there's progress, showing the
    error wins over clobbering the chat.

    #624: it must also look at WHAT failed. This used to consider only
    "did anything stream" and "how many attempts", so a deterministic
    rejection — the prompt did not fit, a parameter was malformed — was
    re-sent unchanged up to three times. Each attempt failed identically, and
    the hint appended between them ("the previous attempt failed… try again")
    was added to a prompt that was already too long. A `400` is the provider
    telling us the request is wrong; repeating it is not a strategy.
    """
    if progress_made:
        return False
    if error_text and _is_deterministic_rejection(error_text):
        return False
    return attempt <= max_retries


def _is_deterministic_rejection(message: str) -> bool:
    """Whether this error will fail identically no matter how often we resend.

    Length rejections and malformed-request errors are decided by the request
    itself, so a retry is pure latency. Everything else (timeouts, blips,
    small-model JSON garble) keeps the #76 retry-with-a-hint behaviour."""
    from ..context_budget import is_context_overflow

    low = (message or "").lower()
    return is_context_overflow(low) or "invalid_request_error" in low


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
        fallback_chains: FallbackChains | None = None,
        cooldown_registry: CooldownRegistry | None = None,
        token_service: ITokenService | None = None,
        stream_deadlines: tuple[float, float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rate_limit_budget_s: float = 120.0,
    ) -> None:
        # #94: no runner-level default config. Every turn's config arrives on
        # ctx.agent_config (resolved per-item via the AppCatalog / KB / wiki
        # catalogs); run() fails loud if it's missing.
        self._max_retries = max_retries
        # #624: what we have learned about each endpoint's real ceiling, from
        # the rejections it hands back. Per-pod and in-memory on purpose — a
        # cache that re-learns in one turn, never a durable claim that can go
        # stale and quietly mis-govern a deploy.
        self._limits = LimitLearner()
        # Endpoints already asked (value or None), so a silent one is asked once.
        self._probed: dict[tuple[str, str], int | None] = {}
        # The same, for what a PROXY in front of the model says it was
        # configured with. Separate cache because it is a different question
        # with a different answer shape, and separate from the learner because
        # what a proxy relays is a claim, not something the endpoint did.
        self._endpoint_declared: dict[tuple[str, str], Any] = {}
        # When each of those keys was last asked, so a NEGATIVE can expire. See
        # `DECLARED_RETRY_S`.
        self._declared_at: dict[tuple[str, str], float] = {}
        self._clock: Callable[[], float] = time.monotonic
        self._declared_probe: Callable[[str | None, str], Any] = lambda base_url, model: (
            probe_endpoint_limits(base_url=base_url, model=model)
        )
        # The endpoint-asking seam. A plain attribute (not a method) so it is an
        # injectable dependency like the other sinks here — every failure inside
        # `probe_context_limit` already degrades to None.
        self._probe: Callable[[str | None, str], int | None] = lambda base_url, model: (
            probe_context_limit(base_url=base_url, model=model)
        )
        self._max_turns = max_turns
        # Chat LLM endpoint (global; see factories.Settings). None → LiteLLM's
        # own provider env / Ollama defaults.
        self._base_url = base_url
        self._api_key = api_key
        # Per-user token seam. There is no universal system key — each preset
        # configures its own endpoint key — so the token service resolves PER
        # ENDPOINT: given the speaker + the key that endpoint would otherwise use,
        # it returns the key to actually use. V1 (PassthroughTokenService) returns
        # it unchanged; a real user-keyed source swaps in later. None = no service
        # wired → every key passes through untouched (behaviour unchanged).
        self._token_service = token_service
        # #196 busy-aware failover (None when no preset declares fallbacks).
        self._fallback_chains = fallback_chains
        self._cooldown_registry = cooldown_registry
        # #493 (give_up_s, idle_s) for a SINGLE-endpoint turn, so a provider that
        # goes quiet ends the turn with a real error instead of hanging forever.
        # `give_up_s` is the turn deadline, NOT the failover ttft — see the note
        # at the wiring site. The failover path carries its own bounds. None = no
        # bound (what every deploy without `fallbacks:` used to get).
        self._stream_deadlines = stream_deadlines
        # How the turn waits out a rate limit. `asyncio.sleep`, never
        # `time.sleep` — this runs on the loop that serves every other request,
        # and parking it here would stall the whole pod for the whole window.
        # Injectable so the suite never actually waits.
        self._sleep: Callable[[float], Awaitable[None]] = sleep or asyncio.sleep
        # Total time one turn may spend waiting out rate limits before it gives
        # up and says so. A COUNT would be the wrong bound — three holds mean
        # 3s against one provider and 3 minutes against another — and without
        # any bound a throttled endpoint parks the turn forever. Matches the
        # chain-level `failover.total_deadline_s` default.
        self._rate_limit_budget_s = rate_limit_budget_s

    async def _credential_resolver(
        self, ctx: AgentToolContext
    ) -> Callable[[str | None], LlmCredential]:
        """A SYNC ``key -> LlmCredential`` mapping for THIS turn's endpoints, resolved
        through the token service on the acting user's behalf.

        The token service is async but the models are built synchronously (a
        fallback endpoint's model is built deep inside the SDK), so we pre-resolve
        every key the turn can use — the primary ``config.llm_api_key or
        self._api_key`` plus each fallback endpoint's ``e.api_key`` — into a map,
        and hand ``_agent_for`` a sync lookup.

        Whose behalf: the ``speaker`` when a person is in the room, else
        ``acting_user`` — a background turn (a workflow step, a card-gen pass) has
        no speaker but still runs for someone, and the gateway still has to know
        whose quota to charge. Neither, or no service → identity (every key
        unchanged, no headers), so an unattributed turn is byte-for-byte as before.

        ``ctx.call_lane`` rides along so the source can hand back a lane-appropriate
        credential — that is the whole mechanism by which a background job gets the
        tighter rate limit."""
        ts = self._token_service
        config = ctx.agent_config
        user_id = ctx.speaker.id if ctx.speaker is not None else (ctx.acting_user or None)
        if ts is None or user_id is None or config is None:
            return lambda key: LlmCredential(key)
        eff_base_url = config.llm_base_url or self._base_url
        raw_keys = {config.llm_api_key or self._api_key}
        chain = (self._fallback_chains or {}).get((config.model, eff_base_url))
        if chain is not None:
            raw_keys.update(e.api_key for e in chain)
        resolved: dict[str | None, LlmCredential] = {}
        for key in raw_keys:
            resolved[key] = await ts.get_credential(user_id, key, ctx.call_lane)
        return lambda key: resolved.get(key, LlmCredential(key))

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        # #94: no silent fallback. Every turn must arrive with a resolved
        # agent_config (App items resolve via the AppCatalog; KB/wiki via their
        # catalogs). A None here means resolution failed upstream — fail loud
        # rather than run some default agent the operator never picked.
        if ctx.agent_config is None:
            _LOGGER.warning(
                "litellm_runner: no agent config resolved for turn (investigation=%s)",
                ctx.investigation_id,
            )
            yield RunError(
                message="no agent config resolved for this turn — the item could not "
                "be matched to an App (check the item id / App registration)"
            )
            yield RunDone()
            return
        feedback: str | None = None
        attempt = 0
        # Rate limits are held out separately from `attempt` — see the branch in
        # the handler below for why the two budgets must not be one.
        rate_limited = 0
        waited_s = 0.0
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
                _LOGGER.warning(
                    "litellm_runner: turn exceeded max turns (investigation=%s) — terminal",
                    ctx.investigation_id,
                )
                yield MaxTurnsExceeded(turns=ctx.max_turns or self._max_turns)
                yield RunDone()
                return
            except Exception as exc:  # noqa: BLE001 — every other failure becomes a hint or final error
                if is_rate_limited(exc) and not progress_made:
                    # The endpoint is healthy and told us to slow down, so this is
                    # the one failure the loop must WAIT out rather than re-send.
                    # A single-endpoint deploy has no chain to absorb it — this is
                    # the only guard it gets.
                    #
                    # It is charged to its OWN budget, not `attempt`: `max_retries`
                    # exists for a small model garbling its tool call (#76), a
                    # different failure on a different timescale, and spending it
                    # here gives up on a limit that would have cleared. The bound
                    # is total time held rather than a count, because that is what
                    # the caller actually cares about — and `progress_made` still
                    # blocks the retry above, since a stream already seen cannot be
                    # restarted (#26).
                    held = rate_limit_wait_s(exc, attempt=rate_limited + 1)
                    if waited_s + held <= self._rate_limit_budget_s:
                        rate_limited += 1
                        waited_s += held
                        _LOGGER.warning(
                            "litellm_runner: rate-limited (hold %d) — waiting %.1fs "
                            "before retrying the same endpoint (investigation=%s)",
                            rate_limited,
                            held,
                            ctx.investigation_id,
                        )
                        yield RateLimited(seconds=held)
                        await self._sleep(held)
                        continue
                    _LOGGER.warning(
                        "litellm_runner: still rate-limited after %.1fs — giving up "
                        "(investigation=%s)",
                        waited_s,
                        ctx.investigation_id,
                    )
                    yield RunError(
                        message=(
                            "這一輪送不出去:模型服務持續回報請求過於頻繁,"
                            f"已等待約 {int(waited_s)} 秒仍未恢復。請稍後再試一次。"
                        )
                    )
                    yield RunDone()
                    return
                attempt += 1
                # #624: an over-long request is the one failure with a productive
                # answer — send less. Repeating it unchanged cannot work, and
                # simply giving up strands the conversation for good: the failure
                # is persisted, so the next turn replays a LONGER thread and is
                # rejected again, forever. Shrink, remember what it told us, and
                # try once more.
                text = f"{type(exc).__name__}: {exc}"
                if is_context_overflow(text):
                    if (stated := parse_limit_from_error(text)) is not None:
                        self._limits.learn_exact(
                            ctx.agent_config.model, self._base_url, limit=stated
                        )
                    # This branch deliberately bypasses the attempt counter — an
                    # over-long request is the one failure with a productive
                    # answer, and a budget of three would strand a long thread.
                    # Its termination therefore rests ENTIRELY on every round
                    # being smaller than the last, so that is checked HERE
                    # rather than trusted to the callee: when the guarantee
                    # lapsed, no test went red, the suite simply never finished
                    # — and in production it is an unbounded run of rejected
                    # LLM calls.
                    #
                    # Smaller is measured in TOKENS, not messages: the cheapest
                    # stage folds bulky tool output and keeps every message.
                    before = estimate_messages(ctx.history)
                    shrunk = (
                        _shrink_history(
                            ctx.history,
                            stated,
                            # The ceiling in the rejection covers the WHOLE
                            # request; history only gets what the prompt and
                            # tool schemas leave behind.
                            overhead_tokens=ctx.context_overhead_tokens,
                        )
                        if len(ctx.history) > 1
                        else ctx.history
                    )
                    if estimate_messages(shrunk) < before:
                        dropped = len(ctx.history) - len(shrunk)
                        ctx.history = shrunk
                        _LOGGER.warning(
                            "litellm_runner: request too long — retrying with %d history "
                            "items (investigation=%s)",
                            len(ctx.history),
                            ctx.investigation_id,
                        )
                        yield ContextTrimmed(dropped=dropped, kept=len(ctx.history))
                        continue
                    # Nothing got smaller — usually one message that alone
                    # exceeds the window. Say it in words the user can act on;
                    # a raw provider string tells them nothing about what to do.
                    yield RunError(
                        message=(
                            "這一輪送不出去:內容超過模型一次能讀的範圍,而且已經沒有東西可以再省。"
                            "請把訊息拆小,或開一個新對話重試。"
                        )
                    )
                    yield RunDone()
                    return
                if not _should_retry(
                    progress_made=progress_made,
                    attempt=attempt,
                    max_retries=self._max_retries,
                    error_text=f"{type(exc).__name__}: {exc}",
                ):
                    _LOGGER.warning(
                        "litellm_runner: turn stopped after %d attempt(s), not retrying "
                        "(%r) (investigation=%s)",
                        attempt,
                        exc,
                        ctx.investigation_id,
                    )
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
                _LOGGER.warning(
                    "litellm_runner: turn attempt %d failed (%r) — retrying with hint "
                    "(investigation=%s)",
                    attempt,
                    exc,
                    ctx.investigation_id,
                )
                feedback = diagnose_error(exc)
                yield classify_retry_event(exc, feedback)

    def _note_prompt_usage(
        self, ctx: AgentToolContext, *, sent_estimate: int, reported: int | None
    ) -> None:
        """Compare what we believe we sent against what the provider says it
        read, and learn the endpoint's real window from the gap (#624).

        This is the ONLY signal a silently-truncating provider gives. Ollama
        drops the front of an over-long request with no error and no warning —
        the model then answers, fluently, from a prompt it never fully saw
        (measured: 3,983 of 8,755 tokens read, and it invented a project code
        rather than saying it did not know). The usage figure was already being
        collected for the UI's ↑ counter and thrown away for this purpose.

        Inferences need confirming before they govern anything; see
        ``LimitLearner``."""
        if ctx.agent_config is None:
            return
        cut = detect_truncation(sent_estimate=sent_estimate, reported_prompt_tokens=reported)
        if cut is None:
            return
        _LOGGER.warning(
            "litellm_runner: provider read %d of ~%d estimated tokens — it truncated "
            "silently; treating %d as its window (model=%s)",
            reported or 0,
            sent_estimate,
            cut,
            ctx.agent_config.model,
        )
        self._limits.observe(ctx.agent_config.model, self._base_url, limit=cut)

    def learned_limit(self, model: str, base_url: str | None) -> int | None:
        """What we know about this endpoint's real ceiling (#624).

        Read by the turn-context builder so a turn's budget comes from what the
        endpoint actually told us rather than from a guess. Two sources, in
        order: what a past rejection or truncation taught us, then — once per
        endpoint — asking it outright.

        The probe is asked lazily and cached (including its silence): most
        endpoints are not vLLM and answer nothing, and re-asking every turn
        would put an HTTP round-trip in front of every message for a value that
        does not change.

        It is also asked OFF the request path. The probe is a synchronous POST
        with a multi-second timeout, and this method is reached from inside
        `async def build_chat_turn` — so an endpoint that hangs would stall the
        whole pod's event loop, not just the turn that asked. Nothing has to be
        invented to avoid that: an endpoint we have not heard from yet is
        `unknown`, and `unknown` already means "send it all" (§3), so the first
        turn behaves exactly as designed while the answer arrives for the next
        one."""
        known = self._limits.get(model, base_url)
        if known is not None:
            return known
        probed = deferred_lookup(
            self._probed,
            (model or "", base_url or ""),
            lambda: self._probe(base_url or self._base_url, model),
        )
        if probed is not None:
            # The learner is the ONE place that answers "what do we know about
            # this endpoint" — a rejection, a silent truncation and a probe all
            # land there, so nothing downstream has to know which of them spoke.
            self._limits.learn_exact(model, base_url, limit=probed)
        return self._limits.get(model, base_url)

    def endpoint_limits(self, model: str, base_url: str | None) -> Any:
        """What a proxy in front of `model` says it was configured with (#624
        follow-up), or None.

        Lives here rather than in the turn-context builder for one reason, and
        it is the reason the first version of this did nothing at all: an
        `AgentConfig` carries `llm_base_url == ""` whenever the preset does not
        override it, and that empty string MEANS "use the deploy's endpoint" —
        which only the runner knows. A caller that reads the config's field
        alone sees no endpoint on exactly the deployment this rung exists for:
        one endpoint configured globally, presets naming only a model.

        Asked once per (model, endpoint) and cached — most endpoints are not a
        litellm proxy, and re-asking would put an HTTP round trip in front of
        every message for a value that does not change. Filled off the loop by
        `deferred_lookup`, like the `/tokenize` probe: an endpoint that hangs
        must not stall the pod.

        Silence expires, though — see `DECLARED_RETRY_S`."""
        key = (model or "", base_url or "")
        self._expire_declared(key)
        return deferred_lookup(
            self._endpoint_declared,
            key,
            lambda: self._declared_probe(base_url or self._base_url, model),
        )

    def _expire_declared(self, key: tuple[str, str]) -> None:
        """Forget a cached silence once it is old enough to re-ask.

        Only silence: an answer is kept for the life of the pod. And the stamp
        is taken at the moment the question is ASKED, not answered, so a lookup
        still in flight (which reads as `None` until the thread lands) cannot be
        re-asked out from under itself."""
        now = self._clock()
        asked_at = self._declared_at.get(key)
        if asked_at is None:
            self._declared_at[key] = now
            return
        if self._endpoint_declared.get(key) is None and now - asked_at >= DECLARED_RETRY_S:
            self._endpoint_declared.pop(key, None)
            self._declared_at[key] = now

    def _agent_kwargs(
        self,
        ctx: AgentToolContext,
        feedback: str | None,
        resolve_credential: Callable[[str | None], LlmCredential],
    ) -> dict[str, Any]:
        """Everything both turn shapes hand `_agent_for` — the streamed turn adds
        only its per-turn event sinks (`on_failover_switch`, `on_rate_limit_hold`).

        One list, because two copies is how the shapes drift: every axis added
        since (`app_slug`, `has_subagents`, `skills_reachable`) had to be written
        twice, and a copy that forgets one never fails a run — both call sites are
        `pragma: no cover`, exercised only against a live model. The turn that
        then behaves differently is the one nobody watches."""
        return {
            "extra_instructions": _turn_instructions(ctx, feedback),
            "base_url": self._base_url,
            "api_key": self._api_key,
            "reasoning_effort": ctx.reasoning_effort,
            "context_window": ctx.context_window,
            "app_slug": ctx.app_slug,
            "template_profile": ctx.template_profile,
            "has_subagents": bool(ctx.subagent_defs),
            "skills_reachable": ctx.skills_reachable,
            "subagent_models": ctx.subagent_models,
            "fallback_chains": self._fallback_chains,
            "cooldown_registry": self._cooldown_registry,
            "resolve_credential": resolve_credential,
            "stream_deadlines": self._stream_deadlines,
        }

    async def _run_once(  # pragma: no cover — see the note below
        # The exclusion predates a test that DOES drive this loop
        # (`test_tool_call_arguments_are_counted_but_never_shown` stubs
        # `Runner.run_streamed`). Keeping it means the 100% gate stays blind
        # here — which is how tool-call JSON came to be printed into replies
        # for a whole commit. Narrow or remove it when the fake stream covers
        # enough of the loop to hold the line on its own.
        self,
        prompt: str,
        ctx: AgentToolContext,
        feedback: str | None,
    ) -> AsyncIterator[AgentEvent]:
        assert ctx.agent_config is not None  # run() guards None before _run_once
        # Non-streaming path: the escape hatch (WORKSPACE_AGENT_STREAM=0).
        if not _stream_enabled():
            async for ev in self._run_once_nonstream(prompt, ctx, feedback):
                yield ev
            return
        # The SDK delivers model output via stream_events(), but a running
        # tool (a long exec) produces stdout *between* those events with no
        # SDK channel to surface it. So we fan both into one queue: a producer
        # task drives stream_events(), and the exec tool pushes ToolLog chunks
        # via ctx.on_exec_output — the drain loop yields whichever arrives
        # first, so tool output shows up live while the command is still
        # running. The queue is created BEFORE the agent so a FallbackModel
        # switch (#249/#131) can push a live FailoverSwitch notice into it too.
        queue: asyncio.Queue[AgentEvent | object] = asyncio.Queue()
        done = object()
        ctx.on_exec_output = lambda b: queue.put_nowait(ToolLog(text=b.decode("utf-8", "replace")))
        # #492 P11: a cold wake's snapshot restore streams (done, total) here so the
        # FE shows "還原中 N/M" instead of a blank running card while it completes.
        ctx.on_restore_progress = lambda done, total: queue.put_nowait(
            RestoreProgress(done=done, total=total)
        )
        # #613: the update_todos tool streams its freshly-written checklist here so
        # the FE's pinned panel updates live mid-turn.
        ctx.on_todos_updated = _todos_emitter(queue)
        resolve_credential = await self._credential_resolver(ctx)
        agent = _agent_for(
            ctx.agent_config,
            ctx.packages,
            ctx.unavailable_tools,
            on_failover_switch=_failover_emitter(queue),
            on_rate_limit_hold=_rate_limit_hold_emitter(queue),
            **self._agent_kwargs(ctx, feedback, resolve_credential),
        )
        t0 = time.monotonic()
        prompt_tok = _live_prompt_tokens(ctx, prompt)

        # Tag the SDK trace with the run flavour (workflow_name) + the
        # investigation/collection id (group_id) so the live monitor can
        # attribute every span to the run that produced it AND tell wiki
        # maintenance / reader / merge apart from chat and RCA turns.
        run_config = RunConfig(
            workflow_name=_trace_workflow_name(ctx), group_id=ctx.investigation_id
        )
        streamed = Runner.run_streamed(
            agent,
            input=_build_input(ctx.history, prompt, ctx.turn_image_urls),  # ty: ignore[invalid-argument-type]
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
                gen = _GenerationClock()
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
                        event_type = getattr(data, "type", "") or ""
                        channel = _delta_channel(event_type)
                        if isinstance(delta, str) and delta and _is_generated_output(event_type):
                            # #748: the count and the clock move TOGETHER. The
                            # tool-call argument JSON is kept out of the answer
                            # text but the provider bills it, so excluding it
                            # from one side and not the other makes tok/s a ratio
                            # of two different populations — first too high, then
                            # (when only the clock was fixed) too low.
                            completion_chars += len(delta)
                            gen.token()
                            if channel == "reasoning":
                                queue.put_nowait(MessageDelta(text=delta, reasoning=True))
                            elif channel == "content":
                                # Explicitly `content`, never a bare `else`. The
                                # gate above answers "does the provider bill
                                # this?", which is now TRUE for tool-call
                                # argument JSON — and an `else` therefore routed
                                # that JSON into the visible reply. Counting and
                                # routing ask different questions; the `if` that
                                # does both has to keep asking both.
                                # (still split any inline <think> tags)
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
                                        generation_ms=gen.elapsed_ms(),
                                    )
                                )
                        continue
                    mapped = _map_event(event)
                    if mapped is not None:
                        if isinstance(mapped, ToolStart):
                            tool_calls_seen += 1
                            # The model went quiet to call this tool; the gap
                            # that follows is the tool's time, not generation.
                            gen.pause()
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

                usage = _exact_usage(streamed)
                # #624: the provider just told us how much it ACTUALLY read. If
                # that is far below what we sent, it truncated silently — the
                # one failure mode with no error and no other symptom.
                self._note_prompt_usage(
                    ctx,
                    sent_estimate=_sent_estimate(ctx, prompt),
                    reported=usage[0] if usage else None,
                )
                prompt_final, completion_final = _final_tokens(usage, prompt_tok, completion_chars)
                measured_prompt, measured_completion = _measured_tokens(usage)
                queue.put_nowait(
                    AgentMetrics(
                        phase="final",
                        prompt_tokens=prompt_final,
                        completion_tokens=completion_final,
                        elapsed_ms=round((time.monotonic() - t0) * 1000),
                        measured_prompt_tokens=measured_prompt,
                        measured_completion_tokens=measured_completion,
                        generation_ms=gen.elapsed_ms(),
                        model=_effective_model(
                            getattr(agent, "model", None),
                            ctx.agent_config.model if ctx.agent_config else "",
                        ),
                        exact=measured_prompt is not None,
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
        resolve_credential = await self._credential_resolver(ctx)
        agent = _agent_for(
            ctx.agent_config,
            ctx.packages,
            ctx.unavailable_tools,
            **self._agent_kwargs(ctx, feedback, resolve_credential),
        )
        t0 = time.monotonic()
        prompt_tok = _live_prompt_tokens(ctx, prompt)
        # No stream → exec stdout can't interleave live; it still lands in the
        # tool's result. Swallow mid-turn pushes so a long exec doesn't error.
        ctx.on_exec_output = lambda b: None
        yield AgentMetrics(phase="up", prompt_tokens=prompt_tok, elapsed_ms=0)
        run_config = RunConfig(
            workflow_name=_trace_workflow_name(ctx), group_id=ctx.investigation_id
        )
        result = await Runner.run(
            agent,
            input=_build_input(ctx.history, prompt, ctx.turn_image_urls),  # ty: ignore[invalid-argument-type]
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
        usage = _exact_usage(result)
        self._note_prompt_usage(
            ctx, sent_estimate=_sent_estimate(ctx, prompt), reported=usage[0] if usage else None
        )
        prompt_final, completion_final = _final_tokens(usage, prompt_tok, len("".join(content_buf)))
        measured_prompt, measured_completion = _measured_tokens(usage)
        yield AgentMetrics(
            phase="final",
            prompt_tokens=prompt_final,
            completion_tokens=completion_final,
            elapsed_ms=round((time.monotonic() - t0) * 1000),
            measured_prompt_tokens=measured_prompt,
            measured_completion_tokens=measured_completion,
            model=_effective_model(
                getattr(agent, "model", None),
                ctx.agent_config.model if ctx.agent_config else "",
            ),
            exact=measured_prompt is not None,
        )
