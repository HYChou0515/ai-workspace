"""tool_prompt: format the agent's runtime tool inventory into a
system-prompt section so small LLMs can't confuse provisioned function
tools with shell binaries."""

from __future__ import annotations

import json

from agents import FunctionTool

from workspace_app.agent.tool_prompt import (
    format_disabled_tools_for_prompt,
    format_tools_for_prompt,
)
from workspace_app.tooling.catalog import ToolMeta


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


def test_includes_section_header_and_invariant_warning():
    """Header + the "don't exec a tool name" rule appear once at the
    top so the LLM can't miss them."""
    tools = [_ft("wafer-history", "fetch history", {"type": "object", "properties": {}})]
    out = format_tools_for_prompt(tools)
    assert out.startswith("## Tools available")
    assert "tool_calls" in out
    assert "exit 127" in out  # explicit failure-mode reminder


def test_each_tool_gets_name_description_and_schema_block():
    """For every tool, the prompt carries name + description + the
    pydantic-derived JSON args schema (verbatim from
    `params_json_schema`)."""
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
    # Name + description
    assert "### `wafer-history`" in out
    assert "Materialise wafer history into a CSV." in out
    # Schema appears as a JSON code block
    assert "```json" in out
    # Round-trip: the dumped schema is recoverable from the prompt.
    fence_start = out.index("```json") + len("```json")
    fence_end = out.index("```", fence_start)
    recovered = json.loads(out[fence_start:fence_end])
    assert recovered == schema


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
    assert "### `foo`" in out
    assert "_(no description)_" in out


def _tm(name: str, description: str) -> ToolMeta:
    return ToolMeta(name=name, label=name, description=description)


# ─── #480 disabled-tool disclosure (known but not callable) ─────────
def test_disabled_meta_renders_name_and_one_line_description():
    """#480: a disabled tool shows up as `name — description` so the agent
    knows it exists and what it's for, without it being a callable tool."""
    out = format_disabled_tools_for_prompt([_tm("make_deck", "Build a .pptx deck.")])
    assert "`make_deck`" in out
    assert "Build a .pptx deck." in out


def test_empty_disabled_metas_returns_empty_string():
    """No disabled tools → empty string so the caller omits the whole section."""
    assert format_disabled_tools_for_prompt([]) == ""


def test_disabled_section_header_and_enable_reminder():
    """#480: the section leads with its header and the positive instruction to
    ask the user to enable a tool in the picker (not a 'do not use' ban)."""
    out = format_disabled_tools_for_prompt([_tm("make_deck", "Build a deck.")])
    assert out.startswith("## Tools available on request")
    assert "tool picker" in out
    assert "turn on" in out or "enable" in out


def test_disabled_tools_carry_no_json_schema():
    """#480: disabled tools are name+description only — no JSON args schema
    (they can't be called, so the schema is dead weight for small models)."""
    out = format_disabled_tools_for_prompt([_tm("make_deck", "Build a deck.")])
    assert "```json" not in out
    assert "Args (JSON schema)" not in out


def test_disabled_metas_listed_in_given_order():
    """Order is the caller's (ceiling order) — deterministic rendering."""
    out = format_disabled_tools_for_prompt(
        [_tm("make_deck", "a"), _tm("rca-tools", "b"), _tm("sci-plot", "c")]
    )
    assert out.index("`make_deck`") < out.index("`rca-tools`") < out.index("`sci-plot`")


def test_disabled_meta_without_description_renders_bare_name():
    """A tool with no available description still lists — just the name, no
    trailing separator (deploy without that package built, etc.)."""
    out = format_disabled_tools_for_prompt([_tm("mystery-pkg", "")])
    assert "- `mystery-pkg`" in out
    assert "`mystery-pkg` —" not in out  # no dangling em-dash


def test_complex_schema_with_enum_and_nested_objects_round_trips():
    """A real schema (enum constraint, nested objects, optional fields)
    survives the markdown round-trip — guards against an over-clever
    formatter that flattens / strips schema features the LLM needs."""
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": ["alloy-batches", "sensor-telemetry"],
            },
            "rows": {"type": "integer", "default": 25000},
            "nested": {
                "type": "object",
                "properties": {"deep": {"type": "boolean"}},
            },
        },
        "required": ["name"],
    }
    tools = [_ft("data-fetch", "fetch dataset", schema)]
    out = format_tools_for_prompt(tools)
    fence_start = out.index("```json") + len("```json")
    fence_end = out.index("```", fence_start)
    assert json.loads(out[fence_start:fence_end]) == schema
