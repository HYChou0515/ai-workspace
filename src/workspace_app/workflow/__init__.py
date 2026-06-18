"""Workflows (#100) — API-triggered headless multi-step procedures.

See ``docs/workflows.md`` (the manual / spec) and ``docs/plan-workflows.md``.
This package holds the platform machinery: the ``WorkflowRun`` resource, the
filesystem-as-journal execution engine, the step library, and (later) the Run
endpoint + orchestration driver. *How* any one workflow behaves lives in a
profile's ``run.py`` (``apps/<slug>/profiles/...``), not here.

The author-facing surface is ``agent_step`` / ``sandbox_node`` / ``check`` /
``human_gate`` over a ``WorkflowHandle`` (``wf``); ``run_step`` is the engine
primitive both adapters build on.
"""

from __future__ import annotations

from .checks import choice_in, collection_has, file_nonempty
from .engine import CheckResult, StepFailed, fail, run_step
from .gate import AwaitingHuman, Decision, human_gate, record_decision
from .handle import WorkflowHandle
from .manifest import WorkflowManifest, WorkflowPhase
from .run import Failure, PendingDecision, PhaseState, RunStatus, WorkflowRun
from .steps import agent_step, agent_write_step, sandbox_node

__all__ = [
    "AwaitingHuman",
    "CheckResult",
    "Decision",
    "Failure",
    "PendingDecision",
    "PhaseState",
    "RunStatus",
    "StepFailed",
    "WorkflowHandle",
    "WorkflowManifest",
    "WorkflowPhase",
    "WorkflowRun",
    "agent_step",
    "agent_write_step",
    "choice_in",
    "collection_has",
    "fail",
    "file_nonempty",
    "human_gate",
    "record_decision",
    "run_step",
    "sandbox_node",
]
