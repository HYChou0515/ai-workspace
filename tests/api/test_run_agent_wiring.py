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
