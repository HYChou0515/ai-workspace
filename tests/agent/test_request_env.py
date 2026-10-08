"""The AI asks for a missing environment variable with a card
(docs/plan-env-request-card.md).

A package tool that cannot work without a credential exits 3 and names the
variable in its output. `request_env` turns that into a card in the chat —
one button per name, "sign in" or "set" — and the turn stops there until the
user has set it and asks for a retry.
"""

from __future__ import annotations

import json

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.env_request import ENV_REQUEST_MARKER
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.tooling.registry import CommandInfo, EnvNeed, PackageInfo


def _pkg(name: str, *commands: str, env_needs=None, third_party: bool = False) -> PackageInfo:  # noqa: ANN001
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(name=c, description="d", params_json_schema={"type": "object"})
            for c in commands
        ),
        install_dir=f"../.tools/{name}",
        env_needs=env_needs,
        third_party=third_party,
    )


def _ctx(*packages: PackageInfo, outputs: dict[str, str] | None = None) -> AgentToolContext:
    actx = AgentToolContext(
        agent_config=AgentConfig(name="a", model="m", allowed_tools=[p.name for p in packages]),
        packages=list(packages),
    )
    actx.tool_outputs.update(outputs or {})
    return actx


async def _call(actx: AgentToolContext, **args) -> str:  # noqa: ANN003
    from workspace_app.agent.env_request import request_env_impl

    return await request_env_impl(RunContextWrapper(actx), **args)


def _card(reply: str) -> dict:
    at = reply.rfind(ENV_REQUEST_MARKER)
    assert at >= 0, reply
    return json.loads(reply[at + len(ENV_REQUEST_MARKER) :])


async def test_a_variable_the_tool_named_becomes_a_card() -> None:
    actx = _ctx(
        _pkg("erp", "lookup"),
        outputs={"lookup": "error: set ERP_TOKEN in the workspace's environment variables"},
    )

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN"], reason="ERP needs a sign-in")

    assert _card(reply) == {
        "tool": "lookup",
        "names": ["ERP_TOKEN"],
        "reason": "ERP needs a sign-in",
    }


async def test_a_command_the_agent_ran_with_exec_gets_no_card() -> None:
    """N2: `exec` runs without the item's variables, so setting one would
    change nothing — the model is told so instead of the user being asked."""
    actx = _ctx(_pkg("erp", "lookup"), outputs={"exec": "ERP_TOKEN is not set"})

    reply = await _call(actx, tool="exec", names=["ERP_TOKEN"], reason="r")

    assert ENV_REQUEST_MARKER not in reply
    assert "does not receive" in reply and "exec" in reply


async def test_a_built_in_tool_gets_no_card() -> None:
    actx = _ctx(_pkg("erp", "lookup"), outputs={"read_file": "ERP_TOKEN"})

    reply = await _call(actx, tool="read_file", names=["ERP_TOKEN"], reason="r")

    assert ENV_REQUEST_MARKER not in reply
    assert "does not receive" in reply


async def test_a_package_tool_this_turn_was_not_given_gets_no_card() -> None:
    actx = _ctx(_pkg("erp", "lookup"), outputs={"lookup": "ERP_TOKEN"})
    assert actx.agent_config is not None
    actx.agent_config = AgentConfig(name="a", model="m", allowed_tools=[])

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN"], reason="r")

    assert ENV_REQUEST_MARKER not in reply
    assert "lookup" in reply


async def test_a_name_the_tool_never_printed_gets_no_card() -> None:
    """D3: a name the model made up would have the user set a variable no
    tool reads."""
    actx = _ctx(_pkg("erp", "lookup"), outputs={"lookup": "error: set ERP_TOKEN"})

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN", "ERP_PASSWORD"], reason="r")

    assert ENV_REQUEST_MARKER not in reply
    assert "ERP_PASSWORD" in reply


@pytest.mark.parametrize("printed", ["error: set ERP_TOKEN_V2", "error: set MY_ERP_TOKEN"])
async def test_a_name_inside_a_longer_word_is_not_a_mention(printed: str) -> None:
    actx = _ctx(_pkg("erp", "lookup"), outputs={"lookup": printed})

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN"], reason="r")

    assert ENV_REQUEST_MARKER not in reply


async def test_a_name_the_tool_declares_needs_no_mention() -> None:
    """A tool's `env.json` already says which names it reads."""
    pkg = _pkg("erp", "lookup", env_needs=(EnvNeed(name="ERP_TOKEN"),))
    actx = _ctx(pkg, outputs={"lookup": "error: not signed in"})

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN"], reason="r")

    assert _card(reply)["names"] == ["ERP_TOKEN"]


async def test_a_name_a_dotenv_file_cannot_hold_gets_no_card() -> None:
    actx = _ctx(_pkg("erp", "lookup"), outputs={"lookup": "set ERP TOKEN"})

    reply = await _call(actx, tool="lookup", names=["ERP TOKEN"], reason="r")

    assert ENV_REQUEST_MARKER not in reply


async def test_a_card_needs_at_least_one_name() -> None:
    actx = _ctx(_pkg("erp", "lookup"), outputs={"lookup": "x"})

    reply = await _call(actx, tool="lookup", names=[], reason="r")

    assert ENV_REQUEST_MARKER not in reply


async def test_a_third_party_tool_is_named_as_the_model_calls_it() -> None:
    pkg = _pkg("mes", "lookup", third_party=True)
    actx = _ctx(pkg, outputs={"mes__lookup": "error: set MES_TOKEN"})

    reply = await _call(actx, tool="mes__lookup", names=["MES_TOKEN"], reason="r")

    assert _card(reply)["tool"] == "mes__lookup"


async def test_a_package_tool_run_is_what_a_later_request_is_checked_against(monkeypatch) -> None:  # noqa: ANN001
    """The record `request_env` reads is written by the tool's own run."""
    from workspace_app.sandbox.protocol import ExecResult
    from workspace_app.tooling import registry

    async def exec_tool(actx, handle, pkg, cmd_name, args_json):  # noqa: ANN001, ANN202
        return ExecResult(stdout=b"", stderr=b"error: set ERP_TOKEN first\n", exit_code=3)

    monkeypatch.setattr(registry, "_exec_tool", exec_tool)
    pkg = _pkg("erp", "lookup")
    actx = _ctx(pkg)
    actx.sandbox = object()  # ty: ignore[invalid-assignment]

    async def ensure_sandbox(**_kw):  # noqa: ANN003, ANN202
        class _H:
            id = "h"

        return _H()

    monkeypatch.setattr(actx, "ensure_sandbox", ensure_sandbox)
    (tool,) = registry.build_function_tools([pkg], allowed=None)
    await tool.on_invoke_tool(RunContextWrapper(actx), "{}")  # ty: ignore[invalid-argument-type]

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN"], reason="r")

    assert _card(reply)["names"] == ["ERP_TOKEN"]


# ── who gets it (D6) and where the turn stops (N3) ──────────────────────────


def _agent(tools: list[str] | None, *packages: PackageInfo):  # noqa: ANN202
    from workspace_app.api.litellm_runner import _agent_for

    names = None if tools is None else [*tools, *(p.name for p in packages)]
    return _agent_for(
        AgentConfig(name="a", model="ollama_chat/x", allowed_tools=names), packages=list(packages)
    )


def test_a_turn_that_can_ask_the_user_and_holds_a_package_tool_can_request_a_variable() -> None:
    agent = _agent(["ask_user"], _pkg("erp", "lookup"))

    assert "request_env" in {t.name for t in agent.tools}


def test_a_turn_that_cannot_ask_the_user_cannot_show_the_card() -> None:
    """D6: same audience as `ask_user` — nobody to press the button otherwise."""
    agent = _agent(["read_file"], _pkg("erp", "lookup"))

    assert "request_env" not in {t.name for t in agent.tools}


def test_a_turn_with_no_package_tool_has_nothing_to_request_for() -> None:
    agent = _agent(["ask_user"])

    assert "request_env" not in {t.name for t in agent.tools}


def test_naming_it_outright_does_not_grant_it() -> None:
    """Only the rule above grants it — a hand-written list cannot put a tool
    that would refuse every call in front of the model. Asked of `build_tools`
    itself: through `_agent_for` the rule would deny this list anyway."""
    from workspace_app.agent.tools import build_tools

    assert build_tools(["request_env"]) == []


async def _stops(agent, *results: tuple[str, str]):  # noqa: ANN001, ANN202
    """What the SDK itself decides after these tool results — the real check,
    not the shape of the setting."""
    from types import SimpleNamespace as NS

    from agents import RunContextWrapper as W
    from agents.run_internal.turn_resolution import check_for_final_output_from_tools

    tools = {t.name: t for t in agent.tools}
    fake = [NS(tool=tools[name], output=out) for name, out in results]
    return await check_for_final_output_from_tools(agent, fake, W(None))  # ty: ignore[invalid-argument-type]


async def test_the_turn_stops_at_the_card() -> None:
    agent = _agent(["ask_user"], _pkg("erp", "lookup"))
    card = f'shown{ENV_REQUEST_MARKER}{{"tool":"lookup","names":["K"],"reason":"r"}}'

    got = await _stops(agent, ("lookup", "error: set K"), ("request_env", card))

    assert got.is_final_output and got.final_output == card


async def test_a_refused_request_does_not_end_the_turn() -> None:
    """The refusal tells the model what to do instead — it has to get the turn
    to do it in (review round 1, D1)."""
    agent = _agent(["ask_user"], _pkg("erp", "lookup"))

    got = await _stops(agent, ("request_env", "error: K does not appear in what `lookup` printed"))

    assert not got.is_final_output


async def test_a_question_still_ends_the_turn() -> None:
    agent = _agent(["ask_user"], _pkg("erp", "lookup"))

    got = await _stops(agent, ("ask_user", "Asked."))

    assert got.is_final_output and got.final_output == "Asked."


def _child(parent: AgentToolContext, tools: list[str]) -> AgentToolContext:
    from workspace_app.api.subagent_run import _child_context
    from workspace_app.apps.subagents import SubagentDef

    return _child_context(parent, SubagentDef(name="d", description="", tools=tools))


def test_a_sub_agent_never_gets_it() -> None:
    """D6, through the child the delegation really builds: a definition that
    names both halves of the rule still yields a turn without the card."""
    from workspace_app.api.litellm_runner import _agent_for

    erp = _pkg("erp", "lookup")
    parent = _ctx(erp)
    parent.agent_config = AgentConfig(
        name="a", model="ollama_chat/x", allowed_tools=["ask_user", "erp"]
    )
    child = _child(parent, ["ask_user", "request_env", "erp"])

    assert child.agent_config is not None
    agent = _agent_for(child.agent_config, packages=[erp])
    assert "request_env" not in {t.name for t in agent.tools}


def test_a_sub_agent_does_not_share_the_parent_s_tool_outputs() -> None:
    """Review round 1 (D4): the check reads what THIS turn's model saw."""
    parent = _ctx(_pkg("erp", "lookup"), outputs={"lookup": "set ERP_TOKEN"})

    child = _child(parent, ["erp"])
    child.tool_outputs["lookup"] = "the child's run"

    assert child.tool_outputs == {"lookup": "the child's run"}
    assert parent.tool_outputs == {"lookup": "set ERP_TOKEN"}


# ── what the model is told when a tool exits 3 (D7) ─────────────────────────


def _blocked(actx: AgentToolContext, name: str) -> str:
    from workspace_app.agent.tools import _exec_result_text
    from workspace_app.sandbox.protocol import ExecResult

    result = ExecResult(stdout=b"", stderr=b"set ERP_TOKEN\n", exit_code=3)
    return _exec_result_text(actx, name, result).splitlines()[0]


def test_a_blocked_package_tool_points_the_model_at_the_card() -> None:
    actx = _ctx(_pkg("erp", "lookup"))
    assert actx.agent_config is not None
    actx.agent_config = AgentConfig(name="a", model="m", allowed_tools=["ask_user", "erp"])

    assert "`request_env`" in _blocked(actx, "lookup")


def test_without_the_card_a_blocked_tool_still_points_at_the_panel() -> None:
    actx = _ctx(_pkg("erp", "lookup"))

    header = _blocked(actx, "lookup")

    assert "request_env" not in header and "environment-variables panel" in header


def test_a_command_run_with_exec_is_not_sent_to_the_panel() -> None:
    """N2: setting a variable cannot help a command `exec` ran."""
    actx = _ctx(_pkg("erp", "lookup"))
    assert actx.agent_config is not None
    actx.agent_config = AgentConfig(name="a", model="m", allowed_tools=["ask_user", "erp"])

    header = _blocked(actx, "exec")

    assert "panel" not in header and "request_env" not in header
    assert "does not receive" in header


@pytest.mark.parametrize(
    ("tools", "with_package"),
    [(["ask_user"], True), (["ask_user"], False), (["read_file"], True), (None, True), ([], True)],
)
def test_the_hint_and_the_grant_answer_from_one_rule(tools, with_package) -> None:  # noqa: ANN001
    """Parity, with the turn's own tool list as the oracle: the hint never
    names a tool the model was not given, nor omits one it was."""
    from workspace_app.agent.env_request import request_env_granted

    packages = [_pkg("erp", "lookup")] if with_package else []
    agent = _agent(tools, *packages)
    config = AgentConfig(
        name="a",
        model="m",
        allowed_tools=None if tools is None else [*tools, *(p.name for p in packages)],
    )

    assert request_env_granted(config, packages) == ("request_env" in {t.name for t in agent.tools})


def test_the_export_strips_the_marker_the_tool_writes() -> None:
    """Parity: the export's copy of the marker is the tool's own."""
    from workspace_app.agent.shown_files import _CARD_DECLARATIONS

    assert (ENV_REQUEST_MARKER, "tool") in _CARD_DECLARATIONS


@pytest.mark.parametrize(
    ("pkg", "called", "recorded"),
    [
        # The grant spelling of a first-party command.
        (_pkg("erp", "lookup"), "erp:lookup", "lookup"),
        # A third-party command by the bare name a skill written before the prefix uses.
        (_pkg("mes", "lookup", third_party=True), "lookup", "mes__lookup"),
    ],
)
async def test_any_name_the_tool_answers_to_reads_its_own_output(pkg, called, recorded) -> None:  # noqa: ANN001
    """Review round 1 (D2): the tool resolves by every spelling, so its output
    must be read under the name it was recorded by — and the card names it so."""
    actx = _ctx(pkg, outputs={recorded: "error: set ERP_TOKEN"})

    reply = await _call(actx, tool=called, names=["ERP_TOKEN"], reason="r")

    assert _card(reply)["tool"] == recorded


async def test_a_name_asked_for_twice_is_one_row() -> None:
    actx = _ctx(_pkg("erp", "lookup"), outputs={"lookup": "error: set ERP_TOKEN"})

    reply = await _call(actx, tool="lookup", names=["ERP_TOKEN", "ERP_TOKEN"], reason="r")

    assert _card(reply)["names"] == ["ERP_TOKEN"]


def test_the_built_in_outranks_a_package_command_of_the_same_name() -> None:
    """Review round 1 (D5): first wins in `dedupe_tools`, and the stop rule and
    the exit-3 hint both mean the built-in."""
    agent = _agent(["ask_user"], _pkg("erp", "lookup", "request_env"))

    (tool,) = [t for t in agent.tools if t.name == "request_env"]
    assert tool.description.startswith("Ask the user to sign in")
