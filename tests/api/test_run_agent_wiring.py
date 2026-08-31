"""A definition written into an item's workspace is delegatable on the next turn.

Built through `create_app` so the wiring under test is the real one: the turn
context the send path actually hands the runner.
"""

from __future__ import annotations

import workspace_app.api.app as app_mod
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.sandbox.mock import MockSandbox

_DEF = (
    "---\n"
    "name: log-digger\n"
    "description: Digs through logs\n"
    "tools: [read_file, delete_file]\n"
    "---\n"
    "\n"
    "You dig logs.\n"
)


async def _dummy_subagent(*_a, **_k):
    return "", []


def _build(monkeypatch):
    spec = make_spec()
    filestore = SpecstarFileStore(spec)
    captured: dict[str, object] = {}
    real = app_mod.TurnContextBuilder

    def _capture(**kw):
        b = real(**kw)
        captured["builder"] = b
        return b

    monkeypatch.setattr(app_mod, "TurnContextBuilder", _capture)
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([RunDone()]),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return filestore, captured["builder"], item_id


async def test_a_definition_in_the_workspace_is_delegatable_on_the_next_turn(monkeypatch):
    filestore, builder, item_id = _build(monkeypatch)
    await filestore.write(item_id, "/.agent/log-digger/AGENT.md", _DEF.encode())

    ctx = await builder.build_chat_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )

    assert [d.name for d in ctx.subagent_defs] == ["log-digger"]
    # The App's ceiling still decides: playground grants no delete_file.
    assert ctx.subagent_defs[0].tools == ["read_file"]
    assert ctx.run_agent is not None


async def test_a_turn_that_cannot_delegate_does_not_even_read_the_definitions(monkeypatch):
    """An App that never opted into `run_agent` was paying one workspace listing
    per turn for an answer it could not use — topic-hub grants neither delegation
    tool, so the read was pure waste. Asserted through the builder rather than by
    timing, so it stays true when the store gets faster."""
    filestore, builder, item_id = _build(monkeypatch)
    await filestore.write(item_id, "/.agent/log-digger/AGENT.md", _DEF.encode())

    reads: list[str] = []
    real_ls = builder._files.ls

    async def counting_ls(workspace_id, prefix=""):
        reads.append(prefix)
        return await real_ls(workspace_id, prefix)

    monkeypatch.setattr(builder._files, "ls", counting_ls)

    ctx = await builder.build_chat_turn(
        item_id,
        agent_config=AgentConfig(name="no-delegation", allowed_tools=["read_file"]),
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )

    assert ctx.subagent_defs == ()
    assert "/.agent/" not in reads


async def test_turning_a_tool_off_for_this_item_also_takes_it_from_hand_written_definitions(
    monkeypatch,
):
    """The per-item toggle has to reach a definition the USER wrote, or it is a
    suggestion rather than a control.

    This is the half that was unguarded: an adversarial review replaced the
    turn-resolved ceiling with the App ceiling and 531 tests still passed,
    because the only test reaching here passed `agent_config=None` — which takes
    the other branch. So this one narrows the turn on purpose.
    """
    filestore, builder, item_id = _build(monkeypatch)
    await filestore.write(item_id, "/.agent/log-digger/AGENT.md", _DEF.encode())

    ctx = await builder.build_chat_turn(
        item_id,
        # The item's own toggles resolved down to this: no read_file this turn.
        agent_config=AgentConfig(name="narrow", allowed_tools=["list_files", "run_agent"]),
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )

    [defn] = ctx.subagent_defs
    assert defn.tools == [], "the definition asked for read_file, which this turn no longer holds"
