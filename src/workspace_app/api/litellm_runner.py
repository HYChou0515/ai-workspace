"""LitellmAgentRunner — drives a turn using OpenAI Agents SDK + LiteLLM.

Per grill-me Q7/feedback-llm-choice: default model is
`ollama/qwen2.5-coder:7b-instruct` served by a local Ollama. Swap to
hosted providers via the AgentConfig.model field — LiteLLM handles the
provider dispatch (openai/, anthropic/, ollama/, together/, ...).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agents import Agent, ItemHelpers, Runner
from agents.extensions.models.litellm_model import LitellmModel

from ..agent.context import AgentToolContext
from ..agent.tools import build_tools
from ..resources import AgentConfig
from .events import AgentEvent, MessageDelta, RunDone, ToolEnd, ToolStart


def _agent_for(config: AgentConfig) -> Agent[AgentToolContext]:
    return Agent[AgentToolContext](
        name=config.name,
        instructions=config.system_prompt or None,
        model=LitellmModel(model=config.model),
        tools=list(build_tools(config.allowed_tools or None)),
    )


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
    if name == "message_output_created":
        return MessageDelta(text=ItemHelpers.text_message_output(item))
    return None


class LitellmAgentRunner:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or AgentConfig(name="workspace-agent")

    async def run(  # pragma: no cover — exercised only by the live Ollama test
        self, prompt: str, ctx: AgentToolContext
    ) -> AsyncIterator[AgentEvent]:
        agent = _agent_for(self._config)
        streamed = Runner.run_streamed(agent, input=prompt, context=ctx)
        async for event in streamed.stream_events():
            mapped = _map_event(event)
            if mapped is not None:
                yield mapped
        yield RunDone()
