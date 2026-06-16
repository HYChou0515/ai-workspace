"""The filesystem-as-journal step engine (#100, manual §9).

``run_step`` is the one place that decides **run vs skip**: it computes a step's
input-hash from its arguments, and if the step's artifact already exists with a
matching hash it returns the cached result without re-executing (no LLM, no sandbox,
no chat re-post). Otherwise it executes — with retry-with-feedback on a failing gate
— and records ``{hash, result}`` to ``step_<name>/<key>.json``.

This module is deliberately decoupled from *how* a step does its work: the caller
passes an ``execute`` coroutine (an agent turn, a sandbox command, a capability
call) and an optional ``check`` gate. ``agent_step`` / ``sandbox_node`` are thin
adapters built on top (later phases).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NoReturn

from msgspec import Struct

if TYPE_CHECKING:
    from .handle import WorkflowHandle


class StepFailed(Exception):
    """A step (or loop element) aborted — its gate failed after all retries, or
    ``fail()`` was called. In a ``for``-each the author catches this to skip+collect
    the element (manual §11); at top level it aborts the run."""


def fail(reason: str) -> NoReturn:
    """Abort the current step/element with ``reason`` (raises ``StepFailed``)."""
    raise StepFailed(reason)


class CheckResult(Struct):
    """A gate verdict: ``ok`` plus, on failure, a ``reason`` that is fed back into
    the step's retry (manual §6)."""

    ok: bool
    reason: str = ""


# A gate: given the handle + the step's result, verify the postcondition.
Check = Callable[["WorkflowHandle", Any], Awaitable[CheckResult]]
# The step body: receives the previous attempt's failure feedback (None first try),
# returns the step's result (whatever should be journaled + handed downstream).
Execute = Callable[[str | None], Awaitable[Any]]


def input_hash(args: Any) -> str:
    """A step's cache key = a stable hash of its arguments (manual §3 convention:
    inputs are passed as args, so this captures everything that should invalidate
    the cache — edit an upstream artifact → its content changes the arg → re-run)."""
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def _artifact_path(name: str, key: str) -> str:
    return f"/step_{name}/{key or 'main'}.json"


async def run_step(
    wf: WorkflowHandle,
    *,
    name: str,
    key: str = "",
    args: Any,
    execute: Execute,
    check: Check | None = None,
    retries: int = 0,
    cache: bool = True,
) -> Any:
    """Run one journaled step. Skips (returns the cached result) when its artifact
    exists with a matching input-hash and ``cache`` is set; otherwise executes,
    retrying on a failing gate up to ``retries`` times (feeding the failure reason
    back each time), then journals ``{hash, result}``. Raises ``StepFailed`` if the
    gate never passes."""
    path = _artifact_path(name, key)
    h = input_hash(args)

    if cache and await wf.exists(path):
        record = await wf.read_json(path)
        if isinstance(record, dict) and record.get("hash") == h:
            return record.get("result")  # SKIP — cached, identical inputs

    feedback: str | None = None
    reason = ""
    for _ in range(retries + 1):
        result = await execute(feedback)
        verdict = await check(wf, result) if check is not None else CheckResult(True)
        if verdict.ok:
            await wf.write_json(path, {"hash": h, "result": result})
            return result
        reason = verdict.reason
        feedback = verdict.reason

    raise StepFailed(reason or f"step {name!r} did not pass its gate")
