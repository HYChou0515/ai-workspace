"""#178 — a deterministic step's stdout rides the per-item stream live as a
``step_output`` event, so a long sandbox command shows movement instead of looking
dead. The event is folded into ``AgentEvent`` and serializes on the SSE wire."""

import json

from workspace_app.api.events import to_sse
from workspace_app.workflow.events import StepOutput, WorkflowEvent
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.steps import sandbox_node


def test_step_output_serializes_onto_the_sse_wire():
    frame = to_sse(StepOutput(phase="commit", name="ingest", text="line 1\n", key="f.pdf"))
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame[len("data: ") :].strip())
    assert payload == {
        "type": "step_output",
        "phase": "commit",
        "name": "ingest",
        "text": "line 1\n",
        "key": "f.pdf",
    }


def test_step_output_is_a_workflow_event():
    assert StepOutput in WorkflowEvent.__args__  # type: ignore[attr-defined]


async def test_sandbox_node_streams_stdout_as_step_output(wf: WorkflowHandle):
    """A deterministic step's stdout is emitted chunk-by-chunk as StepOutput,
    tagged with the step's identity, while the command runs (#178)."""
    events: list = []
    wf.emit = events.append

    async def run_sandbox(cmd: str, on_output) -> tuple[int, str]:
        on_output(b"line 1\n")
        on_output(b"line 2\n")
        return (0, "line 1\nline 2\n")

    wf.run_sandbox = run_sandbox
    out = await sandbox_node(wf, run="script.sh", phase="build", name="compile")
    assert out == {"exit_code": 0, "stdout": "line 1\nline 2\n"}
    assert [e for e in events if isinstance(e, StepOutput)] == [
        StepOutput(phase="build", name="compile", text="line 1\n"),
        StepOutput(phase="build", name="compile", text="line 2\n"),
    ]
