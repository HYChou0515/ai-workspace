"""``human_gate`` — produce → review → commit (#100, manual §10).

The decision *is* an artifact (``step_<phase>/decision.json``), so the gate fits the
filesystem-journal exactly: on first reach there is no decision, so the gate raises
``AwaitingHuman`` and the driver suspends the run (status ``awaiting_human``, sandbox
released). A human responds via the decisions endpoint (``record_decision`` writes
the artifact); re-running the workflow replays completed steps, reaches the gate
again, finds the decision, and continues. No journal/replay machinery beyond §9.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from msgspec import Struct

if TYPE_CHECKING:
    from .handle import WorkflowHandle


class Decision(Struct):
    """A human's answer at a gate. ``choice`` ∈ the gate's ``allow`` (e.g.
    approve/reject); ``input`` carries an optional revision/feedback (manual §10)."""

    choice: str
    input: str = ""


class AwaitingHuman(Exception):
    """Raised by ``human_gate`` when no decision has been recorded yet — the driver
    catches it, marks the run ``awaiting_human`` with the pending decision, and
    stops (manual §10)."""

    def __init__(self, *, phase: str, title: str, summary: str, allow: list[str]) -> None:
        self.phase = phase
        self.title = title
        self.summary = summary
        self.allow = allow
        super().__init__(f"awaiting human decision at phase {phase!r}")


def _decision_path(phase: str) -> str:
    return f"/step_{phase}/decision.json"


def _as_text(summary: Any) -> str:
    return summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False)


async def human_gate(
    wf: WorkflowHandle,
    *,
    phase: str,
    title: str,
    summary: Any = "",
    allow: tuple[str, ...] | list[str] = ("approve", "reject"),
) -> Decision:
    """Pause for a human decision. Returns the recorded ``Decision`` once one exists;
    otherwise raises ``AwaitingHuman`` (the run suspends). ``summary`` is what the
    human reviews — a string, or any JSON-able value (e.g. a routing plan)."""
    path = _decision_path(phase)
    if await wf.exists(path):
        rec = await wf.read_json(path)
        return Decision(choice=rec["choice"], input=rec.get("input", ""))
    raise AwaitingHuman(phase=phase, title=title, summary=_as_text(summary), allow=list(allow))


async def record_decision(
    wf: WorkflowHandle, *, phase: str, choice: str, input: str = "", decided_by: str = ""
) -> None:
    """Write the decision artifact (the decisions endpoint calls this); re-running
    the workflow then finds it at the gate and continues (manual §10). ``decided_by``
    is recorded for audit (manual §15) — ``human_gate`` itself reads only choice/input."""
    await wf.write_json(
        _decision_path(phase), {"choice": choice, "input": input, "decided_by": decided_by}
    )
