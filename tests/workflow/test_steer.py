"""The steerer (#288, manual §10) — propose a SteerPlan from a free-text instruction.

A read-only agent turn reads the run's inputs + journal and proposes which input
files to rewrite + which steps to invalidate. It only PROPOSES; apply_steer commits.
"""

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.run import SteerInputEdit, SteerPlan
from workspace_app.workflow.steer import (
    SteerProposalFailed,
    apply_steer,
    propose_steer,
)

_PLAN_REPLY = (
    "```json\n"
    '{"rationale": "switch targets and re-ingest", '
    '"input_edits": [{"path": "collections.json", "content": "[{\\"id\\": \\"a\\"}]"}], '
    '"invalidate": ["ingest"]}\n'
    "```"
)


def _handle() -> WorkflowHandle:
    return WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", workflow_id="collections")


async def test_propose_steer_parses_plan_and_includes_context():
    """The steerer drives a read-only turn whose prompt carries the instruction, the
    editable input files, and the journaled steps; its JSON reply parses into a
    SteerPlan stamped with the original instruction."""
    wf = _handle()
    await wf.write("collections.json", '[{"id": "old"}]')
    await wf.write("/.workflow/collections/step_ingest/main.json", '{"hash": "h", "result": {}}')
    seen: dict[str, object] = {}

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        seen["prompt"] = prompt
        seen["tools"] = tools
        return _PLAN_REPLY

    wf.drive_turn = drive_turn
    plan = await propose_steer(wf, instruction="use the a collection")

    assert plan.instruction == "use the a collection"
    assert plan.rationale == "switch targets and re-ingest"
    assert plan.invalidate == ["ingest"]
    assert plan.input_edits[0].path == "collections.json"
    assert plan.input_edits[0].content == '[{"id": "a"}]'
    # the prompt is grounded in the run's actual state + the ask
    assert "use the a collection" in str(seen["prompt"])
    assert "collections.json" in str(seen["prompt"])  # editable file listed + inlined
    assert "ingest" in str(seen["prompt"])  # journaled step offered for invalidation
    assert seen["tools"] == ["read_file", "list_files"]  # read-only


async def test_propose_steer_retries_with_feedback_on_garbage():
    """A first unparseable reply is retried with the parse error fed back; the second,
    valid reply is accepted."""
    wf = _handle()
    await wf.write("collections.json", "[]")
    replies = ["sorry, here is my plan but no json", _PLAN_REPLY]
    prompts: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        prompts.append(prompt)
        return replies[len(prompts) - 1]

    wf.drive_turn = drive_turn
    plan = await propose_steer(wf, instruction="x", retries=2)
    assert plan.invalidate == ["ingest"]
    assert len(prompts) == 2
    assert "previous reply was unusable" in prompts[1]


async def test_propose_steer_raises_after_exhausting_retries():
    """No usable plan after all retries → SteerProposalFailed (the API surfaces it)."""
    wf = _handle()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return "never any json here"

    wf.drive_turn = drive_turn
    with pytest.raises(SteerProposalFailed):
        await propose_steer(wf, instruction="x", retries=1)


async def test_propose_steer_rejects_a_journal_path_edit():
    """The steerer may not hand-edit the journal — a proposed edit under /.workflow/ is
    rejected (and, here, never recovered) so the engine stays the journal's only writer."""
    wf = _handle()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return '{"input_edits": [{"path": "/.workflow/x/step_a/main.json", "content": "{}"}]}'

    wf.drive_turn = drive_turn
    with pytest.raises(SteerProposalFailed):
        await propose_steer(wf, instruction="hack the journal", retries=0)


async def test_propose_steer_rejects_a_no_op_plan():
    """An empty plan (no edits, no invalidations) is not a real steer — it's retried,
    then fails."""
    wf = _handle()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return '{"rationale": "do nothing", "input_edits": [], "invalidate": []}'

    wf.drive_turn = drive_turn
    with pytest.raises(SteerProposalFailed, match="at least one"):
        await propose_steer(wf, instruction="x", retries=0)


async def test_propose_steer_inlines_binary_and_truncates_large_inputs():
    """Binary inputs are summarised, not dumped; oversized text is truncated — so a big
    or non-text input can't blow the steerer's context window."""
    wf = _handle()
    await wf.write("scan.pdf", b"\x89PNG\x00\xff\xfe binary")
    await wf.write("big.txt", "x" * 9000)
    captured: dict[str, str] = {}

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        captured["prompt"] = prompt
        return _PLAN_REPLY

    wf.drive_turn = drive_turn
    await propose_steer(wf, instruction="x")
    assert "binary" in captured["prompt"]  # scan.pdf summarised
    assert "truncated" in captured["prompt"]  # big.txt cut
    assert "x" * 9000 not in captured["prompt"]


async def test_propose_steer_without_a_turn_driver_is_a_wiring_error():
    """An unwired handle (no drive_turn) is a programming error, not a steer outcome."""
    wf = _handle()
    with pytest.raises(RuntimeError, match="turn driver"):
        await propose_steer(wf, instruction="x")


@pytest.mark.parametrize(
    "reply",
    [
        "```\njust prose, no json\n```",  # fenced but no object
        "{this is not, valid json}",  # braces but unparseable
        '{"input_edits": "not-a-list"}',  # wrong type for a field
        '{"input_edits": [{"path": "x"}]}',  # an edit missing its content
    ],
)
async def test_propose_steer_rejects_malformed_replies(reply: str):
    """A spread of malformed steerer replies all fail cleanly (none slips through as a
    half-formed plan)."""
    wf = _handle()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return reply

    wf.drive_turn = drive_turn
    with pytest.raises(SteerProposalFailed):
        await propose_steer(wf, instruction="x", retries=0)


async def test_propose_steer_offers_each_journaled_step_once():
    """A step with several journaled artifacts (a per-element loop) is offered for
    invalidation once, not once per element."""
    wf = _handle()
    await wf.write("/.workflow/collections/step_classify/file_1.json", "{}")
    await wf.write("/.workflow/collections/step_classify/file_2.json", "{}")
    captured: dict[str, str] = {}

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        captured["prompt"] = prompt
        return _PLAN_REPLY

    wf.drive_turn = drive_turn
    await propose_steer(wf, instruction="x")
    assert captured["prompt"].count("invalidate by name: classify") == 1


# ── apply_steer (#288 P4) ──────────────────────────────────────────────────


async def test_apply_steer_writes_edits_deletes_invalidated_and_journals_receipt():
    """A confirmed plan rewrites the input file, deletes ALL artifacts of each
    invalidated step (so it re-runs), leaves other steps' artifacts intact, and
    journals an audit receipt naming who decided + what was deleted."""
    wf = _handle()
    await wf.write("collections.json", "[]")
    await wf.write("/.workflow/collections/step_ingest/a.json", "{}")
    await wf.write("/.workflow/collections/step_ingest/b.json", "{}")
    await wf.write("/.workflow/collections/step_classify/x.json", "{}")  # must survive
    plan = SteerPlan(
        instruction="use the a collection",
        rationale="switch ingest target",
        input_edits=[SteerInputEdit(path="collections.json", content='[{"id": "a"}]')],
        invalidate=["ingest"],
    )

    receipt = await apply_steer(wf, plan, decided_by="alice")

    assert await wf.read_text("collections.json") == '[{"id": "a"}]'
    assert not await wf.exists("/.workflow/collections/step_ingest/a.json")
    assert not await wf.exists("/.workflow/collections/step_ingest/b.json")
    assert await wf.exists("/.workflow/collections/step_classify/x.json")  # untouched
    rec = await wf.read_json(receipt)
    assert rec["decided_by"] == "alice"
    assert rec["instruction"] == "use the a collection"
    assert rec["invalidate"] == ["ingest"]
    assert sorted(rec["deleted"]) == [
        ".workflow/collections/step_ingest/a.json",
        ".workflow/collections/step_ingest/b.json",
    ]


async def test_apply_steer_receipts_are_sequential():
    """Each steer journals its own receipt, so a run's steer history is auditable."""
    wf = _handle()
    plan = SteerPlan(instruction="a", invalidate=["x"])
    first = await apply_steer(wf, plan)
    second = await apply_steer(wf, plan)
    assert first != second
    assert first.endswith("0001.json")
    assert second.endswith("0002.json")


async def test_apply_steer_refuses_to_write_into_the_journal():
    """Defense-in-depth: even if a journal-path edit slipped past propose_steer, apply
    refuses it — the engine stays the journal's only writer."""
    wf = _handle()
    plan = SteerPlan(
        instruction="x",
        input_edits=[SteerInputEdit(path="/.workflow/collections/step_a/main.json", content="{}")],
    )
    with pytest.raises(ValueError, match="journal"):
        await apply_steer(wf, plan)


async def test_apply_steer_invalidating_an_absent_step_is_a_no_op():
    """Invalidating a step with no artifacts (never ran / already cleared) deletes
    nothing but still applies + journals — the resume just runs it fresh."""
    wf = _handle()
    plan = SteerPlan(instruction="x", invalidate=["never_ran"])
    receipt = await apply_steer(wf, plan)
    rec = await wf.read_json(receipt)
    assert rec["deleted"] == []
