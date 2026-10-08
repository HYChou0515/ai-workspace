"""What the model calls a third-party tool's command
(docs/plan-third-party-tool-names.md).

Two third-party tools are written by people who never see each other's
command names, so `list-files` in both is ordinary, not a mistake. The model
sees `<local name>__<command>` for every third-party command; the local name
is the operator's key in `app.json` `external_tools`, unique in an App.
First-party packages keep their flat names — those are ours to keep apart.
"""

from __future__ import annotations

import pytest

from workspace_app.tooling.registry import CommandInfo, PackageInfo, build_function_tools


def _pkg(name: str, *commands: str, third_party: bool = True) -> PackageInfo:
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(
                name=c,
                description=f"{name}'s {c}",
                params_json_schema={"type": "object", "properties": {}},
            )
            for c in commands
        ),
        install_dir=f"../.tools/{name}",
        third_party=third_party,
    )


def test_two_third_party_tools_with_one_command_name_both_reach_the_model() -> None:
    tools = build_function_tools([_pkg("a", "list-files"), _pkg("b", "list-files")], allowed=None)

    assert sorted(t.name for t in tools) == ["a__list-files", "b__list-files"]


def test_a_third_party_name_does_not_depend_on_what_else_is_installed() -> None:
    """N1: stable — adding `b` must not rename `a`'s command."""
    alone = build_function_tools([_pkg("a", "list-files")], allowed=None)
    beside = build_function_tools([_pkg("a", "list-files"), _pkg("b", "other")], allowed=None)

    assert [t.name for t in alone] == ["a__list-files"]
    assert "a__list-files" in [t.name for t in beside]


def test_first_party_commands_keep_their_flat_names() -> None:
    (tool,) = build_function_tools([_pkg("rca-tools", "spc", third_party=False)], allowed=None)

    assert tool.name == "spc"


def test_two_first_party_packages_sharing_a_command_is_still_our_fault_to_fix() -> None:
    """D2: first-party names are ours to keep apart; a clash stays loud."""
    with pytest.raises(ValueError, match="cross-package tool name collision"):
        build_function_tools(
            [_pkg("x", "fetch", third_party=False), _pkg("y", "fetch", third_party=False)],
            allowed=None,
        )


def test_a_first_party_and_a_third_party_command_of_one_name_both_reach_the_model() -> None:
    tools = build_function_tools(
        [_pkg("rca-tools", "list-files", third_party=False), _pkg("a", "list-files")],
        allowed=None,
    )

    assert sorted(t.name for t in tools) == ["a__list-files", "list-files"]


def test_a_name_a_provider_would_refuse_drops_that_command_only(caplog) -> None:  # noqa: ANN001
    """D1: one invalid name in the list makes the provider reject the whole
    turn; the command is left out and the log says which one and why."""
    tools = build_function_tools(
        [_pkg("my.tool", "run"), _pkg("a", "x" * 70), _pkg("b", "list-files")],
        allowed=None,
    )

    assert [t.name for t in tools] == ["b__list-files"]
    assert "my.tool:run" in caplog.text and "over 64" in caplog.text


async def test_the_sandbox_is_asked_for_the_command_by_its_own_name(monkeypatch) -> None:  # noqa: ANN001
    """The prefix is the model's; the bundle's launcher knows only `list-files`."""
    import json

    from workspace_app.tooling import registry

    asked: list[str] = []

    async def exec_tool(actx, handle, pkg, cmd_name, args_json):  # noqa: ANN001, ANN202
        asked.append(f"{pkg.name}:{cmd_name}")
        from workspace_app.sandbox.protocol import ExecResult

        return ExecResult(stdout=b"{}", stderr=b"", exit_code=0)

    monkeypatch.setattr(registry, "_exec_tool", exec_tool)
    (tool,) = build_function_tools([_pkg("a", "list-files")], allowed=None)

    from agents import RunContextWrapper

    from workspace_app.agent.context import AgentToolContext

    actx = AgentToolContext(sandbox=object())  # ty: ignore[invalid-argument-type]

    async def ensure_sandbox(**_kw):  # noqa: ANN003, ANN202
        class _H:
            id = "h"

        return _H()

    monkeypatch.setattr(actx, "ensure_sandbox", ensure_sandbox)

    await tool.on_invoke_tool(RunContextWrapper(actx), json.dumps({}))  # ty: ignore[invalid-argument-type]

    assert asked == ["a:list-files"]
