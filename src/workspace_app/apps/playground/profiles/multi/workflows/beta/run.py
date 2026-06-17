"""Beta — the second workflow of the ``multi`` fixture profile (#100, manual §4)."""

from __future__ import annotations

from typing import Any

from workspace_app.workflow.handle import WorkflowHandle


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"status": "done", "workflow": "beta"}
