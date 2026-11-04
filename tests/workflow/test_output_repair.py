"""A workflow agent's malformed output is repaired, not the step re-run
(docs/plan-workflow-output-repair.md)."""

import json
from typing import Any

import pytest

from workspace_app.workflow.checks import artifact_valid
from workspace_app.workflow.dsl import _outputs_check
from workspace_app.workflow.engine import CheckResult, StepFailed, run_step
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.output_format import example, instruction, repair_request
from workspace_app.workflow.steps import agent_write_step

_OUTPUTS: dict[str, Any] = {"verdict": {"type": "str", "enum": ["pass", "fail"]}, "count": "int"}


# ── §3.1: every `outputs` failure is the reply's format ──────────────────────


@pytest.mark.parametrize(
    "fields",
    [
        None,  # not JSON at all
        ["pass", 1],  # JSON, not an object
        {"verdict": "pass"},  # missing field
        {"verdict": "pass", "count": "3"},  # wrong type
        {"verdict": "maybe", "count": 3},  # outside the enum
    ],
)
async def test_every_outputs_failure_is_a_format_failure(wf: WorkflowHandle, fields: Any):
    verdict = await _outputs_check(_OUTPUTS)(wf, {"fields": fields})
    assert not verdict.ok and verdict.format, verdict


async def test_a_reply_that_fits_its_outputs_passes(wf: WorkflowHandle):
    verdict = await _outputs_check(_OUTPUTS)(wf, {"fields": {"verdict": "pass", "count": 3}})
    assert verdict.ok and not verdict.format


# ── §3.3 / D8: the format instruction on the first prompt ────────────────────

_EVERY_TYPE: dict[str, Any] = {
    "s": "str",
    "i": "int",
    "f": "float",
    "b": "bool",
    "l": "list",
    "o": "obj",
    "e": {"type": "str", "enum": ["low", "high"]},
    "n": {"type": "int", "enum": [3, 5]},
}


async def test_the_example_passes_the_check_it_describes(wf: WorkflowHandle):
    """Parity with the gate as oracle: an example the check rejects would teach the model
    the wrong shape."""
    for outputs in (_OUTPUTS, _EVERY_TYPE):
        sample = json.loads(example(outputs))
        assert (await _outputs_check(outputs)(wf, {"fields": sample})).ok, sample


def test_the_instruction_names_every_field_its_type_and_allowed_values():
    text = instruction(_OUTPUTS, "text")
    assert "ONLY one JSON object" in text
    assert "verdict" in text and "count" in text and "int" in text
    assert '"pass"' in text and '"fail"' in text
    assert example(_OUTPUTS) in text


@pytest.mark.parametrize("kind", ["json", "yaml", "csv"])
def test_a_structured_out_is_told_to_reply_with_the_content_only(kind: str):
    text = instruction(None, kind)
    assert f"ONLY the {kind.upper()} content" in text


@pytest.mark.parametrize("kind", ["text", "markdown", "code"])
def test_a_prose_out_gets_no_instruction(kind: str):
    assert instruction(None, kind) == ""


async def test_the_first_prompt_ends_with_the_instruction(wf: WorkflowHandle):
    seen: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        seen.append(prompt)
        return '{"verdict": "pass", "count": 1}'

    wf.drive_turn = drive_turn
    await agent_write_step(
        wf, prompt="judge it", phase="p", name="j", outputs=_OUTPUTS, check=_outputs_check(_OUTPUTS)
    )
    assert seen == [f"judge it\n\n{instruction(_OUTPUTS, 'text')}"]


async def test_a_prose_step_keeps_its_prompt_as_written(wf: WorkflowHandle):
    seen: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        seen.append(prompt)
        return "a note"

    wf.drive_turn = drive_turn
    await agent_write_step(wf, prompt="write a note", phase="p", out="note.md")
    assert seen == ["write a note"]


# ── §3.3 / D3: the repair request ────────────────────────────────────────────


def _not_json(reply: str) -> CheckResult:
    return CheckResult(False, "reply must be a JSON object with keys ['count', 'verdict']", True)


def test_a_preamble_is_quoted_as_the_faulty_part():
    reply = 'Sure! Here is my verdict:\n{"verdict": "pass", "count": 2}'
    text = repair_request(reply, _not_json(reply), _OUTPUTS, "")
    assert "still not valid JSON" in text
    assert "The problem is here:\n<<<\nSure! Here is my verdict:\n>>>" in text
    assert f"Your whole reply was:\n<<<\n{reply}\n>>>" in text  # quoted, not "see above"
    assert example(_OUTPUTS) in text and "ONLY one JSON object" in text
    assert "Do not redo any work" in text


def test_a_parse_error_inside_the_object_is_quoted_around_its_position():
    reply = '{"verdict": "pass", "count": 2,, "extra": true}'
    text = repair_request(reply, _not_json(reply), _OUTPUTS, "")
    fragment = text.split("The problem is here:\n<<<\n")[1].split("\n>>>")[0]
    assert ",," in fragment


async def test_a_field_problem_names_the_field_and_the_value_it_got(wf: WorkflowHandle):
    fields = {"verdict": "pass", "count": "3"}
    verdict = await _outputs_check(_OUTPUTS)(wf, {"fields": fields})
    text = repair_request(json.dumps(fields), verdict, _OUTPUTS, "")
    assert "still does not match the required format" in text
    assert "'count'" in text and '"3"' in text


async def test_a_missing_field_says_which_fields_the_reply_had(wf: WorkflowHandle):
    verdict = await _outputs_check(_OUTPUTS)(wf, {"fields": {"verdict": "pass"}})
    assert "'count'" in verdict.reason and "verdict" in verdict.reason


async def test_a_yaml_reply_points_at_the_line_that_breaks(wf: WorkflowHandle):
    reply = "items:\n  - a\n  - [b\nnext: 1\n"
    await wf.write("/o.yaml", reply)
    verdict = await artifact_valid("/o.yaml", "yaml")(wf, None)
    text = repair_request(reply, verdict, None, "yaml")
    assert "still not valid YAML" in text and "ONLY the YAML content" in text
    fragment = text.split("The problem is here:\n<<<\n")[1].split("\n>>>")[0]
    assert fragment.strip() in reply.splitlines()


async def test_a_csv_reply_quotes_the_row_that_does_not_fit(wf: WorkflowHandle):
    reply = "Here you go:\na,b\n1,2\n3,4\n"
    await wf.write("/o.csv", reply)
    verdict = await artifact_valid("/o.csv", "csv")(wf, None)
    text = repair_request(reply, verdict, None, "csv")
    assert "The problem is here:\n<<<\nHere you go:\n>>>" in text


def test_a_long_reply_is_quoted_head_and_tail():
    reply = "x" * 10_000 + "MIDDLE" + "y" * 10_000
    text = repair_request(reply, _not_json(reply), _OUTPUTS, "")
    assert "MIDDLE" not in text and "characters left out" in text
    assert "x" * 8_000 in text and "y" * 8_000 in text


# ── §3.2: the engine repairs a format failure instead of re-running ──────────


def _check(*verdicts: CheckResult):
    queue = list(verdicts)

    async def check(_wf: WorkflowHandle, _result: Any) -> CheckResult:
        return queue.pop(0)

    return check


_FORMAT = CheckResult(False, "not json", format=True)
_CONTENT = CheckResult(False, "missing section")
_OK = CheckResult(True)


async def test_a_format_failure_is_repaired_not_re_run(wf: WorkflowHandle):
    calls: list[str] = []

    async def execute(feedback: str | None) -> str:
        calls.append(f"execute:{feedback}")
        return "r"

    async def repair(verdict: CheckResult) -> str:
        calls.append(f"repair:{verdict.reason}")
        return "r"

    await run_step(
        wf, name="s", args={}, execute=execute, repair=repair, check=_check(_FORMAT, _OK), retries=2
    )
    assert calls == ["execute:None", "repair:not json"]


async def test_when_repairs_run_out_the_step_fails_without_re_running(wf: WorkflowHandle):
    calls: list[str] = []

    async def execute(feedback: str | None) -> str:
        calls.append("execute")
        return "r"

    async def repair(verdict: CheckResult) -> str:
        calls.append("repair")
        return "r"

    with pytest.raises(StepFailed, match="not json"):
        await run_step(
            wf,
            name="s",
            args={},
            execute=execute,
            repair=repair,
            check=_check(_FORMAT, _FORMAT, _FORMAT),
            retries=2,
        )
    assert calls == ["execute", "repair", "repair"]


async def test_repairs_and_re_runs_share_one_budget(wf: WorkflowHandle):
    calls: list[str] = []

    async def execute(feedback: str | None) -> str:
        calls.append("execute")
        return "r"

    async def repair(verdict: CheckResult) -> str:
        calls.append("repair")
        return "r"

    with pytest.raises(StepFailed, match="not json"):
        await run_step(
            wf,
            name="s",
            args={},
            execute=execute,
            repair=repair,
            check=_check(_CONTENT, _FORMAT, _FORMAT),
            retries=2,
        )
    assert calls == ["execute", "execute", "repair"]


async def test_without_a_repair_a_format_failure_re_runs_as_before(wf: WorkflowHandle):
    feedbacks: list[str | None] = []

    async def execute(feedback: str | None) -> str:
        feedbacks.append(feedback)
        return "r"

    await run_step(wf, name="s", args={}, execute=execute, check=_check(_FORMAT, _OK), retries=1)
    assert feedbacks == [None, "not json"]


# ── §3.4 / D1: the agent step's repair turn ──────────────────────────────────


async def test_an_agent_step_repairs_with_no_tools_and_the_failed_reply(wf: WorkflowHandle):
    turns: list[tuple[str, list[str] | None]] = []
    replies = ["Sure! Here it is.", '{"verdict": "fail", "count": 4}']

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        turns.append((prompt, tools))
        return replies.pop(0)

    wf.drive_turn = drive_turn
    result = await agent_write_step(
        wf,
        prompt="judge it",
        phase="p",
        name="j",
        tools=["read_file"],
        outputs=_OUTPUTS,
        check=_outputs_check(_OUTPUTS),
        retries=2,
    )
    assert result["fields"] == {"verdict": "fail", "count": 4}
    (first, first_tools), (second, second_tools) = turns
    assert first_tools == ["read_file"] and second_tools == []
    assert "judge it" not in second  # a repair, not the task again
    assert "Sure! Here it is." in second and "still not valid JSON" in second


async def test_a_structured_out_is_rewritten_by_its_repair(wf: WorkflowHandle):
    replies = ['Here you go: {"a": 1}', '{"a": 1}']

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return replies.pop(0)

    wf.drive_turn = drive_turn
    await agent_write_step(wf, prompt="write it", phase="p", out="o.json", kind="json", retries=1)
    assert json.loads(await wf.read_text("o.json")) == {"a": 1}


async def test_a_missing_section_still_re_runs_the_step(wf: WorkflowHandle):
    prompts: list[str] = []
    replies = ["# Intro", "# Intro\n## Findings\nx"]

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        prompts.append(prompt)
        return replies.pop(0)

    wf.drive_turn = drive_turn
    await agent_write_step(
        wf,
        prompt="write the report",
        phase="p",
        out="r.md",
        kind="markdown",
        requires={"contains": ["## Findings"]},
        retries=1,
    )
    assert prompts[1].startswith("write the report")  # the task again, with feedback


async def test_a_csv_row_with_a_quoted_newline_does_not_shift_the_quote(wf: WorkflowHandle):
    reply = 'Here you go:\nname,note\nann,"two\nlines"\nbob,x\n'
    await wf.write("/o.csv", reply)
    verdict = await artifact_valid("/o.csv", "csv")(wf, None)
    text = repair_request(reply, verdict, None, "csv")
    assert "The problem is here:\n<<<\nHere you go:\n>>>" in text


def test_an_empty_reply_says_there_was_no_answer():
    text = repair_request("", _not_json(""), _OUTPUTS, "")
    assert "had no answer" in text and "The problem is here" not in text


# ── §3.5 / D7: the repair request is in the workflow chat, never in the history ──


def test_a_repair_request_is_recorded_in_the_workflow_chat():
    """Through the real app: the node misses its shape, is repaired, and the execution's
    record keeps what it was asked — the repair turn's prompt, under its own role, tagged
    with the step like the step's question; never sent to the model, and not the #624
    context notice."""
    import time

    from tests.api._client import TestClient
    from tests.workflow.test_agent_reply_contract import _ANSWER, _WF, _SequenceRunner
    from workspace_app.api import MessageDelta, RunDone, create_app
    from workspace_app.api.turns import REPAIR_REQUEST_ROLE, already_noticed, history_items
    from workspace_app.apps.playground.model import PlaygroundItem
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.resources import Conversation, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    runner = _SequenceRunner(
        [
            [MessageDelta(text="Sure! Here are the jobs."), RunDone()],
            [MessageDelta(text=_ANSWER), RunDone()],
            [MessageDelta(text="a note"), RunDone()],
        ]
    )
    spec = make_spec()
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    base = f"/a/playground/items/{item_id}"
    with TestClient(app) as client:
        assert client.put(f"{base}/files/.workflows/flow.json", content=_WF).status_code == 204
        run_id = client.post(f"{base}/run?workflow_id=flow").json()["run_id"]
        for _ in range(400):
            data = client.get(f"{base}/runs/{run_id}").json()
            if data["status"] == "done":
                break
            time.sleep(0.02)
        assert data["status"] == "done", data
    conv = spec.get_resource_manager(Conversation).get(data["chat_id"]).data
    asked = [m for m in conv.messages if m.role == REPAIR_REQUEST_ROLE]
    assert len(asked) == 1 and "still not valid JSON" in asked[0].content
    assert "Sure! Here are the jobs." in asked[0].content
    # recorded once, as the step's prompt for that turn — not also as a question
    assert not [m for m in conv.messages if m.role == "user" and "still not valid" in m.content]
    plan_q = [m for m in conv.messages if m.role == "user" and "plan the jobs" in m.content]
    assert asked[0].step is not None and asked[0].step == plan_q[0].step
    # it precedes the answer it asked for
    roles = [m.role for m in conv.messages]
    assert roles.index(REPAIR_REQUEST_ROLE) < len(roles) - 1
    assert not already_noticed(conv.messages)
    assert all("still not valid JSON" not in str(i.get("content")) for i in history_items(asked))


def test_a_steps_transcript_shows_a_repair_as_its_own_attempt():
    """The journal transcript (plan-workflow-out-of-chat D9) is what "Ask AI why it
    failed" reads: a repair is an attempt, and its request is not the step's question."""
    from workspace_app.api.turns import REPAIR_REQUEST_ROLE
    from workspace_app.api.workflow_transcript import render_transcript
    from workspace_app.resources import Message

    md = render_transcript(
        "plan",
        [
            Message(role="user", content="plan the jobs"),
            Message(role="assistant", content="Sure! Here are the jobs."),
            Message(role=REPAIR_REQUEST_ROLE, content="Your reply is still not valid JSON."),
            Message(role="assistant", content='{"job_count": 3}'),
        ],
    )
    assert "## Attempt 2 (repair)" in md and "### Repair request" in md
    assert md.index("still not valid JSON") > md.index("Sure! Here are the jobs.")


def test_a_csv_field_past_the_parser_limit_still_gets_a_repair_request():
    """`artifact_valid` reports such a reply as not valid CSV; building the request
    must not then raise from the same parser."""
    reply = 'a,b\n"' + "x" * 200_000 + "\n1,2\n"
    text = repair_request(
        reply, CheckResult(False, "file o.csv is not valid CSV", True), None, "csv"
    )
    assert "does not match the required format: file o.csv is not valid CSV" in text


@pytest.mark.parametrize(
    "reply",
    ["Here is the table:\na,b,c\n", "name,age\nHope this helps!", "a,b\nnote\n1,2\nbye\n"],
)
def test_a_csv_tie_points_at_nothing_rather_than_at_the_data(reply: str):
    """A one-cell sentence against a one-row table: which is the table cannot be told
    from widths alone (the sentence may come first or last). Pointing at the data would
    mislead; the request states the reason instead, and still quotes the whole reply."""
    text = repair_request(reply, CheckResult(False, "inconsistent columns", True), None, "csv")
    assert "The problem is here" not in text
    assert "does not match the required format: inconsistent columns" in text
    assert reply.strip() in text


async def test_an_authors_check_of_another_file_is_not_repaired(wf: WorkflowHandle):
    """Only the reply's own format can be rewritten by a no-tools turn. An author's
    `artifact_valid` on a file the agent wrote with its tools must re-run the step."""
    held: list[list[str] | None] = []
    writes = iter(["{", "{}"])

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        held.append(tools)
        if tools:
            await wf.write("side.json", next(writes))
        return '{"a": 1}'

    wf.drive_turn = drive_turn
    await agent_write_step(
        wf,
        prompt="write both",
        phase="p",
        out="report.json",
        kind="json",
        tools=["write_file"],
        check=artifact_valid("side.json", "json"),
        retries=1,
    )
    assert held == [["write_file"], ["write_file"]]


async def test_a_missing_field_reason_lists_a_bounded_number_of_fields(wf: WorkflowHandle):
    fields = {f"k{i}": i for i in range(500)}
    verdict = await _outputs_check({"want": "str"})(wf, {"fields": fields})
    assert len(verdict.reason) < 400 and "more" in verdict.reason
