"""The node adapters (#100, manual §5) — thin wrappers over ``run_step``.

``agent_step`` and ``sandbox_node`` are *adapters*: they build the right ``execute``
coroutine (drive a ChatTurnEngine turn / run a sandbox command) and hand it to the
journal engine (``run_step``), which owns run-vs-skip + retry + journaling (manual
§9). They are deliberately invoker-distinguished (manual §7): an agent node is
LLM-driven and **must** be gated; a deterministic node is author code with no LLM.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .checks import file_nonempty
from .engine import Check, StepFailed, _emit, run_step
from .events import StepOutput
from .handle import WorkflowHandle


def _parse_fields(text: str) -> Any:
    """Parse an agent reply / sandbox stdout as a JSON object → ``result.fields``
    (#428 §1.2). Returns ``None`` on unparseable / non-object output so the step's
    gate can flag it (and retry with feedback) rather than crashing the run."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed


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


async def agent_write_step(
    wf: WorkflowHandle,
    *,
    prompt: str,
    phase: str,
    out: str = "",
    name: str | None = None,
    key: str = "",
    tools: list[str] | None = None,
    retries: int = 0,
    cache: bool = True,
    check: Check | None = None,
    outputs: dict[str, Any] | None = None,
) -> Any:
    """Decision/action content step (issue #107): the agent PRODUCES the file's
    content as its message output — it does NOT call ``write_file`` — then a
    deterministic write commits that text to ``out``. This avoids routing long
    content through a tool argument, which models (small *and* large) emit
    unreliably: the call comes back as plain text and never executes, so the file
    is silently left unwritten. Gated on the written file (``file_nonempty(out)``
    by default). Journaled like any step (re-run skips); the input-hash covers the
    prompt + tools + out, so editing any of them re-runs it (§9).

    Give it read-only ``tools`` (e.g. ``["read_file"]``) — the agent reads what it
    needs and answers with the content; the step writes it."""
    if wf.drive_turn is None:
        raise RuntimeError("agent_write_step needs a turn driver (wired by the run driver)")
    drive_turn = wf.drive_turn

    async def execute(feedback: str | None) -> Any:
        coro = drive_turn(_retry_prompt(prompt, feedback), tools)
        if wf.step_timeout_s is None:
            text = await coro
        else:
            try:
                text = await asyncio.wait_for(coro, wf.step_timeout_s)
            except TimeoutError as exc:  # per-step cap (manual §17) → abort the step
                raise StepFailed(
                    f"agent step {name or phase!r} timed out after {wf.step_timeout_s}s"
                ) from exc
        result: dict[str, Any] = {"out": out, "bytes": len(text)}
        if out:
            await wf.write(out, text)
        if outputs is not None:  # #428 §1.2: expose the reply's JSON as result.fields
            result["fields"] = _parse_fields(text)
        return result

    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args={"prompt": prompt, "tools": tools, "out": out, "outputs": outputs, "phase": phase},
        execute=execute,
        check=check or file_nonempty(out),
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
    outputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one deterministic node — a command in the sandbox, no LLM (manual §5.2).
    Journals ``{exit_code, stdout}``; an optional ``check`` gates it (a deterministic
    node is often its own check). Reaches platform capabilities over HTTP from inside
    the sandbox (later phases)."""
    if wf.run_sandbox is None:
        raise RuntimeError("sandbox_node needs a sandbox runner (wired by the run driver)")
    run_sandbox = wf.run_sandbox
    step_name = name or phase

    async def execute(_feedback: str | None) -> dict[str, Any]:
        # Stream stdout live as StepOutput so a long command shows movement instead
        # of looking dead (#178); the complete stdout is still journaled below.
        def on_output(chunk: bytes) -> None:
            _emit(
                wf,
                StepOutput(
                    phase=phase, name=step_name, key=key, text=chunk.decode("utf-8", "replace")
                ),
            )

        exit_code, stdout = await run_sandbox(run, on_output)
        result: dict[str, Any] = {"exit_code": exit_code, "stdout": stdout}
        if outputs is not None:  # #428 §1.2: parse stdout JSON into result.fields
            result["fields"] = _parse_fields(stdout)
        return result

    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args={"run": run, "phase": phase, "outputs": outputs},
        execute=execute,
        check=check,
        cache=cache,
    )
