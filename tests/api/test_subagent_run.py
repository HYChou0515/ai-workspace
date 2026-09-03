"""`run_agent_task` — driving one sub-agent turn to completion.

The main agent delegates a whole sub-task; the sub-agent runs on a context that
is the parent's, minus its history and plus the definition's prompt and narrowed
tool set. It keeps the workspace (sandbox, files, identity) because a sub-agent
that cannot read the item's files could not do the job it was delegated.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator

import msgspec

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone, RunError, ToolStart
from workspace_app.api.subagent_run import run_agent_task
from workspace_app.apps.subagents import SubagentDef
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.resources.kb import RetrievedPassage


class _Recorder:
    """An `AgentRunner` that records the prompt + context it was driven with and
    replays a fixed event sequence."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.prompt: str | None = None
        self.ctx: AgentToolContext | None = None

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.prompt = prompt
        self.ctx = ctx
        for ev in self._events:
            yield ev


def _passage() -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="col",
        document_id="doc",
        filename="spec.md",
        start=0,
        end=1,
        source_chunk_ids=["c1"],
        text="x",
    )


def _parent() -> AgentToolContext:
    return AgentToolContext(
        investigation_id="inv-1",
        agent_config=AgentConfig(
            name="main", system_prompt="you are the main agent", allowed_tools=["exec", "read_file"]
        ),
        history=[{"role": "user", "content": "earlier chatter"}],
    )


async def test_the_sub_agent_answers_from_its_own_prompt_and_tools_on_a_clean_context():
    runner = _Recorder([MessageDelta(text="found "), MessageDelta(text="the bug"), RunDone()])
    defn = SubagentDef(name="digger", description="d", tools=["read_file"], body="you dig logs")

    answer = await run_agent_task(runner, _parent(), defn, "dig into app.log")

    assert answer == "found the bug"
    child = runner.ctx
    assert runner.prompt == "dig into app.log"
    assert child is not None and child.agent_config is not None
    assert child.agent_config.system_prompt == "you dig logs"
    assert child.agent_config.allowed_tools == ["read_file"]
    # The whole point of delegating: the sub-agent does not inherit the noise.
    assert child.history == []
    # ...but it does inherit the workspace it was delegated to work in.
    assert child.investigation_id == "inv-1"


async def test_a_sub_agent_cannot_delegate_again():
    """Non-recursion is structural, not a depth counter: the child context simply
    has no delegation seam and no definitions to name, so `run_agent` is not even
    built for its turn."""
    runner = _Recorder([RunDone()])
    defn = SubagentDef(name="digger", description="d", tools=["read_file"], body="dig")
    parent = dataclasses.replace(
        _parent(),
        run_agent=lambda *a, **k: None,
        subagent_defs=(defn,),
    )

    await run_agent_task(runner, parent, defn, "go")

    assert runner.ctx is not None
    assert runner.ctx.run_agent is None
    assert runner.ctx.subagent_defs == ()


async def test_nothing_belonging_to_the_parents_conversation_or_turn_is_inherited():
    """`_child_context` is a `dataclasses.replace`, so every field it does not
    name stays SHARED with the parent. The first version reset only the citation
    buckets, which let a sub-agent rewrite the user's todo list, chip the
    parent's answer with sources it never looked at, and re-send the parent's
    attached image on every delegation.

    Each assertion is one of those, so adding a conversation-scoped or
    turn-accumulating field to `AgentToolContext` without handling it here fails
    right away rather than in production."""
    runner = _Recorder([RunDone()])
    defn = SubagentDef(name="digger", description="d", tools=["read_file"], body="dig")
    # Every field is given a NON-default value: asserting `child.x == []` against
    # a parent that already had `[]` proves nothing, and two of these assertions
    # were exactly that until a review deleted the resets and watched the test
    # stay green.
    parent = dataclasses.replace(
        _parent(),
        conversation_id="conversation:abc",
        on_todos_updated=lambda _items: None,
        turn_image_urls=["data:image/png;base64,AAAA"],
        withheld_collection_ids=["col-x"],
        kb_passages=[_passage()],
        injected_card_ids={"card-1"},
        subagent_citations={"ask_knowledge_base": [[]]},
    )

    await run_agent_task(runner, parent, defn, "go")

    child = runner.ctx
    assert child is not None
    # Conversation-scoped: a sub-agent must not be able to act on the parent's chat.
    assert child.conversation_id is None
    assert child.on_todos_updated is None
    # The parent turn's own inputs — it "cannot see this conversation".
    assert child.turn_image_urls == []
    # Accumulators the parent's assistant message is built from.
    assert child.withheld_collection_ids == []
    assert child.kb_passages == []
    assert child.injected_card_ids == set()
    assert child.subagent_citations == {}
    # ...and none of it reached back into the parent.
    assert parent.withheld_collection_ids == ["col-x"]
    assert parent.turn_image_urls == ["data:image/png;base64,AAAA"]
    assert len(parent.kb_passages) == 1
    assert parent.injected_card_ids == {"card-1"}


async def test_a_sub_agent_that_writes_no_report_says_so():
    """A sub-turn can end without prose — it stopped on a tool call, or ran out
    of steps. An empty string is indistinguishable, to the caller, from a
    successful answer that had nothing to say."""
    runner = _Recorder([RunDone()])  # no MessageDelta at all
    defn = SubagentDef(name="digger", description="d", tools=[], body="dig")

    answer = await run_agent_task(runner, _parent(), defn, "go")

    assert "digger" in answer and "without writing a report" in answer


async def test_the_delegation_tools_are_stripped_from_the_child_not_just_unwired():
    """Nulling the seam stops recursion, but `build_tools` decides what to BUILD
    from the tool NAMES — so a definition naming `save_subagent` (which all three
    apps permit) got `run_agent` built for the child, where it could only ever
    refuse. That is the #537 shape pointed at a sub-agent."""
    runner = _Recorder([RunDone()])
    greedy = SubagentDef(
        name="digger",
        description="d",
        tools=["read_file", "run_agent", "save_subagent", "update_todos", "ask_user"],
        body="dig",
    )

    await run_agent_task(runner, _parent(), greedy, "go")

    assert runner.ctx is not None and runner.ctx.agent_config is not None
    assert runner.ctx.agent_config.allowed_tools == ["read_file"]
    from workspace_app.agent.tools import build_tools

    built = [t.name for t in build_tools(runner.ctx.agent_config.allowed_tools)]
    assert "run_agent" not in built and "save_subagent" not in built


async def test_a_failed_sub_agent_reports_back_instead_of_killing_the_turn():
    """A sub-agent failing is information the main agent can act on (try another
    approach, do it itself). Raising would end the whole turn over one delegated
    step, so the failure comes back as the tool's answer."""
    runner = _Recorder([MessageDelta(text="partial"), RunError(message="model exploded")])
    defn = SubagentDef(name="digger", description="d", tools=[], body="dig")

    answer = await run_agent_task(runner, _parent(), defn, "go")

    assert "digger" in answer and "model exploded" in answer


async def test_the_sub_agents_work_is_relayed_as_it_happens():
    """The parent turn shows a live card, so every event has to surface while the
    sub-agent is still running — not in one dump at the end."""
    seen: list[str] = []
    runner = _Recorder([ToolStart(call_id="c1", name="read_file", args={}), RunDone()])
    defn = SubagentDef(name="digger", description="d", tools=["read_file"], body="dig")

    await run_agent_task(runner, _parent(), defn, "go", on_event=lambda ev: seen.append(ev.type))

    assert seen == ["tool_start", "done"]


async def test_a_sub_agents_kb_citations_do_not_get_attributed_to_the_parent():
    """Citation buckets are paired with the PARENT's tool messages positionally
    (`bubble_kb_citations`, most-recent-call-wins). A sub-agent consulting the KB
    would otherwise append into the same accumulator and the parent's answer
    would cite a lookup the user never saw it make."""
    runner = _Recorder([RunDone()])
    defn = SubagentDef(name="digger", description="d", tools=["ask_knowledge_base"], body="dig")
    parent = _parent()
    parent.subagent_citations.setdefault("ask_knowledge_base", []).append([])

    await run_agent_task(runner, parent, defn, "go")

    assert runner.ctx is not None
    assert runner.ctx.subagent_citations == {}
    runner.ctx.subagent_citations.setdefault("ask_knowledge_base", []).append([])
    assert len(parent.subagent_citations["ask_knowledge_base"]) == 1


async def test_a_model_override_moves_the_child_onto_that_preset_endpoint():
    """plan-subagent-model-choice: the caller may hand the sub-agent a resolved
    preset endpoint; the child's config then carries ITS model + endpoint +
    key — everything else about the child (prompt swap, tool narrowing, clean
    history) is exactly the un-overridden behaviour. No override ⇒ the child
    keeps the parent's engine, byte-for-byte as today."""
    from workspace_app.factories import LlmEndpoint, SubagentModel

    ep = SubagentModel(
        name="cheap",
        description="",
        endpoint=LlmEndpoint(
            model="m-cheap",
            base_url="http://cheap:4000/v1",
            api_key="ck",
            reasoning_effort=None,
            ttft_s=8.0,
            idle_s=120.0,
            cooldown_s=30.0,
        ),
    )
    defn = SubagentDef(name="digger", description="", tools=["read_file"], body="dig")

    runner = _Recorder([MessageDelta(text="ok"), RunDone()])
    await run_agent_task(runner, _parent(), defn, "go", model=ep)
    assert runner.ctx is not None and runner.ctx.agent_config is not None
    cfg = runner.ctx.agent_config
    assert cfg.model == "m-cheap"
    assert cfg.llm_base_url == "http://cheap:4000/v1"
    assert cfg.llm_api_key == "ck"
    assert cfg.system_prompt == "dig"  # the definition still wins the prompt

    parent = _parent()
    assert parent.agent_config is not None
    plain = _Recorder([MessageDelta(text="ok"), RunDone()])
    await run_agent_task(plain, parent, defn, "go")
    assert plain.ctx is not None and plain.ctx.agent_config is not None
    assert plain.ctx.agent_config.model == parent.agent_config.model  # inherited


async def test_a_model_override_swaps_the_whole_endpoint_bundle_not_just_the_address():
    """The parent's endpoint-shaped declarations must not leak onto a picked
    engine: a parent on a vouched proxy (`reports_usage=True`) delegating to an
    unvouched local preset would send `include_usage` there and persist
    litellm's estimate as a measurement (#748/#751); `vision=True` would feed
    raw images to a text-only model. The picked preset's own declarations win —
    in BOTH directions (True→False and False→True)."""
    from workspace_app.factories import LlmEndpoint, SubagentModel

    choice = SubagentModel(
        name="local",
        description="",
        endpoint=LlmEndpoint(
            model="m-local",
            base_url=None,
            api_key=None,
            reasoning_effort=None,
            ttft_s=8.0,
            idle_s=120.0,
            cooldown_s=30.0,
        ),
        reports_usage=False,
        vision=False,
        frequency_penalty=0.5,
    )
    parent = _parent()
    assert parent.agent_config is not None
    parent = dataclasses.replace(
        parent,
        agent_config=msgspec.structs.replace(parent.agent_config, reports_usage=True, vision=True),
    )
    defn = SubagentDef(name="digger", description="", tools=[], body="dig")
    runner = _Recorder([MessageDelta(text="ok"), RunDone()])
    await run_agent_task(runner, parent, defn, "go", model=choice)
    assert runner.ctx is not None and runner.ctx.agent_config is not None
    cfg = runner.ctx.agent_config
    assert (cfg.reports_usage, cfg.vision) == (False, False)  # the preset's, not the parent's
    assert cfg.frequency_penalty == 0.5
    assert (cfg.presence_penalty, cfg.repetition_penalty) == (None, None)
