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
