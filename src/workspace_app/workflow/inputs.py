"""Resolve a run's ``input.json`` (manual §14) — shared by the orchestrator (what the
run actually reads) and the #283 pre-flight preview (what the launch dialog inspects),
so the preview can never describe a different input than the run will use.
"""

from __future__ import annotations

from typing import Any

from .handle import WorkflowHandle
from .manifest import WorkflowManifest


async def resolve_inputs(wf: WorkflowHandle, manifest: WorkflowManifest) -> Any:
    """Parsed ``input.json`` — ``{}`` when the file is absent so a no-input workflow just
    runs. The location is the manifest's ``input_json`` if pinned, else
    ``{upload_dir}/input.json`` (#198) so the control file sits in the same staging folder
    a chat attach lands in."""
    path = manifest.input_json or f"{wf.upload_dir.rstrip('/')}/input.json"
    if await wf.exists(path):
        return await wf.read_json(path)
    return {}
