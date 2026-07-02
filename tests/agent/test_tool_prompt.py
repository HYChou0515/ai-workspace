"""tool_prompt: format the agent's runtime tool inventory into a
system-prompt section so small LLMs can't confuse provisioned function
tools with shell binaries.

#107 request hygiene: the section is a SLIM name+summary list. It must
NOT carry the JSON args schemas — the schemas already reach the model
through the native `tools` param (and chat templates like Qwen/GLM's
render them into `<tools>` themselves); duplicating them as fenced JSON
in the content channel teaches the model a *textual* representation of
tool calls, which is exactly the shape it degenerates into on long args
(emitting the call as message text instead of a tool_call).
"""

from __future__ import annotations

from agents import FunctionTool

from workspace_app.agent.tool_prompt import format_tools_for_prompt


def _ft(name: str, description: str, schema: dict) -> FunctionTool:
    """Build a bare FunctionTool with the (name, description, schema)
    bits we want to surface."""

    async def _noop(_ctx, _args_json: str) -> str:  # pragma: no cover
        return ""

    return FunctionTool(
        name=name,
        description=description,
        params_json_schema=schema,
        on_invoke_tool=_noop,
        strict_json_schema=False,
    )


def test_empty_tools_returns_empty_string():
    """No tools → empty string so the caller can skip concatenation."""
    assert format_tools_for_prompt([]) == ""


def test_includes_section_header_and_invariant_warnings():
    """Header + the "don't exec a tool name" rule + the #107 "never print
    a tool call as text" rule appear once at the top so the LLM can't
    miss them."""
    tools = [_ft("wafer-history", "fetch history", {"type": "object", "properties": {}})]
    out = format_tools_for_prompt(tools)
    assert out.startswith("## Tools available")
    assert "exit 127" in out  # explicit failure-mode reminder
    # #107: the specific degeneration (printing the call into message text)
    # must be named — a printed call is never executed.
    assert "NEVER write a tool call" in out
    assert "not executed" in out


def test_each_tool_gets_name_and_first_description_line():
    """Each tool renders as one bullet: name + the first line of its
    description. Full descriptions + schemas ride the native `tools`
    param; the prompt list only binds names to "function tool"."""
    tools = [
        _ft(
            "wafer-history",
            "Materialise wafer history into a CSV.\n\nLong usage notes\nspanning lines.",
            {"type": "object", "properties": {}},
        )
    ]
    out = format_tools_for_prompt(tools)
    assert "- `wafer-history` — Materialise wafer history into a CSV." in out
    # Only the first line of the description is surfaced.
    assert "Long usage notes" not in out


def test_no_json_schema_leaks_into_the_prompt():
    """#107: the args schema must NOT appear in the content channel — no
    fenced JSON, no property names from the schema."""
    schema = {
        "type": "object",
        "properties": {
            "wafer_ids": {"type": "array", "items": {"type": "string"}},
            "n_wafers": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["wafer_ids"],
    }
    tools = [_ft("wafer-history", "Materialise wafer history into a CSV.", schema)]
    out = format_tools_for_prompt(tools)
    assert "```" not in out
    assert "wafer_ids" not in out
    assert "JSON schema" not in out


def test_multiple_tools_listed_in_order():
    """Order is preserved (caller picked it: built-ins first, then
    provisioned). Helps the LLM reason about which to reach for first."""
    tools = [
        _ft("exec", "run a shell command", {"type": "object", "properties": {"cmd": {}}}),
        _ft("read_file", "read a file", {"type": "object", "properties": {"path": {}}}),
        _ft("wafer-history", "fetch wafer history", {"type": "object", "properties": {}}),
    ]
    out = format_tools_for_prompt(tools)
    assert out.index("`exec`") < out.index("`read_file`") < out.index("`wafer-history`")


def test_tool_without_description_still_renders():
    """A tool with empty description doesn't crash — placeholder text
    keeps the structure consistent."""
    tools = [_ft("foo", "", {"type": "object", "properties": {}})]
    out = format_tools_for_prompt(tools)
    assert "- `foo` — _(no description)_" in out
