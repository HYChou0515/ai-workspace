"""Creating a sub-agent and using it is ONE reply, not two.

The turn's tool list and delegation index are both fixed before the agent loop
starts, so "save it, then call it" only works if the tool resolves names against
the workspace rather than that frozen index. That is SDK-driven territory — the
loop, not our code — so this drives a real `Runner.run` with a network-free
model: turn 1 saves a sub-agent, turn 2 delegates to it, and the run must come
back with the sub-agent's report.

Without the live re-read this fails at turn 2 with "no sub-agent named …", which
is exactly what the user would have hit: a second message needed for no reason
they could see.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agents import Agent, ModelSettings, Runner
from agents.items import ModelResponse, ToolCallOutputItem
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText

from workspace_app.agent import AgentToolContext, build_tools
from workspace_app.apps.subagents import SubagentDef
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore

_SAVE_ARGS = {
    "name": "Line Finder",
    "description": "Finds which line first mentions a thing.",
    "tools": ["read_file"],
    "body": "Answer with the line number and the verbatim line.",
}


def _text_message() -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="m1",
        content=[ResponseOutputText(annotations=[], text="done", type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


def _call(name: str, args: dict[str, Any], call_id: str) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps(args), call_id=call_id, name=name, type="function_call"
    )


def _one(item: Any) -> ModelResponse:
    return ModelResponse(output=[item], usage=Usage(), response_id=None)


class _SavesThenDelegates(Model):
    """Turn 1 saves a sub-agent; turn 2 delegates to the slug it was told about."""

    def __init__(self) -> None:
        self.turn = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        self.turn += 1
        if self.turn == 1:
            return _one(_call("save_subagent", _SAVE_ARGS, "c1"))
        if self.turn == 2:
            task = {"agent_type": "line-finder", "prompt": "which line?"}
            return _one(_call("run_agent", task, "c2"))
        return _one(_text_message())

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise AssertionError("this probe answers through get_response only")
        yield  # pragma: no cover — only here to make this an async generator


async def test_a_sub_agent_created_mid_run_is_delegated_to_in_the_same_run():
    delegated: list[tuple[str, str]] = []

    async def run_agent(parent_ctx, defn: SubagentDef, prompt: str, emit=None) -> str:
        delegated.append((defn.name, prompt))
        return f"[{defn.name}] line 84"

    ctx = AgentToolContext(
        investigation_id="inv-1",
        files=WorkspaceFiles(MemoryFileStore()),
        run_agent=run_agent,
        # As at turn start: nothing defined yet, so nothing in the frozen index.
        subagent_defs=(),
    )
    agent = Agent[AgentToolContext](
        name="probe",
        # `run_agent` is offered because `save_subagent` is held — there is no
        # dead end to protect the model from.
        tools=list(build_tools(["read_file", "save_subagent", "run_agent"])),
        model=_SavesThenDelegates(),
        model_settings=ModelSettings(),
    )

    result = await Runner.run(agent, "make a line finder and use it", context=ctx)

    assert delegated == [("line-finder", "which line?")]
    outputs = [str(i.output) for i in result.new_items if isinstance(i, ToolCallOutputItem)]
    assert any("saved sub-agent 'line-finder'" in o for o in outputs)
    assert any("[line-finder] line 84" in o for o in outputs)
