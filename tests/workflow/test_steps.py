"""Node adapters (manual §5) — agent_step (gated LLM turn) and sandbox_node
(deterministic command), both on the filesystem-journal engine."""

import asyncio

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.checks import file_nonempty
from workspace_app.workflow.engine import CheckResult, StepFailed
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.steps import agent_step, sandbox_node


async def test_agent_step_per_step_timeout_aborts(wf: WorkflowHandle):
    """A turn that outruns the per-step cap (manual §17) aborts the step with a
    StepFailed (which the driver surfaces as a terminal error)."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", step_timeout_s=0.01)

    async def slow_turn(prompt: str, tools: list[str] | None) -> str:
        await asyncio.sleep(5)
        return "never"

    wf.drive_turn = slow_turn
    with pytest.raises(StepFailed, match="timed out"):
        await agent_step(wf, prompt="p", phase="p", check=file_nonempty("/x"))


async def test_agent_step_gates_then_journals_and_skips_on_rerun(wf: WorkflowHandle):
    """An agent turn writes its output; the gate passes; the step journals and is
    skipped (turn not re-driven) on an identical re-run."""
    turns: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        turns.append(prompt)
        await wf.write("/plan.json", '{"collection": "a"}')
        return "ok"

    wf.drive_turn = drive_turn
    await agent_step(wf, prompt="classify", phase="classify", check=file_nonempty("/plan.json"))
    await agent_step(wf, prompt="classify", phase="classify", check=file_nonempty("/plan.json"))
    assert len(turns) == 1  # second call skipped via the journal


async def test_agent_step_retries_with_feedback_until_the_gate_passes(wf: WorkflowHandle):
    """A failing gate re-drives the turn with the failure fed back into the prompt;
    once the agent produces valid output the gate passes."""
    prompts: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        prompts.append(prompt)
        await wf.write("/out.txt", "" if len(prompts) == 1 else "fixed")
        return "ok"

    wf.drive_turn = drive_turn
    await agent_step(wf, prompt="write out", phase="p", check=file_nonempty("/out.txt"), retries=2)
    assert len(prompts) == 2
    assert "did not pass" in prompts[1]  # the gate's reason was fed back


async def test_agent_step_aborts_after_exhausting_retries(wf: WorkflowHandle):
    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        await wf.write("/out.txt", "")  # never satisfies the gate
        return "ok"

    wf.drive_turn = drive_turn
    with pytest.raises(StepFailed):
        await agent_step(wf, prompt="x", phase="p", check=file_nonempty("/out.txt"), retries=1)


async def test_agent_step_requires_a_gate(wf: WorkflowHandle):
    """`check` is a required argument — an ungated agent node is not expressible
    (manual §5.1: avoid 'the agent thinks it's done but isn't')."""
    with pytest.raises(TypeError):
        await agent_step(wf, prompt="x", phase="p")  # ty: ignore[missing-argument]


async def test_agent_step_without_a_wired_driver_is_a_clear_error(wf: WorkflowHandle):
    with pytest.raises(RuntimeError, match="turn driver"):
        await agent_step(wf, prompt="x", phase="p", check=file_nonempty("/x"))


async def test_sandbox_node_journals_exit_and_skips_on_rerun(wf: WorkflowHandle):
    runs: list[str] = []

    async def run_sandbox(cmd: str) -> tuple[int, str]:
        runs.append(cmd)
        return (0, "hello\n")

    wf.run_sandbox = run_sandbox
    out = await sandbox_node(wf, run="echo hello", phase="say")
    assert out == {"exit_code": 0, "stdout": "hello\n"}
    await sandbox_node(wf, run="echo hello", phase="say")  # skip
    assert len(runs) == 1


async def test_sandbox_node_gate_fails_on_nonzero_exit(wf: WorkflowHandle):
    async def run_sandbox(cmd: str) -> tuple[int, str]:
        return (1, "boom")

    async def exit_zero(_wf: WorkflowHandle, result: dict) -> CheckResult:
        return CheckResult(result["exit_code"] == 0, f"exit {result['exit_code']}")

    wf.run_sandbox = run_sandbox
    with pytest.raises(StepFailed):
        await sandbox_node(wf, run="false", phase="check", check=exit_zero)


async def test_sandbox_node_without_a_wired_runner_is_a_clear_error(wf: WorkflowHandle):
    with pytest.raises(RuntimeError, match="sandbox runner"):
        await sandbox_node(wf, run="echo", phase="p")
