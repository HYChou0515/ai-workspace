"""The ceiling is ENFORCED, not merely attached.

Attaching a guardrail to every tool only helps if the runner actually executes
it, and the execution point is SDK code (`_execute_tool_output_guardrails`),
not ours. So this drives a real `Runner.run` over a real capped tool with a
network-free model and reads what the tool result item ended up holding — the
thing that becomes the model's next-turn context.
"""

from __future__ import annotations

from typing import Any

from agents import Agent, ModelSettings, Runner
from agents.items import ModelResponse, ToolCallOutputItem
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText

from workspace_app.agent import AgentToolContext, build_tools, write_file_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _text_message() -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="m1",
        content=[ResponseOutputText(annotations=[], text="done", type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


class _CallsOnceThenAnswers(Model):
    """Calls `list_files` on the first turn, then replies with plain text."""

    def __init__(self) -> None:
        self.turn = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        self.turn += 1
        if self.turn == 1:
            call = ResponseFunctionToolCall(
                arguments="{}", call_id="c1", name="list_files", type="function_call"
            )
            return ModelResponse(output=[call], usage=Usage(), response_id=None)
        return ModelResponse(output=[_text_message()], usage=Usage(), response_id=None)

    async def stream_response(self, *args: Any, **kwargs: Any):  # pragma: no cover — unused
        raise AssertionError


async def test_a_real_run_never_lets_an_oversized_tool_result_into_the_context():
    files = WorkspaceFiles(MemoryFileStore())
    ctx = AgentToolContext(
        investigation_id="inv-1",
        files=files,
        # A ceiling far under what the listing would render to, and a per-tool
        # budget far OVER it — so anything that gets through is the ceiling
        # failing, not the listing tool capping itself.
        tool_output_max_chars=500,
        exec_output_max_chars=10_000_000,
    )
    from agents import RunContextWrapper

    wrapper: RunContextWrapper[AgentToolContext] = RunContextWrapper(ctx)
    for i in range(400):
        await write_file_impl(wrapper, f"/f{i:04d}.txt", "x")

    agent = Agent[AgentToolContext](
        name="probe",
        tools=build_tools(["list_files"]),
        model=_CallsOnceThenAnswers(),
        model_settings=ModelSettings(),
    )
    result = await Runner.run(agent, "list the workspace", context=ctx)

    [tool_output] = [i for i in result.new_items if isinstance(i, ToolCallOutputItem)]
    assert len(str(tool_output.output)) <= 700  # the 500 ceiling plus its marker
    assert "omitted" in str(tool_output.output)
