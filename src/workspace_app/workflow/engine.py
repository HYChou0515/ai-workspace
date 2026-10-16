"""The filesystem-as-journal step engine (#100, manual §9).

``run_step`` is the one place that decides **run vs skip**: it computes a step's
input-hash from its arguments, and if the step's artifact already exists with a
matching hash it returns the cached result without re-executing (no LLM, no sandbox,
no chat re-post). Otherwise it executes — with retry-with-feedback on a failing gate
— and records ``{hash, result}`` to ``<wf.journal_dir>/step_<name>/<key>.json`` — the
run's per-workflow journal folder (#136), not the workspace root.

This module is deliberately decoupled from *how* a step does its work: the caller
passes an ``execute`` coroutine (an agent turn, a sandbox command, a capability
call) and an optional ``check`` gate. ``agent_step`` / ``sandbox_node`` are thin
adapters built on top (later phases).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NoReturn

from msgspec import Struct

from .events import StepFailed as StepFailedEv
from .events import StepPassed, StepRetrying, StepSkipped, StepStarted

if TYPE_CHECKING:
    from .handle import WorkflowHandle

logger = logging.getLogger(__name__)


class StepFailed(Exception):
    """A step (or loop element) aborted — its gate failed after all retries, or
    ``fail()`` was called. In a ``for``-each the author catches this to skip+collect
    the element (manual §11); at top level it aborts the run."""


def fail(reason: str) -> NoReturn:
    """Abort the current step/element with ``reason`` (raises ``StepFailed``)."""
    raise StepFailed(reason)


class CheckResult(Struct):
    """A gate verdict: ``ok`` plus, on failure, a ``reason`` that is fed back into
    the step's retry (manual §6). ``format`` marks a failure of the reply's own
    format — not JSON, a field missing or mistyped, a structured artifact that does
    not parse — which a repair turn can fix without redoing the step's work
    (docs/plan-workflow-output-repair.md §3.1). Set by the check that made the
    verdict, never inferred from the reason's wording."""

    ok: bool
    reason: str = ""
    format: bool = False


# A gate: given the handle + the step's result, verify the postcondition.
Check = Callable[["WorkflowHandle", Any], Awaitable[CheckResult]]
# The step body: receives the previous attempt's failure feedback (None first try),
# returns the step's result (whatever should be journaled + handed downstream).
Execute = Callable[[str | None], Awaitable[Any]]
# A repair: given the verdict on a reply whose own FORMAT failed, have it re-emitted
# without redoing the step's work, and return the step's result as `Execute` does
# (docs/plan-workflow-output-repair.md §3.2).
Repair = Callable[[CheckResult], Awaitable[Any]]


def input_hash(args: Any) -> str:
    """A step's cache key = a stable hash of its arguments (manual §3 convention:
    inputs are passed as args, so this captures everything that should invalidate
    the cache — edit an upstream artifact → its content changes the arg → re-run)."""
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def journal_stem(journal_dir: str, name: str, key: str) -> str:
    """Where a step's files live in the journal, minus the extension: its
    artifact is ``<stem>.json``, its transcript ``<stem>.transcript.md``. One
    rule for both, so the transcript is always beside the artifact it explains."""
    return f"{journal_dir}/step_{name}/{key or 'main'}"


def transcript_path(journal_dir: str, name: str, key: str) -> str:
    return f"{journal_stem(journal_dir, name, key)}.transcript.md"


def _artifact_path(wf: WorkflowHandle, name: str, key: str) -> str:
    return f"{journal_stem(wf.journal_dir, name, key)}.json"


def _emit(wf: WorkflowHandle, event: object) -> None:
    """Publish a phase/step observability event (manual §12) if the handle wired
    an ``emit`` hook; a no-op otherwise (events are best-effort)."""
    if wf.emit is not None:
        wf.emit(event)


async def run_step(
    wf: WorkflowHandle,
    *,
    name: str,
    key: str = "",
    phase: str = "",
    args: Any,
    execute: Execute,
    check: Check | None = None,
    retries: int = 0,
    cache: bool = True,
    repair: Repair | None = None,
) -> Any:
    """Run one journaled step. Skips (returns the cached result) when its artifact
    exists with a matching input-hash and ``cache`` is set; otherwise executes,
    retrying on a failing gate up to ``retries`` times (feeding the failure reason
    back each time), then journals ``{hash, result}``. Raises ``StepFailed`` if the
    gate never passes. Emits StepSkipped / StepStarted / StepRetrying / StepPassed /
    StepFailed through the handle's ``emit`` hook (manual §12).

    With a ``repair``, a verdict that says the reply's own format failed
    (``CheckResult.format``) is followed by ``repair(verdict)`` instead of a re-run.
    Repairs and re-runs spend the same ``retries``; when they run out the step fails
    with the last reason — a repair that never takes is not followed by a re-run
    (docs/plan-workflow-output-repair.md D2, D6)."""
    path = _artifact_path(wf, name, key)
    h = input_hash(args)

    if cache and await wf.exists(path):
        record = await wf.read_json(path)
        if isinstance(record, dict) and record.get("hash") == h:
            logger.debug("step: skip %s/%s (cached, hash match)", name, key)
            _emit(wf, StepSkipped(phase=phase, name=name, key=key))
            return record.get("result")  # SKIP — cached, identical inputs

    _emit(wf, StepStarted(phase=phase, name=name, key=key))
    logger.info("step: start %s/%s (phase %s)", name, key, phase)
    feedback: str | None = None
    reason = ""
    # the verdict a repair answers, when the last attempt's format failed
    to_repair: CheckResult | None = None
    for attempt in range(retries + 1):
        try:
            result = await (
                repair(to_repair)
                if repair is not None and to_repair is not None
                else execute(feedback)
            )
        except StepFailed as exc:
            # The step could not RUN, as distinct from running and failing its
            # gate. No retry — nothing about the input changed — and no journal,
            # so a transient failure cannot record the node as done forever. But
            # it is emitted like any other step failure: without this the step
            # simply vanished from the live stream (measured: the parent left a
            # permanently-"running" card on the board), which is worse than the
            # gate-failure path it sits beside.
            #
            # ⚠️ Known, pre-existing, and widened by one path here: a `_emit`
            # hook that RAISES escapes as its own exception rather than as
            # `StepFailed`, so `wf.map`'s `except StepFailed` cannot tolerate
            # the element and its `gather` (no `return_exceptions`) leaves the
            # siblings detached. The gate-failure path below has had this since
            # it was written. The honest fix is to suppress inside `_emit` for
            # BOTH — but `_on_event` also PERSISTS the run's state, so making it
            # swallow is a decision about run bookkeeping, not observability,
            # and it does not belong in a change about python environments.
            _emit(wf, StepFailedEv(phase=phase, name=name, reason=str(exc), key=key))
            logger.warning("step: fail %s/%s (could not run): %s", name, key, exc)
            raise
        verdict = await check(wf, result) if check is not None else CheckResult(True)
        if verdict.ok:
            # The step's effects are already done; this only records that. See
            # `write_record_json` — it is not refused for space, because losing
            # the record makes the re-run repeat those effects (#538).
            await wf.write_record_json(path, {"hash": h, "result": result})
            _emit(wf, StepPassed(phase=phase, name=name, key=key))
            logger.info("step: pass %s/%s", name, key)
            return result
        reason = verdict.reason
        feedback = verdict.reason
        to_repair = verdict if verdict.format else None
        if attempt < retries:
            logger.warning("step: retry %s/%s after failed gate: %s", name, key, reason)
            _emit(wf, StepRetrying(phase=phase, name=name, reason=reason, key=key))

    _emit(wf, StepFailedEv(phase=phase, name=name, reason=reason, key=key))
    logger.warning("step: fail %s/%s (gate never passed): %s", name, key, reason)
    raise StepFailed(reason or f"step {name!r} did not pass its gate")
