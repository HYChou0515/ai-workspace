"""docs/plan-workflow-out-of-chat.md P2 — a node's context is its references.

An agent node runs as a turn on its run's conversation, and used to read that
whole thread as history: the chat before a takeover, every earlier step, other
runs in a reused schedule chat, the steerer's replies. D1 / D5: a node reads
none of it. What it needs reaches it through its prompt (the upstream outputs
it names, files, `input.json`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from workspace_app.api.events import AgentEvent, MessageDelta, RunDone


class _RecordingRunner:
    """Answers "ok" and records the history each turn was handed."""

    def __init__(self) -> None:
        self.histories: list[list[dict[str, Any]]] = []
        self.prompts: list[str] = []
        self.reply = "ok"

    async def run(self, prompt: str, ctx: Any) -> AsyncIterator[AgentEvent]:
        self.prompts.append(prompt)
        self.histories.append(list(ctx.history or []))
        yield MessageDelta(text=self.reply)
        yield RunDone()


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import workspace_app.api.app as app_mod
    from workspace_app.api import create_app
    from workspace_app.api.workflow_exec import WorkflowExecutor
    from workspace_app.apps.playground.model import PlaygroundItem
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.resources import Conversation, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec()
    captured: dict[str, WorkflowExecutor] = {}
    real = app_mod.WorkflowExecutor
    monkeypatch.setattr(
        app_mod, "WorkflowExecutor", lambda **kw: captured.setdefault("ex", real(**kw))
    )
    runner = _RecordingRunner()
    create_app(spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner)
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return {
        "ex": captured["ex"],
        "runner": runner,
        "item_id": item_id,
        "conv_rm": spec.get_resource_manager(Conversation),
    }


async def test_a_node_reads_nothing_of_the_conversation_it_runs_on(stack: dict[str, Any]) -> None:
    """The thread holds what a person said before, and what earlier steps
    answered. None of it reaches the node's model."""
    from workspace_app.resources import Conversation, Message

    rid = (
        stack["conv_rm"]
        .create(
            Conversation(
                item_id=stack["item_id"],
                messages=[
                    Message(role="user", content="a person's own question"),
                    Message(role="assistant", content="an earlier step's answer"),
                ],
            )
        )
        .resource_id
    )

    await stack["ex"].drive_turn(stack["item_id"], rid, "u", "classify the files", None)

    assert stack["runner"].prompts == ["classify the files"]
    assert stack["runner"].histories == [[]]


async def test_a_step_persists_its_prompt_before_its_answer_both_marked(
    stack: dict[str, Any],
) -> None:
    """D6: the thread used to hold answers without the questions that led to
    them. Each message a step persists says which execution and which step it
    belongs to, so the execution page can show one row per step."""
    from workspace_app.resources import Conversation
    from workspace_app.workflow.steps import StepRef

    rid = stack["conv_rm"].create(Conversation(item_id=stack["item_id"])).resource_id

    await stack["ex"].drive_turn(
        stack["item_id"],
        rid,
        "u",
        "classify the files",
        None,
        run_id="run-1",
        step=StepRef("classify", "a"),
    )

    msgs = stack["conv_rm"].get(rid).data.messages
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "classify the files"),
        ("assistant", "ok"),
    ]
    assert {(m.run_id, m.step) for m in msgs} == {("run-1", "classify/a")}
    # persisted as role="user", but no person said it: off-hours autonomy asks
    # "has my owner said anything recently?" and must not hear a workflow
    from workspace_app.api.goal_offhours import last_human_message_ms

    assert last_human_message_ms(stack["conv_rm"].get(rid).data) is None


async def test_a_turn_driven_from_inside_a_step_is_marked_with_that_step(
    stack: dict[str, Any],
) -> None:
    """The handle wires `drive_turn` as `(prompt, tools)`; the step comes from
    where the step runs (`CURRENT_STEP`)."""
    from workspace_app.resources import Conversation
    from workspace_app.workflow.steps import CURRENT_STEP, StepRef

    rid = stack["conv_rm"].create(Conversation(item_id=stack["item_id"])).resource_id

    token = CURRENT_STEP.set(StepRef("digest", "a.md"))
    try:
        await stack["ex"].drive_turn(stack["item_id"], rid, "u", "p", None, run_id="run-1")
    finally:
        CURRENT_STEP.reset(token)

    assert {m.step for m in stack["conv_rm"].get(rid).data.messages} == {"digest/a.md"}


async def test_each_attempt_of_a_step_lands_in_its_journal_transcript(
    stack: dict[str, Any],
) -> None:
    """D9: "Ask AI why it failed" reads the step's journal, as a file, so a
    chat never reads a run's messages. A retried step keeps every attempt."""
    from workspace_app.resources import Conversation
    from workspace_app.workflow.steps import StepRef

    rid = stack["conv_rm"].create(Conversation(item_id=stack["item_id"])).resource_id
    turn = {"run_id": "run-1", "step": StepRef("digest", "a.md"), "journal_dir": "/.workflow/w"}

    other = {**turn, "step": StepRef("digest", "b.md")}
    await stack["ex"].drive_turn(stack["item_id"], rid, "u", "first try", None, **turn)
    await stack["ex"].drive_turn(stack["item_id"], rid, "u", "another element", None, **other)
    await stack["ex"].drive_turn(stack["item_id"], rid, "u", "second try", None, **turn)

    path = "/.workflow/w/step_digest/a.md.transcript.md"
    text = (await stack["ex"]._files.read(stack["item_id"], path)).decode()
    assert text.index("first try") < text.index("second try")
    assert text.count("ok") == 2  # each attempt's answer
    assert "another element" not in text  # one step's record, not its siblings'


async def test_a_step_run_through_the_wired_handle_marks_and_journals_its_turn(
    stack: dict[str, Any],
) -> None:
    """The path a real run takes: the handle `wire_handle` wires, driven by a
    real `agent_write_step` and a loop element of it. Passing the step and the
    journal by hand above proves the parts; this proves they are connected."""
    from workspace_app.resources import Conversation
    from workspace_app.workflow.engine import transcript_path
    from workspace_app.workflow.handle import WorkflowHandle
    from workspace_app.workflow.steps import agent_write_step

    rid = stack["conv_rm"].create(Conversation(item_id=stack["item_id"])).resource_id
    wf = WorkflowHandle(store=stack["ex"]._files, workspace_id=stack["item_id"], workflow_id="w")
    stack["ex"].wire_handle(wf, "run-1", stack["item_id"], "u", rid)

    await agent_write_step(wf, prompt="digest it", phase="digest", key="a.md", out="n/a.md")

    msgs = stack["conv_rm"].get(rid).data.messages
    assert {(m.run_id, m.step) for m in msgs} == {("run-1", "digest/a.md")}
    path = transcript_path(wf.journal_dir, "digest", "a.md")
    assert "digest it" in (await stack["ex"]._files.read(stack["item_id"], path)).decode()


async def test_a_map_elements_turn_on_its_own_lane_is_journaled_too(stack: dict[str, Any]) -> None:
    """Map elements drive their turns through `sub_turn` (a lane each), not
    `drive_turn`; that wiring has to carry the journal as well."""
    from workspace_app.resources import Conversation
    from workspace_app.workflow.engine import transcript_path
    from workspace_app.workflow.handle import WorkflowHandle
    from workspace_app.workflow.steps import CURRENT_STEP, StepRef

    rid = stack["conv_rm"].create(Conversation(item_id=stack["item_id"])).resource_id
    wf = WorkflowHandle(store=stack["ex"]._files, workspace_id=stack["item_id"], workflow_id="w")
    stack["ex"].wire_handle(wf, "run-1", stack["item_id"], "u", rid)

    assert wf.sub_turn is not None  # wired by `wire_handle`
    token = CURRENT_STEP.set(StepRef("note", "x.png"))
    try:
        await wf.sub_turn("x.png")("describe x.png", None)
    finally:
        CURRENT_STEP.reset(token)

    path = transcript_path(wf.journal_dir, "note", "x.png")
    assert "describe x.png" in (await stack["ex"]._files.read(stack["item_id"], path)).decode()


async def test_a_steer_proposal_leaves_no_trace_in_the_executions_thread(
    stack: dict[str, Any],
) -> None:
    """D2: the steerer answers in JSON, and that JSON was persisted and streamed
    as the agent's reply. Its turn is not part of the execution's record: nothing
    is persisted and nothing reaches the execution's live stream. The plan is
    shown as a confirm card instead (`pending_steer`, carrying the request)."""
    import asyncio
    import json

    from workspace_app.resources import Conversation
    from workspace_app.workflow.handle import WorkflowHandle
    from workspace_app.workflow.steer import propose_steer

    rid = stack["conv_rm"].create(Conversation(item_id=stack["item_id"])).resource_id
    wf = WorkflowHandle(store=stack["ex"]._files, workspace_id=stack["item_id"], workflow_id="w")
    stack["ex"].wire_handle(wf, "run-1", stack["item_id"], "u", rid)
    stack["runner"].reply = json.dumps(
        {"rationale": "use b", "input_edits": [], "invalidate": ["classify"]}
    )

    streamed: list[Any] = []
    sub = stack["ex"]._turn_engine.subscribe(rid)

    async def watch() -> None:
        async for ev in sub:
            streamed.append(ev)

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0)
    plan = await propose_steer(wf, instruction="use collection b")
    await asyncio.sleep(0.05)
    watcher.cancel()

    assert plan.instruction == "use collection b"
    assert stack["conv_rm"].get(rid).data.messages == []
    assert not [ev for ev in streamed if isinstance(ev, MessageDelta)]
