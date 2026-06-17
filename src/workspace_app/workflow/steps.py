"""The node adapters (#100, manual §5) — thin wrappers over ``run_step``.

``agent_step`` and ``sandbox_node`` are *adapters*: they build the right ``execute``
coroutine (drive a ChatTurnEngine turn / run a sandbox command) and hand it to the
journal engine (``run_step``), which owns run-vs-skip + retry + journaling (manual
§9). They are deliberately invoker-distinguished (manual §7): an agent node is
LLM-driven and **must** be gated; a deterministic node is author code with no LLM.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .engine import Check, StepFailed, run_step
from .handle import WorkflowHandle


def _retry_prompt(prompt: str, feedback: str | None) -> str:
    if not feedback:
        return prompt
    return (
        f"{prompt}\n\nYour previous attempt did not pass its check: {feedback}\n"
        "Fix it and try again."
    )


async def agent_step(
    wf: WorkflowHandle,
    *,
    prompt: str,
    phase: str,
    check: Check,
    name: str | None = None,
    key: str = "",
    tools: list[str] | None = None,
    retries: int = 0,
    cache: bool = True,
) -> Any:
    """Run one agent node — an LLM turn on the item, gated. ``check`` is a required
    argument: an agent node without a gate is not expressible (manual §5.1). The
    step's identity is ``name`` (defaults to ``phase``) + ``key`` (the loop element);
    its input-hash covers the prompt + tools, so editing either re-runs it (§9)."""
    if wf.drive_turn is None:
        raise RuntimeError("agent_step needs a turn driver (wired by the run driver)")
    drive_turn = wf.drive_turn

    async def execute(feedback: str | None) -> Any:
        coro = drive_turn(_retry_prompt(prompt, feedback), tools)
        if wf.step_timeout_s is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, wf.step_timeout_s)
        except TimeoutError as exc:  # per-step cap (manual §17) → abort the step
            raise StepFailed(
                f"agent step {name or phase!r} timed out after {wf.step_timeout_s}s"
            ) from exc

    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args={"prompt": prompt, "tools": tools, "phase": phase},
        execute=execute,
        check=check,
        retries=retries,
        cache=cache,
    )


async def sandbox_node(
    wf: WorkflowHandle,
    *,
    run: str,
    phase: str,
    check: Check | None = None,
    name: str | None = None,
    key: str = "",
    cache: bool = True,
) -> dict[str, Any]:
    """Run one deterministic node — a command in the sandbox, no LLM (manual §5.2).
    Journals ``{exit_code, stdout}``; an optional ``check`` gates it (a deterministic
    node is often its own check). Reaches platform capabilities over HTTP from inside
    the sandbox (later phases)."""
    if wf.run_sandbox is None:
        raise RuntimeError("sandbox_node needs a sandbox runner (wired by the run driver)")
    run_sandbox = wf.run_sandbox

    async def execute(_feedback: str | None) -> dict[str, Any]:
        exit_code, stdout = await run_sandbox(run)
        return {"exit_code": exit_code, "stdout": stdout}

    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args={"run": run, "phase": phase},
        execute=execute,
        check=check,
        cache=cache,
    )
