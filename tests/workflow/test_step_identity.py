"""docs/plan-workflow-out-of-chat.md P2 — a turn knows which step it is.

The execution page shows one row per step (D6), so each message a step's turn
persists is marked with that step. The step is known where the step runs
(`agent_step` / `agent_write_step`); the executor that persists the turn reads
it from `CURRENT_STEP`, which every task (each map element, each parallel step)
holds its own copy of.
"""

from __future__ import annotations

import asyncio

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.steps import CURRENT_STEP, StepRef, agent_step, agent_write_step


def _wf(seen: list[str]) -> WorkflowHandle:
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", workflow_id="w")

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        ref = CURRENT_STEP.get()
        seen.append(ref.id if ref else "")
        await asyncio.sleep(0)  # let siblings interleave
        return "a note"

    wf.drive_turn = drive_turn
    return wf


async def test_a_write_step_runs_its_turn_as_itself_and_one_loop_element_as_name_slash_key() -> (
    None
):
    seen: list[str] = []
    wf = _wf(seen)

    await agent_write_step(wf, prompt="p", phase="digest", out="n.md")
    await agent_write_step(wf, prompt="p", phase="digest", name="digest", key="a.md", out="a.md")

    assert seen == ["digest", "digest/a.md"]
    assert CURRENT_STEP.get() is None  # nothing outside a step claims to be one


async def test_steps_running_side_by_side_each_keep_their_own_identity() -> None:
    seen: list[str] = []
    wf = _wf(seen)

    await asyncio.gather(
        *(
            agent_write_step(wf, prompt=f"p{k}", phase="digest", key=k, out=f"{k}.md")
            for k in ("a", "b", "c")
        )
    )

    assert sorted(seen) == ["digest/a", "digest/b", "digest/c"]


async def test_a_gated_agent_step_runs_its_turn_as_itself() -> None:
    from workspace_app.workflow.checks import file_nonempty

    seen: list[str] = []
    wf = _wf(seen)

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        assert CURRENT_STEP.get() == StepRef("classify", "", "classify")
        seen.append("classify")
        await wf.write("out.md", "x")
        return "done"

    wf.drive_turn = drive_turn
    await agent_step(wf, prompt="p", phase="classify", check=file_nonempty("out.md"))

    assert seen == ["classify"]


async def test_a_step_knows_the_phase_it_runs_in() -> None:
    """The execution page's rows are phases ("Step 2 of 3"); a loop element is
    gone from the run's board once it ends, so its messages say their phase
    themselves rather than leaving the page to look it up."""
    phases: list[str] = []
    wf = _wf([])

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        ref = CURRENT_STEP.get()
        phases.append(ref.phase if ref else "")
        return "a note"

    wf.drive_turn = drive_turn
    await agent_write_step(wf, prompt="p", phase="digest", name="summarise", key="a", out="a.md")

    assert phases == ["digest"]
