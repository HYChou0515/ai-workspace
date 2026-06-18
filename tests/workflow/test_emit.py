"""The step engine emits phase/step observability events (#100, manual §12)
through the handle's ``emit`` hook — StepStarted/Passed/Failed/Skipped/Retrying.
Emission is purely additive: with no ``emit`` wired the engine behaves exactly as
the §9 tests assert."""

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.checks import file_nonempty
from workspace_app.workflow.engine import StepFailed, run_step
from workspace_app.workflow.events import (
    StepFailed as StepFailedEv,
)
from workspace_app.workflow.events import (
    StepPassed,
    StepRetrying,
    StepSkipped,
    StepStarted,
)
from workspace_app.workflow.handle import WorkflowHandle


def _wf(events: list) -> WorkflowHandle:
    return WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", emit=events.append)


async def test_run_then_skip_emits_started_passed_then_skipped():
    events: list = []
    wf = _wf(events)

    async def execute(_fb):
        return {"n": 1}

    await run_step(wf, name="s", phase="p", args={"a": 1}, execute=execute)
    await run_step(wf, name="s", phase="p", args={"a": 1}, execute=execute)  # skip
    assert events == [
        StepStarted(phase="p", name="s"),
        StepPassed(phase="p", name="s"),
        StepSkipped(phase="p", name="s"),
    ]


async def test_failing_gate_emits_retrying_then_failed():
    events: list = []
    wf = _wf(events)

    async def execute(_fb):
        await wf.write("/out.txt", "")  # empty → gate fails every attempt
        return {}

    with pytest.raises(StepFailed):
        await run_step(
            wf,
            name="s",
            phase="p",
            args={},
            execute=execute,
            check=file_nonempty("/out.txt"),
            retries=1,
        )
    # started, one retry (a second attempt remains), then failed
    assert events[0] == StepStarted(phase="p", name="s")
    assert any(isinstance(e, StepRetrying) for e in events)
    assert isinstance(events[-1], StepFailedEv)
    assert events[-1].phase == "p" and events[-1].name == "s"


async def test_no_emit_hook_is_a_noop():
    """Without an ``emit`` wired the engine still runs (events are best-effort)."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")

    async def execute(_fb):
        return {"ok": True}

    assert await run_step(wf, name="s", phase="p", args={}, execute=execute) == {"ok": True}
