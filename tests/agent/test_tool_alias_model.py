"""A model calling a third-party command by an old name
(docs/plan-third-party-tool-names.md N2).

The model's list says `a__list-files`, but a skill or a page written before
the prefix says `list-files` — and the SDK ends the whole turn on a call to a
name it does not hold. So the call is renamed at the model-output boundary,
before the SDK resolves it: a name only one command answers to becomes that
command; a name two answer to becomes a call that fails, with today's
collision message and the names to use instead.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from workspace_app.agent.args_recovery import ambiguous_call_reply
from workspace_app.agent.tool_alias_model import ToolAliasModel
from workspace_app.tooling.registry import CommandInfo, PackageInfo, third_party_aliases


def _pkg(name: str, *commands: str, third_party: bool = True) -> PackageInfo:
    return PackageInfo(
        name=name,
        commands=tuple(
            CommandInfo(name=c, description="d", params_json_schema={"type": "object"})
            for c in commands
        ),
        install_dir=f"../.tools/{name}",
        third_party=third_party,
    )


def _fc(name: str, args: str = "{}"):
    return NS(type="function_call", name=name, arguments=args)


class _Inner:
    def __init__(self, events: list):
        self._events = events

    async def get_response(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
        return NS(output=[e.item for e in self._events])

    async def stream_response(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
        for e in self._events:
            yield e


async def _stream(model, *items) -> list:  # noqa: ANN001
    events = [NS(type="response.output_item.done", item=i) for i in items]
    events.append(NS(type="response.completed", response=NS(output=list(items))))
    inner = _Inner(events)
    out = [c async for c in ToolAliasModel(inner, model).stream_response()]  # ty: ignore[invalid-argument-type]
    return [e.item for e in out if e.type == "response.output_item.done"]


def test_the_alias_table_is_every_granted_third_party_command_by_its_old_names() -> None:
    aliases = third_party_aliases(
        [
            _pkg("a", "list-files", "trend"),
            _pkg("b", "list-files"),
            _pkg("rca", "spc", third_party=False),
        ],
        allowed=None,
    )

    assert aliases["trend"] == (("a", "a__trend"),)
    assert aliases["a:trend"] == (("a", "a__trend"),)
    assert aliases["list-files"] == (("a", "a__list-files"), ("b", "b__list-files"))
    assert "spc" not in aliases, "a first-party command is already called by its own name"


def test_only_granted_commands_have_aliases() -> None:
    """D3: an old name cannot reach a command this turn was not granted."""
    aliases = third_party_aliases([_pkg("a", "list-files"), _pkg("b", "list-files")], allowed=["a"])

    assert aliases["list-files"] == (("a", "a__list-files"),)


async def test_an_old_name_one_command_answers_to_becomes_that_command() -> None:
    aliases = {"trend": (("a", "a__trend"),)}

    (item,) = await _stream(aliases, _fc("trend", '{"lot": "L1"}'))

    assert (item.name, item.arguments) == ("a__trend", '{"lot": "L1"}')


async def test_a_name_the_model_was_given_is_left_alone() -> None:
    (item,) = await _stream({"trend": (("a", "a__trend"),)}, _fc("a__trend"))

    assert item.name == "a__trend"


async def test_an_old_name_two_commands_answer_to_fails_that_call_with_todays_message() -> None:
    aliases = {"list-files": (("a", "a__list-files"), ("b", "b__list-files"))}

    (item,) = await _stream(aliases, _fc("list-files", '{"path": "."}'))

    reply = ambiguous_call_reply(json.loads(item.arguments))
    assert reply is not None
    assert reply.startswith(
        "cross-package tool name collision: command 'list-files' appears in packages ['a', 'b']"
    )
    assert "a__list-files" in reply and "b__list-files" in reply
    # It lands on a tool the SDK holds, so the SDK does not end the turn.
    assert item.name in {"a__list-files", "b__list-files"}


def test_ordinary_arguments_are_not_an_ambiguous_call() -> None:
    assert ambiguous_call_reply({"path": "."}) is None


def test_a_turn_with_third_party_tools_answers_their_old_names() -> None:
    """Through the turn's own composition (`_agent_for`): the model is
    wrapped with the table, minus names the model was given (D6) — a
    third-party `read_file` does not steal the built-in's name."""
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    agent = _agent_for(
        AgentConfig(name="a", model="ollama_chat/x"),
        packages=[_pkg("a", "list-files", "read_file"), _pkg("b", "list-files")],
    )

    names = {t.name for t in agent.tools}
    assert {"a__list-files", "b__list-files", "a__read_file"} <= names
    model = agent.model
    assert isinstance(model, ToolAliasModel)
    assert model._aliases["list-files"] == (("a", "a__list-files"), ("b", "b__list-files"))  # noqa: SLF001
    assert "read_file" not in model._aliases  # noqa: SLF001


async def test_the_failed_call_runs_nothing_and_the_model_reads_why(monkeypatch) -> None:  # noqa: ANN001
    """End to end through the turn's real tool: the rewritten call reaches
    `a__list-files`, whose wrap answers with the collision — the sandbox is
    never asked."""
    from agents.tool_context import ToolContext

    from workspace_app.agent.context import AgentToolContext
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig
    from workspace_app.tooling import registry

    async def no_exec(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("an ambiguous call ran a command")

    monkeypatch.setattr(registry, "_exec_tool", no_exec)
    agent = _agent_for(
        AgentConfig(name="a", model="ollama_chat/x"),
        packages=[_pkg("a", "list-files"), _pkg("b", "list-files")],
    )
    (item,) = await _stream(agent.model._aliases, _fc("list-files", '{"path": "."}'))  # noqa: SLF001  # ty: ignore[unresolved-attribute]
    tool = next(t for t in agent.tools if t.name == item.name)
    actx = AgentToolContext(sandbox=object())  # ty: ignore[invalid-argument-type]
    ctx = ToolContext(
        context=actx, tool_name=item.name, tool_call_id="c1", tool_arguments=item.arguments
    )

    out = await tool.on_invoke_tool(ctx, item.arguments)  # ty: ignore[unresolved-attribute]

    assert "cross-package tool name collision: command 'list-files'" in str(out)


def test_the_system_prompt_lists_third_party_commands_by_their_new_names() -> None:
    """P4: the inventory the model reads names what it can call."""
    from workspace_app.api.litellm_runner import _agent_for
    from workspace_app.resources.agent_config import AgentConfig

    agent = _agent_for(
        AgentConfig(name="a", model="ollama_chat/x"),
        packages=[_pkg("a", "list-files"), _pkg("b", "list-files")],
    )

    assert isinstance(agent.instructions, str)
    assert "a__list-files" in agent.instructions and "b__list-files" in agent.instructions


async def test_a_non_streamed_response_is_renamed_too() -> None:
    """`Runner.run` (no stream) reads `get_response`; the same rename applies."""
    inner = _Inner([NS(type="response.output_item.done", item=_fc("trend"))])
    model = ToolAliasModel(inner, {"trend": (("a", "a__trend"),)})  # ty: ignore[invalid-argument-type]

    response = await model.get_response()

    assert [i.name for i in response.output] == ["a__trend"]


async def test_events_other_than_finished_items_pass_through_untouched() -> None:
    delta = NS(type="response.output_text.delta", delta="hi")
    inner = _Inner([delta, NS(type="response.completed", response=NS(output=None))])

    out = [
        e
        async for e in ToolAliasModel(inner, {"x": (("a", "a__x"),)}).stream_response()  # ty: ignore[invalid-argument-type]
    ]

    assert out[0] is delta and len(out) == 2


def test_the_wrapped_model_still_answers_for_its_own_attributes() -> None:
    """Code that reads e.g. `agent.model.model` (the model id) keeps working."""
    inner = NS(model="ollama_chat/x")

    assert ToolAliasModel(inner, {}).model == "ollama_chat/x"  # ty: ignore[invalid-argument-type]


def test_a_message_item_is_not_a_call_and_is_left_alone() -> None:
    from workspace_app.agent.tool_alias_model import _rename

    item = NS(type="message", name="trend")
    _rename(item, {"trend": (("a", "a__trend"),)})

    assert item.name == "trend"


def test_a_sentinel_that_is_not_one_of_ours_is_ordinary_arguments() -> None:
    """A tool whose own argument happens to use the key is not failed."""
    from workspace_app.agent.arg_repair import AMBIGUOUS_CALL_KEY

    assert ambiguous_call_reply({AMBIGUOUS_CALL_KEY: "x"}) is None
    assert ambiguous_call_reply({AMBIGUOUS_CALL_KEY: {"called": "x", "candidates": []}}) is None


def test_a_mangled_sentinel_is_ordinary_arguments_not_a_crash() -> None:
    """A model copying the sentinel shape from history with a bad entry must
    not end the turn: `len(1)` raised out of the tool wrap."""
    from workspace_app.agent.arg_repair import AMBIGUOUS_CALL_KEY

    assert ambiguous_call_reply({AMBIGUOUS_CALL_KEY: {"called": "x", "candidates": [1]}}) is None
    assert ambiguous_call_reply({AMBIGUOUS_CALL_KEY: {"called": "x", "candidates": 5}}) is None
