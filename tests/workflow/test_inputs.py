"""``resolve_inputs`` (#283 / manual §14) — the one place a run's ``input.json`` is
located + parsed, shared by the orchestrator and the pre-flight preview so they never
disagree about which file the run reads."""

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.inputs import resolve_inputs
from workspace_app.workflow.manifest import WorkflowManifest


def _wf(upload_dir: str = "uploads") -> WorkflowHandle:
    return WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", upload_dir=upload_dir)


async def test_reads_input_json_from_the_upload_dir_by_default():
    wf = _wf()
    await wf.write("uploads/input.json", b'{"n": 3}')
    assert await resolve_inputs(wf, WorkflowManifest()) == {"n": 3}


async def test_reads_a_pinned_input_json_location():
    wf = _wf()
    await wf.write("control/in.json", b'{"k": "v"}')
    assert await resolve_inputs(wf, WorkflowManifest(input_json="control/in.json")) == {"k": "v"}


async def test_missing_input_json_is_empty():
    assert await resolve_inputs(_wf(), WorkflowManifest()) == {}
