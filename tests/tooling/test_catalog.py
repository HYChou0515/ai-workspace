"""#322: the tool catalog — one source of truth for tool display metadata
(name + human label + one-line description), shared by the web tool picker and
the chat tool cards so neither hand-maintains a name→label map that drifts."""

from workspace_app.tooling.catalog import (
    ToolMeta,
    flat_catalog,
    humanize_tool_label,
    picker_units,
)
from workspace_app.tooling.registry import CommandInfo, PackageInfo


def _pkg(name: str, *cmds: CommandInfo) -> PackageInfo:
    return PackageInfo(name=name, commands=tuple(cmds), install_dir=f"../.tools/{name}")


def _cmd(name: str, description: str) -> CommandInfo:
    return CommandInfo(name=name, description=description, params_json_schema={})


def test_humanize_tool_label_never_leaves_raw_separators():
    assert humanize_tool_label("ask_knowledge_base") == "Ask Knowledge Base"
    assert humanize_tool_label("rca-tools") == "Rca Tools"
    assert humanize_tool_label("pkg:cmd") == "Pkg Cmd"
    assert humanize_tool_label("") == ""  # degenerate input stays empty


def test_flat_catalog_includes_builtins_with_human_label_and_summary():
    cat = flat_catalog(packages=[])
    meta = cat["ask_knowledge_base"]
    assert isinstance(meta, ToolMeta)
    assert meta.label == "Ask Knowledge Base"  # humanized, never raw snake_case
    # one-line summary drawn from the impl docstring's first sentence
    assert meta.description.startswith("Ask the knowledge-base agent")
    assert "\n" not in meta.description


def test_flat_catalog_includes_package_commands_by_command_name():
    """A tool card looks a package command up by the command name the LLM calls,
    so the flat catalog keys those, not the package name."""
    cat = flat_catalog([_pkg("rca-tools", _cmd("spc", "Run an SPC chart over a column."))])
    assert cat["spc"].label == "Spc"
    assert cat["spc"].description.startswith("Run an SPC chart")


def test_picker_unit_for_pkg_cmd_entry_resolves_the_command():
    """A scoped ``pkg:cmd`` app entry resolves to that one command's meta, keyed
    by the full entry string so a pref lines up."""
    units = picker_units(
        ["rca-tools:spc"],
        packages=[_pkg("rca-tools", _cmd("spc", "Run an SPC chart over a column."))],
    )
    assert units[0].name == "rca-tools:spc"
    assert units[0].label == "Spc"
    assert units[0].description.startswith("Run an SPC chart")


def test_picker_unit_for_pkg_cmd_entry_without_built_package_degrades_cleanly():
    """A ``pkg:cmd`` entry whose package isn't built in this deploy still shows a
    clean label, just no description."""
    units = picker_units(["data-fetch:grab"], packages=[])
    assert units[0].name == "data-fetch:grab"
    assert units[0].label == "Grab"
    assert units[0].description == ""


def test_picker_unit_for_unknown_bare_entry_degrades_cleanly():
    """A bare entry that is neither a built-in nor a built package (deploy without
    it) still appears as a toggleable unit with a clean label, no description."""
    units = picker_units(["mystery"], packages=[])
    assert units[0].name == "mystery"
    assert units[0].label == "Mystery"
    assert units[0].description == ""


def test_summarize_description_handles_empty_text():
    from workspace_app.tooling.catalog import summarize_description

    assert summarize_description("") == ""
    assert summarize_description("   \n  ") == ""


def test_picker_units_one_per_app_entry_with_package_summary():
    """The picker's pickable unit is each ``app.json`` ``tools[]`` entry verbatim
    (so a pref key matches what resolve adds/removes). A bare package entry
    becomes one unit whose description lists the tools it bundles."""
    units = picker_units(
        ["exec", "rca-tools"],
        packages=[
            _pkg(
                "rca-tools",
                _cmd("spc", "Run an SPC chart."),
                _cmd("pareto", "Pareto of defect modes."),
            )
        ],
    )
    by_name = {u.name: u for u in units}
    assert list(by_name) == ["exec", "rca-tools"]  # one unit per entry, in order
    assert by_name["exec"].label == "Exec"
    assert by_name["exec"].description.startswith("Run a shell command")
    # the package entry advertises what it grants
    assert "Spc" in by_name["rca-tools"].description
    assert "Pareto" in by_name["rca-tools"].description
