"""The orchestration driver (#100, manual §13) — owns the ``WorkflowRun`` status
lifecycle while a profile's ``run()`` executes.

The filesystem journal (``run_step``) owns step skip/resume; this owns the run's
*status*: it marks the run ``running``, calls ``run(wf, inputs)``, and persists the
terminal status + result/error. Decoupled from *how* steps work (the ``wf`` handle
already carries the turn/sandbox drivers), so the lifecycle is unit-tested with a
fake ``run()``. The Run endpoint creates the ``WorkflowRun`` (capturing the user)
and calls this; per-phase progress + events arrive in a later phase.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import msgspec
from specstar import SpecStar

from .gate import AwaitingHuman
from .handle import WorkflowHandle
from .run import PendingDecision, RunStatus, WorkflowRun

ProfileRun = Callable[[WorkflowHandle, Any], Awaitable[Any]]


def _now_ms() -> int:
    return int(time.time() * 1000)


async def run_workflow(
    spec: SpecStar,
    *,
    run_id: str,
    profile_run: ProfileRun,
    wf: WorkflowHandle,
    inputs: Any,
    now: Callable[[], int] = _now_ms,
) -> None:
    """Drive one workflow run to a terminal ``WorkflowRun`` status. ``running`` is
    set before ``run()`` executes; on return the result is persisted as ``done``; on
    an exception, ``error`` (with the message); on cancel (Stop, §10), ``cancelled``
    is recorded and the cancellation re-raised. The run's actual work + step
    skip/resume is the journal engine's job (manual §9)."""
    rm = spec.get_resource_manager(WorkflowRun)

    def _patch(**changes: Any) -> None:
        current = rm.get(run_id).data
        rm.update(run_id, msgspec.structs.replace(current, **changes))

    _patch(status=RunStatus.RUNNING, started=now())
    try:
        result = await profile_run(wf, inputs)
    except AwaitingHuman as gate:
        # Suspend: record the open decision; the run task exits. A human responds
        # via the decisions endpoint, then re-running resumes (manual §10).
        _patch(
            status=RunStatus.AWAITING_HUMAN,
            pending_decision=PendingDecision(
                phase=gate.phase, title=gate.title, summary=gate.summary, allow=gate.allow
            ),
        )
        return
    except asyncio.CancelledError:
        _patch(status=RunStatus.CANCELLED, ended=now())
        raise
    except Exception as exc:  # noqa: BLE001 — surface any run() failure as a terminal status
        _patch(
            status=RunStatus.ERROR,
            ended=now(),
            result={"error": f"{type(exc).__name__}: {exc}"},
        )
        return
    _patch(
        status=RunStatus.DONE,
        ended=now(),
        result=result if isinstance(result, dict) else {"result": result},
    )
