"""Every tool the agent can call has a ceiling on what it puts in the model's
context — enforced by the toolset, not by each tool's author remembering."""

from agents import Agent, FunctionTool, RunContextWrapper, ToolOutputImage
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
    data = ToolOutputGuardrailData(context=tctx, agent=Agent(name="probe"), output=output)
    result = await guardrail.run(data)
    if result.behavior["type"] == "reject_content":
        return result.behavior["message"]
    return output


def test_every_builtin_tool_carries_the_output_cap():
    """EVERY registered built-in, not just the default workspace dozen — the
    claim is that a tool cannot be handed out without a ceiling."""
    from workspace_app.agent.tools import _IMPLS

    tools = build_tools(sorted(_IMPLS))
    assert len(tools) == len(_IMPLS)
    for tool in tools:
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
    """A tool that answers with a list (`str()`-ed into the history by the SDK)
    is capped on that rendering, not on the number of entries."""
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


def test_the_tail_survives_when_the_text_is_a_single_long_line():
    """head+tail is the whole point (#44): the punchline — a count, an error, a
    summary — sits at the end. A one-line body with a trailing newline used to
    lose its tail entirely and silently degrade to head-only."""
    from workspace_app.agent.output_cap import truncate_middle

    out = truncate_middle("START" + "X" * 1000 + "END\n", 100)

    assert out.startswith("START")
    assert "END" in out


async def test_a_text_tool_output_object_is_capped_like_any_other_text():
    """ToolOutputText IS text in the context window. Exempting it would leave
    the backstop depending on tool authors again — the thing it exists to
    replace."""
    from agents import ToolOutputText

    [tool] = build_tools(["read_file"])
    out = await _run_cap(tool, ToolOutputText(text="Z" * 5_000))

    assert isinstance(out, str)
    assert len(out) < 400
