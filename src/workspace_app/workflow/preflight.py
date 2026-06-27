"""Pre-flight preview (#283, manual §18) — what the launch dialog shows BEFORE a run.

The operator's biggest UX trap is launching a workflow blind: not knowing which one
does what, not realising it needs files staged first, and so triggering a 0ms no-op
(#283 抱怨 1). The fix is a pre-flight: an author-written ``preflight(wf, inputs)``
that (a) verifies the run's preconditions as a checklist and (b) describes, in human
words, what the run is about to do — surfaced in a confirm dialog before anything runs.

Pre-flight is a *precondition* check, distinct from a step gate (``checks.py``, a
*postcondition* on a step's result). A failing REQUIRED item blocks the run (it would
no-op or error anyway); an ADVISORY item is a warning the operator may proceed past.
The author may still call the gate ``check`` builders inside ``preflight`` and wrap
their verdicts into items — the philosophy (mechanical verification) is shared.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from msgspec import Struct, field

if TYPE_CHECKING:
    from .handle import WorkflowHandle


class Severity(StrEnum):
    """How hard a failing pre-flight item is. ``required`` blocks the run (a missing
    precondition); ``advisory`` only warns (the operator may proceed)."""

    REQUIRED = "required"
    ADVISORY = "advisory"


class PreflightItem(Struct):
    """One checklist line in the launch dialog: a human ``label``, whether it ``ok``,
    its ``severity`` (default ``required`` — the safe default, an unmarked failure
    blocks), and a ``reason`` shown when it fails (and how to fix it)."""

    label: str
    ok: bool
    severity: Severity = Severity.REQUIRED
    reason: str = ""


class PreflightReport(Struct):
    """The author's pre-flight verdict: a human-readable ``summary`` of what the run
    will do (e.g. "ingest 3 files from uploads/ into a, b, c") plus a ``checks``
    list of preconditions. ``preflight(wf, inputs)`` returns one of these."""

    summary: str = ""
    checks: list[PreflightItem] = field(default_factory=list)


# An author's pre-flight hook: given the handle + parsed inputs, describe + verify the
# run before it starts. Optional — a workflow without one just shows its phases.
Preflight = Callable[["WorkflowHandle", Any], Awaitable[PreflightReport]]


def can_run(report: PreflightReport) -> bool:
    """Whether the launch dialog should allow 'Run': every REQUIRED check passed.
    Advisory failures never block (the operator decides)."""
    return all(c.ok for c in report.checks if c.severity is Severity.REQUIRED)
