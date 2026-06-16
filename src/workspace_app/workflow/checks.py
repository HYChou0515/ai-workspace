"""Built-in deterministic gates (#100, manual §6).

Gates are postconditions on a step's result, verified mechanically wherever
possible (a hard guarantee, vs an LLM judging an LLM). Each builder returns a
``Check`` — a coroutine ``(wf, result) -> CheckResult``. The file-based gates here
only need the workspace; capability gates (``collection_has``) and sandbox
predicates (``exec``) arrive with their phases.
"""

from __future__ import annotations

from typing import Any

from ..filestore.protocol import FileNotFound
from .engine import Check, CheckResult
from .handle import WorkflowHandle


def file_nonempty(path: str) -> Check:
    """The agent actually wrote ``path`` and it has content."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        try:
            data = await wf.read(path)
        except FileNotFound:
            return CheckResult(False, f"expected file {path} was not written")
        if not data.strip():
            return CheckResult(False, f"file {path} is empty")
        return CheckResult(True)

    return _check


def choice_in(path: str, *, key: str, allowed: list[Any]) -> Check:
    """The decision recorded at ``path[key]`` is within the allowed set (manual §8:
    clamp the agent's choice deterministically — the prompt may suggest, the gate
    enforces). On a bad pick the reason is fed back so the agent re-picks."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        try:
            obj = await wf.read_json(path)
        except FileNotFound:
            return CheckResult(False, f"expected file {path} was not written")
        value = obj.get(key) if isinstance(obj, dict) else None
        if value not in allowed:
            return CheckResult(False, f"{key}={value!r} is not one of {allowed}")
        return CheckResult(True)

    return _check
