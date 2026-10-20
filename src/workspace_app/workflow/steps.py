"""The node adapters (#100, manual §5) — thin wrappers over ``run_step``.

``agent_step`` and ``sandbox_node`` are *adapters*: they build the right ``execute``
coroutine (drive a ChatTurnEngine turn / run a sandbox command) and hand it to the
journal engine (``run_step``), which owns run-vs-skip + retry + journaling (manual
§9). They are deliberately invoker-distinguished (manual §7): an agent node is
LLM-driven and **must** be gated; a deterministic node is author code with no LLM.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, NamedTuple

from .checks import artifact_valid, exit_zero
from .engine import Check, CheckResult, StepFailed, _emit, run_step
from .events import StepOutput
from .handle import WorkflowHandle
from .output_format import STRUCTURED_KINDS, instruction, repair_request

logger = logging.getLogger(__name__)


async def reads_fingerprint(wf: WorkflowHandle, reads: list[str] | None) -> dict[str, str] | None:
    """A content fingerprint of the files a step DECLARES it reads (#429 P1).

    Expands each ``reads`` glob/path, hashes each matched file's bytes, and returns a
    sorted ``{relpath: sha256}`` map to fold into the step's input-hash — so editing a
    declared file's CONTENT (not just its path) re-runs the step, which a bare path
    arg does not give. The engine does this for the author: they only DECLARE what they
    read; they cannot forget to interpolate a digest or compute it wrong.

    A declared pattern that matches nothing contributes a stable sentinel keyed by the
    pattern, so a missing→present transition also re-runs the step. Returns ``None`` for
    an empty/absent ``reads`` so a step that declares nothing keeps its exact prior hash
    (no cache-bust for the untouched majority)."""
    if not reads:
        return None
    fp: dict[str, str] = {}
    for pat in reads:
        matched = await wf.glob(pat)
        if not matched:
            fp[pat.lstrip("/")] = "\x00absent"  # keyed by the pattern → appears/disappears
            continue
        for path in matched:
            fp[path.lstrip("/")] = hashlib.sha256(await wf.read(path)).hexdigest()
    return fp


def _parse_fields(text: str) -> Any:
    """Parse an agent reply / sandbox stdout as a JSON object → ``result.fields``
    (#428 §1.2). Returns ``None`` on unparseable / non-object output so the step's
    gate can flag it (and retry with feedback) rather than crashing the run."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed


class StepRef(NamedTuple):
    """A step as the journal knows it: ``name`` plus ``key``, the loop element.
    ``phase`` is the diagram phase it runs in — what the execution page groups
    its rows by; not part of the journal identity (``id``)."""

    name: str
    key: str = ""
    phase: str = ""

    @property
    def id(self) -> str:
        """``name``, or ``name/key`` for one element of a loop: what a message
        the step persists is marked with (``Message.step``)."""
        return f"{self.name}/{self.key}" if self.key else self.name


#: The step whose agent turn is running. The executor that persists the turn
#: marks the messages with it and writes its transcript beside its artifact
#: (plan-workflow-out-of-chat D6 / D9). A ContextVar, not a handle attribute:
#: steps running side by side (``asyncio.gather``, map elements) each hold their
#: own copy, where one attribute would be overwritten.
CURRENT_STEP: ContextVar[StepRef | None] = ContextVar("workflow_current_step", default=None)


#: Set while a step's turn is a REPAIR of its reply's format
#: (plan-workflow-output-repair §3.5): the executor records that turn's prompt under
#: `REPAIR_REQUEST_ROLE`, not as the step's question. A ContextVar for the same reason
#: as `CURRENT_STEP`: parallel steps each hold their own.
REPAIR_TURN: ContextVar[bool] = ContextVar("workflow_repair_turn", default=False)


@contextmanager
def _as_repair() -> Iterator[None]:
    token = REPAIR_TURN.set(True)
    try:
        yield
    finally:
        REPAIR_TURN.reset(token)


@contextmanager
def _as_step(name: str, key: str, phase: str) -> Iterator[None]:
    # Set before the turn's coroutine is created and awaited, so the turn — and
    # any task it starts, which copies the context then — sees this step.
    token = CURRENT_STEP.set(StepRef(name, key, phase))
    try:
        yield
    finally:
        CURRENT_STEP.reset(token)


#: How much of the previous answer a retry prompt carries.
_PREVIOUS_ANSWER_CHARS = 4000


def _retry_prompt(prompt: str, feedback: str | None, previous: str | None = None) -> str:
    """The prompt again, with why the last attempt failed — and what it answered:
    a node reads no conversation (plan-workflow-out-of-chat D1), so the attempt
    it is told to fix must travel in the prompt itself (review R1)."""
    if not feedback:
        return prompt
    shown = ""
    if previous:
        cut = previous[:_PREVIOUS_ANSWER_CHARS]
        more = " …(cut)" if len(previous) > _PREVIOUS_ANSWER_CHARS else ""
        shown = f"\n\nYour previous answer was:\n{cut}{more}"
    return (
        f"{prompt}{shown}\n\nYour previous attempt did not pass its check: {feedback}\n"
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
    reads: list[str] | None = None,
) -> Any:
    """Run one agent node — an LLM turn on the item, gated. ``check`` is a required
    argument: an agent node without a gate is not expressible (manual §5.1). The
    step's identity is ``name`` (defaults to ``phase``) + ``key`` (the loop element);
    its input-hash covers the prompt + tools, so editing either re-runs it (§9)."""
    if wf.drive_turn is None:
        raise RuntimeError("agent_step needs a turn driver (wired by the run driver)")
    drive_turn = wf.drive_turn

    previous: list[str] = []  # the last attempt's answer, for the retry prompt

    async def execute(feedback: str | None) -> Any:
        with _as_step(name or phase, key, phase):
            answer = await _turn(feedback)
            previous[:] = [answer] if isinstance(answer, str) else []
            return answer

    async def _turn(feedback: str | None) -> Any:
        coro = drive_turn(_retry_prompt(prompt, feedback, next(iter(previous), None)), tools)
        if wf.step_timeout_s is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, wf.step_timeout_s)
        except TimeoutError as exc:  # per-step cap (manual §17) → abort the step
            logger.warning("agent step timed out after %ss (phase %s)", wf.step_timeout_s, phase)
            raise StepFailed(
                f"agent step {name or phase!r} timed out after {wf.step_timeout_s}s"
            ) from exc

    args: dict[str, Any] = {"prompt": prompt, "tools": tools, "phase": phase}
    fp = await reads_fingerprint(wf, reads)
    if fp is not None:  # #429 P1: only when declared → untouched steps keep their hash
        args["reads"] = fp
    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args=args,
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
    kind: str = "text",
    requires: dict[str, Any] | None = None,
    name: str | None = None,
    key: str = "",
    tools: list[str] | None = None,
    retries: int = 0,
    cache: bool = True,
    check: Check | None = None,
    outputs: dict[str, Any] | None = None,
    produces: str = "",
    reads: list[str] | None = None,
) -> Any:
    """Decision/action content step (issue #107): the agent PRODUCES the file's
    content as its message output — it does NOT call ``write_file`` — then a
    deterministic write commits that text to ``out``. This avoids routing long
    content through a tool argument, which models (small *and* large) emit
    unreliably: the call comes back as plain text and never executes, so the file
    is silently left unwritten. Gated by default on ``artifact_valid(out, kind)``
    (plan §2.2): the written file must exist, be non-empty, and — for a structured
    ``kind`` (json/yaml/csv) — PARSE as that format, so a reply that leaks
    conversational text fails and retries instead of flowing downstream polluted.
    ``kind`` defaults to ``text`` (non-empty, like the old ``file_nonempty``).
    Journaled like any step (re-run skips); the input-hash covers the prompt +
    tools + out, so editing any of them re-runs it (§9).

    Give it read-only ``tools`` (e.g. ``["read_file"]``) — the agent reads what it
    needs and answers with the content; the step writes it."""
    if wf.drive_turn is None:
        raise RuntimeError("agent_write_step needs a turn driver (wired by the run driver)")
    drive_turn = wf.drive_turn
    # D8: the shape the reply must take, told up front, from the same declaration the
    # gate checks — so the first answer usually needs no repair at all.
    shape = instruction(outputs, kind if out else "")
    asked = f"{prompt}\n\n{shape}" if shape else prompt

    # The last attempt's reply: a node reads no conversation (plan-workflow-out-of-chat
    # D1), so a re-run's prompt carries it, and a repair quotes it in full
    # (plan-workflow-output-repair §3.3).
    previous: list[str] = []

    async def turn(text_in: str, held: list[str] | None) -> str:
        coro = drive_turn(text_in, held)
        if wf.step_timeout_s is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, wf.step_timeout_s)
        except TimeoutError as exc:  # per-step cap (manual §17) → abort the step
            logger.warning(
                "agent write step timed out after %ss (phase %s)", wf.step_timeout_s, phase
            )
            raise StepFailed(
                f"agent step {name or phase!r} timed out after {wf.step_timeout_s}s"
            ) from exc

    async def finish(text: str) -> dict[str, Any]:
        previous[:] = [text]
        result: dict[str, Any] = {"out": out, "bytes": len(text)}
        if out:
            await wf.write(out, text)
        if outputs is not None:  # #428 §1.2: expose the reply's JSON as result.fields
            result["fields"] = _parse_fields(text)
        if produces:
            # The node's output is what it WROTE, so the reply is not parsed at all — the
            # model is free to narrate. Recorded as `fields` so the existing
            # `{steps.<name>.<field>}` lookup resolves it with no second mechanism.
            # The turn's writes went to the sandbox; the glob reads the snapshot, so ask
            # for the write-back FIRST or this node globs state from before its own turn.
            if wf.reconcile is not None:
                await wf.reconcile()
            result["fields"] = {"produces": sorted(await wf.glob(produces))}
        return result

    async def execute(feedback: str | None) -> Any:
        with _as_step(name or phase, key, phase):
            return await finish(
                await turn(_retry_prompt(asked, feedback, next(iter(previous), None)), tools)
            )

    async def repair(verdict: CheckResult) -> Any:
        # D1: the answer re-emitted, not the task redone — the request alone, and no
        # tools, so nothing the step already did can be done again. Marked as a repair
        # so the execution's record keeps the request under its own role (§3.5).
        asked_again = repair_request(
            next(iter(previous), ""), verdict, outputs, kind if out else ""
        )
        with _as_step(name or phase, key, phase), _as_repair():
            return await finish(await turn(asked_again, []))

    args: dict[str, Any] = {
        "prompt": prompt,
        "tools": tools,
        "out": out,
        "kind": kind,
        "requires": requires,
        "outputs": outputs,
        "produces": produces,
        "phase": phase,
    }
    fp = await reads_fingerprint(wf, reads)
    if fp is not None:  # #429 P1: only when declared → untouched steps keep their hash
        args["reads"] = fp
    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args=args,
        execute=execute,
        check=check or artifact_valid(out, kind, requires, of_reply=True),
        retries=retries,
        cache=cache,
        # only a reply with a machine format can be repaired; `produces` writes files
        repair=repair if outputs or (out and kind in STRUCTURED_KINDS) else None,
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
    produces: str = "",
    reads: list[str] | None = None,
) -> dict[str, Any]:
    """Run one deterministic node — a command in the sandbox, no LLM (manual §5.2).
    Journals ``{exit_code, stdout}``; gated by default on ``exit_code == 0`` (plan §2.2)
    so a failed command fails the step instead of silently 'succeeding' — a custom
    ``check`` overrides. Reaches platform capabilities over HTTP from inside the sandbox
    (later phases).

    ``reads`` (#429 P1) DECLARES the files this command depends on: the engine folds
    their content fingerprint into the input-hash, so editing a declared file's content
    re-runs the step (a bare path in ``run`` would skip on a content-only change)."""
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
        logger.info("sandbox node %s: exit=%s (phase %s)", step_name, exit_code, phase)
        result: dict[str, Any] = {"exit_code": exit_code, "stdout": stdout}
        if outputs is not None:  # #428 §1.2: parse stdout JSON into result.fields
            result["fields"] = _parse_fields(stdout)
        if produces:  # the node's output is what it WROTE; stdout is not parsed at all
            # No `wf.reconcile()` here, unlike the agent node: this command ran through
            # the executor's `run_sandbox`, which writes the sandbox back to the snapshot
            # before it returns. Adding one would walk the workspace a second time for
            # state that is already fresh — and that walk is per map element.
            result["fields"] = {"produces": sorted(await wf.glob(produces))}
        return result

    args: dict[str, Any] = {
        "run": run,
        "phase": phase,
        "outputs": outputs,
        "produces": produces,
    }
    fp = await reads_fingerprint(wf, reads)
    if fp is not None:  # only when declared → untouched steps keep their prior hash
        args["reads"] = fp
    return await run_step(
        wf,
        name=name or phase,
        key=key,
        phase=phase,
        args=args,
        execute=execute,
        check=check or exit_zero(),
        cache=cache,
    )
