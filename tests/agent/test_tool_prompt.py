"""tool_prompt: format the agent's runtime tool inventory into a
system-prompt section so small LLMs can't confuse provisioned function
tools with shell binaries."""

from __future__ import annotations

from agents import FunctionTool

from workspace_app.agent.tool_prompt import (
    format_disabled_tools_for_prompt,
    format_tools_for_prompt,
    format_unavailable_tools_for_prompt,
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


def test_the_note_explains_why_a_schema_may_be_empty():
    """A catalog entry carries an empty argument schema
    (`docs/plan-tool-on-demand-loading.md`). Unexplained, that reads as "takes
    no arguments" — and the model would call it, get the schema back, and have
    no idea it was supposed to call again.

    It also has to say that several can be loaded in ONE `load_tool` call:
    each first use costs an extra step, and `runner.max_turns` bounds the turn,
    so a turn using five commands one at a time hits the ceiling with the work
    already done (round 1 A4)."""
    out = format_tools_for_prompt([_ft("wafer-history", "fetch", {}), _ft("load_tool", "load", {})])

    assert "empty" in out
    assert "again" in out
    assert "load_tool" in out and "ONCE" in out
    # Both ways a bundle name reaches the model: the `[bundle]` suffix a
    # first-party entry carries, and the `<bundle>__<command>` name a
    # third-party one already has — third-party entries deliberately carry no
    # suffix, and they are the user-installed population, so naming only the
    # suffix pointed at a marker that is not there (verify round F8).
    assert "[bundle]" in out and "__" in out


def test_a_turn_with_no_catalog_is_not_told_about_one():
    """The control: without `load_tool` there is no catalog entry and no empty
    schema, so the paragraph would be an instruction about something that
    cannot happen (round 1 A15)."""
    out = format_tools_for_prompt([_ft("exec", "run", {"type": "object"})])

    assert "exit 127" in out  # the rule that always applies
    assert "empty" not in out
    assert "load_tool" not in out


def test_the_note_does_not_grow_with_the_tool_list():
    """The section no longer repeats the inventory — that second copy is what
    put six figures of characters in one system prompt. Three tools and three
    hundred cost the same here; the tools themselves are in the API payload."""
    schema = {
        "type": "object",
        "properties": {
            "wafer_ids": {"type": "array", "items": {"type": "string"}},
            "n_wafers": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["wafer_ids"],
    }
    three = [
        _ft("exec", "run a shell command", {"type": "object", "properties": {"cmd": {}}}),
        _ft("read_file", "read a file", {"type": "object", "properties": {"path": {}}}),
        _ft("wafer-history", "Materialise wafer history into a CSV.", schema),
    ]
    many = three + [_ft(f"cmd-{i}", "d" * 200, schema) for i in range(300)]

    out = format_tools_for_prompt(three)

    assert out == format_tools_for_prompt(many)
    # Neither the names nor the schemas are in it any more.
    assert "wafer-history" not in out
    assert "```json" not in out
    assert "wafer_ids" not in out


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


def test_unavailable_tools_say_what_went_wrong_not_just_that_they_are_gone():
    """#674: a third-party tool that could not be fetched is NOT the same as a
    tool the user turned off. Nobody can enable it from the picker, so the
    section says what happened and points at who can fix it."""
    section = format_unavailable_tools_for_prompt({"wafer-history": "404 — the artifact expired"})

    assert "wafer-history" in section
    assert "404 — the artifact expired" in section
    # Must not send the agent to the tool picker: toggling changes nothing here.
    assert "picker" not in section.lower()


def test_no_unavailable_tools_renders_nothing_at_all():
    # The overwhelmingly common case. A heading saying "nothing is broken"
    # would be spent context on every single turn.
    assert format_unavailable_tools_for_prompt({}) == ""
