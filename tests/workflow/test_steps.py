"""Node adapters (manual §5) — agent_step (gated LLM turn) and sandbox_node
(deterministic command), both on the filesystem-journal engine."""

import asyncio

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.checks import file_nonempty
from workspace_app.workflow.engine import CheckResult, StepFailed
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.steps import agent_step, agent_write_step, sandbox_node


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


async def test_agent_step_reads_reruns_on_declared_content_change(wf: WorkflowHandle):
    """A plain (gated) agent node honours `reads` too: editing a declared source the
    turn depends on re-runs it, prompt unchanged (#429 P1)."""
    turns: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        turns.append(prompt)
        await wf.write("/out.txt", "done")
        return "ok"

    wf.drive_turn = drive_turn
    await wf.write("/src.txt", "a")
    await agent_step(wf, prompt="p", phase="p", check=file_nonempty("/out.txt"), reads=["src.txt"])
    await agent_step(wf, prompt="p", phase="p", check=file_nonempty("/out.txt"), reads=["src.txt"])
    assert len(turns) == 1

    await wf.write("/src.txt", "b")
    await agent_step(wf, prompt="p", phase="p", check=file_nonempty("/out.txt"), reads=["src.txt"])
    assert len(turns) == 2


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

    async def run_sandbox(cmd: str, on_output=None) -> tuple[int, str]:
        runs.append(cmd)
        return (0, "hello\n")

    wf.run_sandbox = run_sandbox
    out = await sandbox_node(wf, run="echo hello", phase="say")
    assert out == {"exit_code": 0, "stdout": "hello\n"}
    await sandbox_node(wf, run="echo hello", phase="say")  # skip
    assert len(runs) == 1


async def test_sandbox_node_reads_reruns_on_declared_content_change(wf: WorkflowHandle):
    """A sandbox_node that declares `reads` re-runs when a declared file's CONTENT
    changes, even though `run` (the command) and the path are unchanged — the engine
    folds the declared files' content fingerprint into the input-hash (#429 P1). This
    is the content-aware invalidation that a bare path arg does NOT give."""
    runs: list[str] = []

    async def run_sandbox(cmd: str, on_output=None) -> tuple[int, str]:
        runs.append(cmd)
        return (0, "ok\n")

    wf.run_sandbox = run_sandbox
    await wf.write("/data.txt", "v1")
    await sandbox_node(wf, run="python analyze.py", phase="a", reads=["data.txt"])
    await sandbox_node(wf, run="python analyze.py", phase="a", reads=["data.txt"])
    assert len(runs) == 1  # unchanged declared content → skipped via the journal

    await wf.write("/data.txt", "v2")  # CONTENT changed; command + path unchanged
    await sandbox_node(wf, run="python analyze.py", phase="a", reads=["data.txt"])
    assert len(runs) == 2  # re-ran because the declared file's content changed


async def test_sandbox_node_gate_fails_on_nonzero_exit(wf: WorkflowHandle):
    async def run_sandbox(cmd: str, on_output=None) -> tuple[int, str]:
        return (1, "boom")

    async def exit_zero(_wf: WorkflowHandle, result: dict) -> CheckResult:
        return CheckResult(result["exit_code"] == 0, f"exit {result['exit_code']}")

    wf.run_sandbox = run_sandbox
    with pytest.raises(StepFailed):
        await sandbox_node(wf, run="false", phase="check", check=exit_zero)


async def test_sandbox_node_without_a_wired_runner_is_a_clear_error(wf: WorkflowHandle):
    with pytest.raises(RuntimeError, match="sandbox runner"):
        await sandbox_node(wf, run="echo", phase="p")


async def test_agent_write_step_writes_the_reply_and_journals(wf: WorkflowHandle):
    """Decision/action (#107): the agent REPLIES with the content (no write_file);
    agent_write_step commits that text to `out`, gates on it, journals, and skips on
    an identical re-run."""
    turns: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        turns.append(prompt)
        return "the produced note"

    wf.drive_turn = drive_turn
    await agent_write_step(wf, prompt="produce", phase="p", out="/note.md", tools=["read_file"])
    assert await wf.read_text("/note.md") == "the produced note"
    await agent_write_step(wf, prompt="produce", phase="p", out="/note.md", tools=["read_file"])
    assert len(turns) == 1  # journaled → skipped on identical re-run


async def test_agent_write_step_overwrites_an_existing_file(wf: WorkflowHandle):
    """A pre-existing (seeded) file is replaced by the agent's reply — the write is
    unconditional, so there is no create-only 'already exists' wall (#107)."""
    await wf.write("/MEMORY.md", "STALE")

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return "FRESH"

    wf.drive_turn = drive_turn
    await agent_write_step(wf, prompt="rewrite", phase="p", out="/MEMORY.md", tools=["read_file"])
    assert await wf.read_text("/MEMORY.md") == "FRESH"


async def test_agent_write_step_reads_reruns_on_declared_content_change(wf: WorkflowHandle):
    """`reads` on an agent node folds the declared source files' content into the
    input-hash, so editing a source the agent summarised re-runs it — even though the
    prompt is unchanged (#429 P1)."""
    turns: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        turns.append(prompt)
        return "summary"

    wf.drive_turn = drive_turn
    await wf.write("/src.md", "original")
    await agent_write_step(wf, prompt="summarise src", phase="p", out="/o.md", reads=["src.md"])
    await agent_write_step(wf, prompt="summarise src", phase="p", out="/o.md", reads=["src.md"])
    assert len(turns) == 1  # unchanged source → skipped

    await wf.write("/src.md", "revised")  # source content changed; prompt unchanged
    await agent_write_step(wf, prompt="summarise src", phase="p", out="/o.md", reads=["src.md"])
    assert len(turns) == 2  # re-ran because the declared source changed


async def test_agent_write_step_per_step_timeout_aborts():
    """The produce turn is subject to the same per-step cap as agent_step."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", step_timeout_s=0.01)

    async def slow_turn(prompt: str, tools: list[str] | None) -> str:
        await asyncio.sleep(5)
        return "never"

    wf.drive_turn = slow_turn
    with pytest.raises(StepFailed, match="timed out"):
        await agent_write_step(wf, prompt="p", phase="p", out="/x.md")
