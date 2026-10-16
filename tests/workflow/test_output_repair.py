"""A workflow agent's malformed output is repaired, not the step re-run
(docs/plan-workflow-output-repair.md)."""

import json
from typing import Any

import pytest

from workspace_app.workflow.dsl import _outputs_check
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.output_format import example, instruction
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
