"""Every tool the agent can call has a ceiling on what it puts in the model's
context — enforced by the toolset, not by each tool's author remembering."""

from agents import FunctionTool, RunContextWrapper, ToolOutputImage
from agents.tool_context import ToolContext
from agents.tool_guardrails import ToolOutputGuardrailData

from workspace_app.agent import AgentToolContext, build_tools
from workspace_app.agent.output_cap import TOOL_OUTPUT_CAP_NAME, cap_tool_outputs
from workspace_app.tooling.registry import CommandInfo, PackageInfo, build_function_tools


def _guardrail_names(tool: FunctionTool) -> list[str]:
    return [g.get_name() for g in tool.tool_output_guardrails or []]


async def _run_cap(tool: FunctionTool, output: object, *, cap: int = 100) -> object:
    """Drive the tool's output guardrail the way the SDK does and return what
    the model would end up seeing (the raw output when nothing trips)."""
    actx = AgentToolContext(investigation_id="inv-1", tool_output_max_chars=cap)
    tctx = ToolContext.from_agent_context(
        RunContextWrapper(actx), tool_name=tool.name, tool_call_id="call-1", tool_arguments="{}"
    )
    guardrail = (tool.tool_output_guardrails or [])[0]
    result = await guardrail.run(ToolOutputGuardrailData(context=tctx, agent=None, output=output))
    if result.behavior["type"] == "reject_content":
        return result.behavior["message"]
    return output


def test_every_builtin_tool_carries_the_output_cap():
    for tool in build_tools():
        assert TOOL_OUTPUT_CAP_NAME in _guardrail_names(tool), tool.name


def test_package_command_tools_carry_the_output_cap():
    pkg = PackageInfo(
        name="demo",
        commands=(CommandInfo(name="chart", description="d", params_json_schema={}),),
        install_dir="../.tools/demo",
    )
    for tool in build_function_tools([pkg], allowed=None):
        assert TOOL_OUTPUT_CAP_NAME in _guardrail_names(tool), tool.name


def test_cap_attachment_keeps_any_guardrail_a_tool_already_declared():
    [tool] = cap_tool_outputs(build_tools(["exists"]))
    [again] = cap_tool_outputs([tool])
    assert _guardrail_names(again).count(TOOL_OUTPUT_CAP_NAME) == 1


async def test_output_within_budget_is_passed_through_untouched():
    [tool] = build_tools(["read_file"])
    assert await _run_cap(tool, "short body") == "short body"


async def test_oversized_text_is_truncated_head_and_tail_with_a_notice():
    [tool] = build_tools(["read_file"])
    out = await _run_cap(tool, "\n".join(f"line{i}" for i in range(500)))
    assert isinstance(out, str)
    assert len(out) < 400  # the 100-char budget plus the notice, not 500 lines
    assert out.startswith("line0")  # head kept
    assert out.rstrip().endswith("line499")  # tail kept
    assert "omitted" in out


async def test_a_list_returning_tool_is_capped_on_what_the_model_actually_sees():
    """`list_files` returns list[str]; the SDK stringifies it into the history,
    so the cap has to measure that rendering, not len(list)."""
    [tool] = build_tools(["list_files"])
    out = await _run_cap(tool, [f"/dir/file{i}.txt" for i in range(200)])
    assert isinstance(out, str)
    assert len(out) < 400


async def test_a_structured_image_output_is_never_truncated():
    """read_image hands back a ToolOutputImage — truncating it would corrupt the
    payload, and it is not text sitting in the context window."""
    [tool] = build_tools(["read_image"])
    image = ToolOutputImage(image_url="data:image/png;base64," + "A" * 5000)
    assert await _run_cap(tool, image) is image
    assert await _run_cap(tool, [image]) == [image]
