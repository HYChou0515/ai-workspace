"""plan-tools-picker-groups part 2: `command_grants` — the one rule that turns a
ceiling of `app.json` entries plus an item's tri-state prefs into the commands
the agent gets, at COMMAND granularity even for a whole-package grant.

Both consumers (the runner's `_agent_for` and the picker route) call this; the
tests here are the rule's own, and `test_tools_routes.py` / the runner tests
pin that each consumer reads it."""

from __future__ import annotations

from workspace_app.tooling.catalog import command_grants, expand_entries, unit_pref
from workspace_app.tooling.registry import CommandInfo, PackageInfo


def _pkg(name: str, *cmds: str) -> PackageInfo:
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(name=c, description=f"{c}.", params_json_schema={}) for c in cmds
        ),
        install_dir=f"../.tools/{name}",
    )


RCA = _pkg("rca-tools", "spc", "pareto", "wafer-history")
CSV = _pkg("csv-column-summary", "summarise", "plot")
CARRIER = _pkg("python-stack")  # a runtime carrier: zero commands


def test_expand_a_whole_package_grant_into_the_commands_it_has_now():
    units = expand_entries(["exec", "rca-tools", "csv-column-summary:plot", "mystery"], [RCA, CSV])
    assert units == [
        "exec",  # a built-in passes through
        "rca-tools:spc",
        "rca-tools:pareto",
        "rca-tools:wafer-history",  # the whole package = every command it has, in its order
        "csv-column-summary:plot",  # an entry already at command granularity stays
        "mystery",  # nothing resolves it: stays as written
    ]


def test_a_package_with_no_commands_stays_one_unit():
    """`python-stack` carries a Python environment and exports nothing to call;
    expanding it to zero rows would make its switch vanish."""
    assert expand_entries(["python-stack"], [CARRIER]) == ["python-stack"]


def test_pref_precedence_is_command_then_package_then_nothing():
    prefs = {"rca-tools": False, "rca-tools:spc": True}
    assert unit_pref("rca-tools:spc", prefs) is True  # the command's own key wins
    assert unit_pref("rca-tools:pareto", prefs) is False  # falls back to the package key
    assert unit_pref("csv-column-summary:plot", prefs) is None  # nobody pinned it
    assert unit_pref("exec", {"exec": False}) is False


def test_grants_follow_the_default_set_when_nothing_is_pinned():
    g = command_grants(
        ceiling=["exec", "rca-tools", "csv-column-summary"],
        default_entries=["exec", "rca-tools"],  # the profile narrowed csv away
        prefs={},
        packages=[RCA, CSV],
    )
    assert g.enabled == ("exec", "rca-tools:spc", "rca-tools:pareto", "rca-tools:wafer-history")
    assert g.disabled == ("csv-column-summary:summarise", "csv-column-summary:plot")
    assert g.default_on == frozenset(g.enabled)


def test_a_command_pref_narrows_a_whole_package_grant():
    """The user's ask: the app granted the whole package; one command is
    switched off on this item; the other commands are untouched."""
    g = command_grants(["rca-tools"], ["rca-tools"], {"rca-tools:pareto": False}, [RCA])
    assert g.enabled == ("rca-tools:spc", "rca-tools:wafer-history")
    assert g.disabled == ("rca-tools:pareto",)


def test_a_legacy_whole_package_pin_still_applies_to_every_command():
    """Items pinned before this change hold `rca-tools: false`; that must go
    on meaning "the whole package", and a command key on top overrides it."""
    g = command_grants(
        ["rca-tools"], ["rca-tools"], {"rca-tools": False, "rca-tools:spc": True}, [RCA]
    )
    assert g.enabled == ("rca-tools:spc",)
    assert g.disabled == ("rca-tools:pareto", "rca-tools:wafer-history")


def test_a_command_pin_can_re_add_one_command_the_profile_narrowed_away():
    g = command_grants(
        ceiling=["rca-tools"], default_entries=[], prefs={"rca-tools:spc": True}, packages=[RCA]
    )
    assert g.enabled == ("rca-tools:spc",)
    assert "rca-tools:spc" not in g.default_on


def test_enabled_and_disabled_partition_the_expanded_ceiling_in_ceiling_order():
    ceiling = ["csv-column-summary", "exec", "rca-tools"]
    g = command_grants(ceiling, ceiling, {"exec": False, "rca-tools:pareto": False}, [RCA, CSV])
    expanded = expand_entries(ceiling, [RCA, CSV])
    assert sorted(g.enabled + g.disabled, key=expanded.index) == expanded  # a partition
    assert set(g.enabled).isdisjoint(g.disabled)
    assert list(g.disabled) == [u for u in expanded if u in g.disabled]  # each keeps ceiling order
    assert g.enabled == (
        "csv-column-summary:summarise",
        "csv-column-summary:plot",
        "rca-tools:spc",
        "rca-tools:wafer-history",
    )


def test_without_packages_nothing_expands_and_the_old_entry_rule_holds():
    """No package inventory ⇒ entries are the units, prefs apply per entry,
    defaults per entry — the same reading `_apply_tool_prefs` gives at entry
    level (set-wise; this asserts the fixed answer, not that oracle)."""
    g = command_grants(["exec", "rca-tools"], ["exec"], {"rca-tools": True}, [])
    assert g.enabled == ("exec", "rca-tools")
    assert g.disabled == ()


# ─── P14 revision: expansion edge rules ────────────────────────────


def test_a_ceiling_naming_a_package_and_one_of_its_commands_draws_each_command_once():
    """`["rca-tools", "rca-tools:spc"]` is a redundant grant, not two grants:
    spc is one unit, at its first position. Twice would be a duplicate picker
    row and a FunctionTool built twice on every turn."""
    assert expand_entries(["rca-tools", "rca-tools:spc"], [RCA]) == [
        "rca-tools:spc",
        "rca-tools:pareto",
        "rca-tools:wafer-history",
    ]
    assert expand_entries(["rca-tools:spc", "rca-tools"], [RCA]) == [
        "rca-tools:spc",
        "rca-tools:pareto",
        "rca-tools:wafer-history",
    ]


def test_an_entry_naming_a_builtin_is_never_expanded_as_a_package():
    """A package that happens to be called like a built-in does not take the
    built-in's row: the runner's `dedupe_tools` lets the built-in outrank a
    package's copy, and the picker has to draw the same winner."""
    impostor = _pkg("exec", "a")
    assert expand_entries(["exec"], [impostor]) == ["exec"]


# ─── P14 revision: narrowing is an intersection, at either granularity ─


def test_narrow_keeps_what_is_held_verbatim():
    from workspace_app.tooling.catalog import narrow_entries

    held = ["read_file", "rca-tools:spc", "rca-tools:pareto"]
    assert narrow_entries(["read_file", "rca-tools:pareto"], held) == [
        "read_file",
        "rca-tools:pareto",
    ]


def test_narrow_expands_a_bare_package_to_the_commands_held_of_it():
    """A step or a sub-agent definition says `rca-tools`; the turn holds two of
    its three commands (the third was pinned off). It gets those two — the
    item's pins bind the delegate too — sorted by name."""
    from workspace_app.tooling.catalog import narrow_entries

    held = ["read_file", "rca-tools:wafer-history", "rca-tools:spc"]
    assert narrow_entries(["rca-tools"], held) == ["rca-tools:spc", "rca-tools:wafer-history"]


def test_narrow_keeps_a_command_when_its_whole_package_is_held():
    """The reverse granularity: `held` still names the bare package (a config
    that never met its package list), and the entry names one command of it."""
    from workspace_app.tooling.catalog import narrow_entries

    assert narrow_entries(["rca-tools:spc"], ["rca-tools", "exec"]) == ["rca-tools:spc"]


def test_narrow_drops_what_is_not_held():
    from workspace_app.tooling.catalog import narrow_entries

    held = ["read_file", "rca-tools:spc"]
    assert narrow_entries(["exec", "rca-tools:pareto", "sci-plot", "mystery"], held) == []


def test_narrow_dedupes_and_keeps_the_entries_order():
    from workspace_app.tooling.catalog import narrow_entries

    held = ["exec", "read_file", "rca-tools:spc", "rca-tools:pareto"]
    assert narrow_entries(["rca-tools", "exec", "rca-tools:spc", "exec"], held) == [
        "rca-tools:pareto",
        "rca-tools:spc",
        "exec",
    ]
