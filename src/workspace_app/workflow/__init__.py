"""Workflows (#100) — API-triggered headless multi-step procedures.

See ``docs/workflows.md`` (the manual / spec) and ``docs/plan-workflows.md``.
This package holds the platform machinery: the ``WorkflowRun`` resource, the
filesystem-as-journal execution engine, and the step library. *How* any one
workflow behaves lives in a profile's ``run.py`` (``apps/<slug>/profiles/...``),
not here.
"""

from __future__ import annotations

from .run import Failure, PhaseState, RunStatus, WorkflowRun

__all__ = ["Failure", "PhaseState", "RunStatus", "WorkflowRun"]
