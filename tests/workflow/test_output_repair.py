"""A workflow agent's malformed output is repaired, not the step re-run
(docs/plan-workflow-output-repair.md)."""

from typing import Any

import pytest

from workspace_app.workflow.dsl import _outputs_check
from workspace_app.workflow.handle import WorkflowHandle

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
