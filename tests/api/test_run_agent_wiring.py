"""A definition written into an item's workspace is delegatable on the next turn.

Built through `create_app` so the wiring under test is the real one: the turn
context the send path actually hands the runner.
"""

from __future__ import annotations

import workspace_app.api.app as app_mod
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.api.turn_context import TurnContextBuilder
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.apps.registry import app_model
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


def _build_bare(monkeypatch):
    """The same app, but the caller creates the item — for the cases that need an
    App which never listed `run_agent` at all (topic-hub), rather than one that
    merely has it toggled off."""
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
    return filestore, captured["builder"], spec


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


async def test_the_one_switch_worth_offering_is_the_one_that_gets_offered(monkeypatch):
    """The #480 section says "ask the user to enable one". `run_agent` belongs
    there exactly when the item HAS definitions and the tool is merely toggled
    off — and that was the one case it could never reach, because the two
    round-3 fixes cancelled: the defs were skipped whenever `run_agent` was
    absent from `allowed_tools`, which is precisely when it lands in
    `disabled_tools`.

    Driven through `build_chat_turn` → `_agent_for` rather than by handing
    `has_subagents=True` to `_agent_for` directly: the earlier version of this
    test asserted a state the real path cannot produce, which is how it passed
    while the behaviour was broken."""
    from workspace_app.api.litellm_runner import _agent_for, _turn_instructions

    filestore, builder, item_id = _build(monkeypatch)
    await filestore.write(item_id, "/.agent/log-digger/AGENT.md", _DEF.encode())

    ctx = await builder.build_chat_turn(
        item_id,
        # `run_agent` toggled OFF for this item, so it is in disabled_tools.
        # `run_agent` alone: `save_subagent`'s own description contains the
        # string "run_agent", so offering it too made the assertion below true
        # no matter what the filter did.
        agent_config=AgentConfig(
            name="narrow",
            allowed_tools=["read_file"],
            disabled_tools=["run_agent"],
        ),
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )

    # The definitions are still found — being toggled off is not being opted out.
    assert [d.name for d in ctx.subagent_defs] == ["log-digger"]
    agent = _agent_for(
        ctx.agent_config,
        extra_instructions=_turn_instructions(ctx, None),
        has_subagents=bool(ctx.subagent_defs),
    )
    assert isinstance(agent.instructions, str)
    assert "run_agent" in agent.instructions  # offered, because enabling it would work

    # The control that makes the line above mean something: the SAME config with
    # nothing to delegate to must not mention it, or "it is offered" is just
    # "the string appears somewhere in a long prompt".
    with_nothing = _agent_for(
        ctx.agent_config,
        extra_instructions=_turn_instructions(ctx, None),
        has_subagents=False,
    )
    assert isinstance(with_nothing.instructions, str)
    assert "run_agent" not in with_nothing.instructions


async def test_a_turn_that_cannot_delegate_does_not_even_read_the_definitions(monkeypatch):
    """An App that never opted into `run_agent` was paying one workspace listing
    per turn for an answer it could not use — topic-hub grants neither delegation
    tool, so the read was pure waste. Asserted through the builder rather than by
    timing, so it stays true when the store gets faster."""
    filestore, builder, spec = _build_bare(monkeypatch)
    hub = app_model("topic-hub")  # hyphenated slug — the platform's own resolver
    item_id = (
        spec.get_resource_manager(hub)
        .create(hub(title="t", owner="u", profile="default"))
        .resource_id
    )
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


async def test_both_halves_of_the_turn_are_told_there_are_sub_agents(monkeypatch):
    """#624: the sizing pass must measure the tool set the runner will send. Two
    places carry `has_subagents` — `_overhead_for` (which sizes) and
    `_agent_kwargs` (which builds) — and hardcoding EITHER to False left the whole
    agent/runner/skills/context surface green, because every other fixture uses a
    config that never holds `run_agent`, so the axis was never driven.

    Its sibling `skills_reachable` has exactly this test; this is the one that was
    missing. The failure it admits is silent: overhead under-counts by
    `run_agent`'s schema, the history budget over-grants, and the endpoint
    truncates the difference away with nothing to see."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner
    from workspace_app.api.turn_context import TurnContextBuilder
    from workspace_app.tokens.protocol import LlmCredential

    filestore, builder, item_id = _build(monkeypatch)
    await filestore.write(item_id, "/.agent/log-digger/AGENT.md", _DEF.encode())

    sized: list[object] = []
    real = TurnContextBuilder._tools_tokens

    def _recording(self, agent_config, **kw):
        # Pass through, never restate the signature — a copy of the argument list
        # here would go stale exactly when this test is needed.
        sized.append(kw.get("has_subagents", "absent"))
        return real(self, agent_config, **kw)

    monkeypatch.setattr(TurnContextBuilder, "_tools_tokens", _recording)

    ctx = await builder.build_chat_turn(
        item_id,
        agent_config=AgentConfig(name="delegating", allowed_tools=["read_file", "run_agent"]),
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
    assert sized == [True]
    kwargs = LitellmAgentRunner()._agent_kwargs(ctx, None, lambda _m: LlmCredential())
    assert kwargs["has_subagents"] is True


async def test_the_curated_models_ride_create_app_onto_every_turn_context(monkeypatch):
    """plan-subagent-model-choice, the last mile: what the composition root is
    handed (`create_app(subagent_models=...)` — production passes
    `resolve_subagent_models(settings)`) lands on the turn context, which is
    where `_agent_kwargs` reads it to shape run_agent's schema. Absent ⇒ ()."""
    from workspace_app.factories import LlmEndpoint, SubagentModel

    spec = make_spec()
    filestore = SpecstarFileStore(spec)
    captured: dict[str, TurnContextBuilder] = {}
    real = app_mod.TurnContextBuilder

    def _capture(**kw):
        b = real(**kw)
        captured["builder"] = b
        return b

    monkeypatch.setattr(app_mod, "TurnContextBuilder", _capture)
    choice = SubagentModel(
        name="fast",
        description="Cheap local engine.",
        endpoint=LlmEndpoint(
            model="m-fast",
            base_url=None,
            api_key=None,
            reasoning_effort=None,
            ttft_s=8.0,
            idle_s=120.0,
            cooldown_s=30.0,
        ),
    )
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([RunDone()]),
        subagent_models=(choice,),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    builder = captured["builder"]
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
    assert ctx.subagent_models == (choice,)
