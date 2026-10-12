"""What of a conversation belongs to a workflow run (plan-workflow-out-of-chat P3).

A chat turn never reads a run (D1 / D3). A message a run persists carries
`Message.run_id`. A chat taken over under #343 also holds run messages written
before that mark existed; those are placed by the run's own start and end,
which `WorkflowRun` records per chat (`chat_id`).
"""

from __future__ import annotations

from typing import NamedTuple

from specstar import QB, SpecStar

from ..resources import Message
from ..workflow.run import WorkflowRun


class RunWindow(NamedTuple):
    """When a run drove a chat, epoch ms; ``ended`` is None while it still runs."""

    started: int
    ended: int | None


def _inside(at: int, windows: list[RunWindow]) -> bool:
    return any(w.started <= at and (w.ended is None or at <= w.ended) for w in windows)


def without_runs(messages: list[Message], windows: list[RunWindow]) -> list[Message]:
    """``messages`` less every one a run produced: those marked with a run, and,
    in a chat a run took over before marks existed, the unmarked ones inside a
    run's window. A message with no time cannot be placed, and is kept."""
    return [
        m
        for m in messages
        if m.run_id is None and (m.created_at is None or not _inside(m.created_at, windows))
    ]


def run_windows_of_chat(spec: SpecStar, item_id: str, chat_id: str) -> list[RunWindow]:
    """The windows of every run that drove ``chat_id``. Scoped to the item by its
    indexed ``item_id``, then filtered by chat, as `active_run_for_chat` does."""
    rm = spec.get_resource_manager(WorkflowRun)
    out: list[RunWindow] = []
    for r in rm.list_resources((QB["item_id"] == item_id).build()):
        run = r.data
        assert isinstance(run, WorkflowRun)
        if run.chat_id == chat_id and run.started is not None:
            out.append(RunWindow(started=run.started, ended=run.ended))
    return out
