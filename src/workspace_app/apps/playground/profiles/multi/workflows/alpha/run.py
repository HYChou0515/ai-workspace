"""Alpha — a trivial workflow for the multi-workflow-per-profile fixture (#100,
manual §4). Lives at ``profiles/multi/workflows/alpha/run.py``: discovery resolves
each declared workflow's ``run.py`` by file path under ``workflows/<id>/``."""

from __future__ import annotations

from typing import Any

from workspace_app.workflow.handle import WorkflowHandle


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    await wf.write_json("out/alpha.json", {"ok": True})
    return {"status": "done", "workflow": "alpha"}
