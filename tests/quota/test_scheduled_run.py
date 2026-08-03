"""P9 — a scheduled headless run refused for quota leaves a VISIBLE record.

A scheduled run is refused like any other (an exemption would be a way to spend
resources you do not have, just by scheduling it). The thing that makes that
acceptable rather than dangerous is that the refusal is discoverable the next
morning: a terminal `error` run carrying a reason a person can read, not a
silent no-op.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.quota.admission import SandboxQuotaExceeded
from workspace_app.resources import make_spec
from workspace_app.workflow.driver import run_workflow
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.run import RunStatus, WorkflowRun


async def test_a_refused_scheduled_run_ends_visible_and_says_why():
    spec: SpecStar = make_spec()
    run_id = (
        spec.get_resource_manager(WorkflowRun)
        .create(WorkflowRun(item_id="rca/a/1", captured_user="alice"))
        .resource_id
    )

    async def _refused(_wf, _inputs):
        raise SandboxQuotaExceeded("alice", "sandboxes", used=3, limit=2)

    await run_workflow(
        spec=spec,
        run_id=run_id,
        profile_run=_refused,
        wf=WorkflowHandle(store=MemoryFileStore(), workspace_id="ws"),
        inputs=None,
    )

    data = spec.get_resource_manager(WorkflowRun).get(run_id).data
    assert isinstance(data, WorkflowRun)
    # Terminal + discoverable — NOT a silent skip, which is the failure mode that
    # makes scheduled work untrustworthy.
    assert data.status is RunStatus.ERROR
    assert data.ended
    assert data.result is not None  # a terminal run always carries one
    reason = data.result["error"]
    # The message has to be readable by whoever finds it tomorrow: who was over,
    # on what, and by how much.
    assert "alice" in reason
    assert "sandboxes" in reason
    assert "2" in reason


@pytest.mark.parametrize("dimension", ["sandboxes", "cpu", "memory"])
def test_the_refusal_message_names_the_dimension(dimension: str):
    """Whichever dimension bound, the person is told which — "you are at your
    limit" without saying which limit gives them nothing to act on."""
    exc = SandboxQuotaExceeded("alice", dimension, used=5, limit=4)
    assert dimension in str(exc)
    assert "alice" in str(exc)
