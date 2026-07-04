"""The filesystem-as-journal step engine (manual §9) — run vs skip, input-hash
auto-invalidation, cache=False, and retry-with-feedback then abort."""

import asyncio

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.checks import choice_in, file_nonempty
from workspace_app.workflow.engine import StepFailed, fail, run_step
from workspace_app.workflow.handle import WorkflowHandle


def _counter():
    calls: list[str | None] = []

    async def execute(feedback: str | None):
        calls.append(feedback)
        return {"n": len(calls)}

    return calls, execute


async def test_step_runs_then_skips_on_identical_rerun(wf: WorkflowHandle):
    """Second run with identical args is skipped — the cached artifact is returned,
    execute is not called again (no LLM/sandbox re-run, manual §9)."""
    calls, execute = _counter()
    r1 = await run_step(wf, name="s", args={"a": 1}, execute=execute)
    r2 = await run_step(wf, name="s", args={"a": 1}, execute=execute)
    assert r1 == {"n": 1}
    assert r2 == {"n": 1}  # the cached result, not a fresh {"n": 2}
    assert len(calls) == 1


async def test_changed_args_reruns(wf: WorkflowHandle):
    """input-hash = hash(args): different args → cache miss → re-run. This is how
    editing an upstream artifact auto-invalidates a downstream step."""
    calls, execute = _counter()
    await run_step(wf, name="s", args={"a": 1}, execute=execute)
    r2 = await run_step(wf, name="s", args={"a": 2}, execute=execute)
    assert r2 == {"n": 2}
    assert len(calls) == 2


async def test_deleted_artifact_reruns(wf: WorkflowHandle):
    """Deleting a step's artifact forces it to re-run (the file-based retry/rewind
    of manual §9)."""
    calls, execute = _counter()
    await run_step(wf, name="s", args={"a": 1}, execute=execute)
    await wf.delete("/.workflow/_default/step_s/main.json")
    await run_step(wf, name="s", args={"a": 1}, execute=execute)
    assert len(calls) == 2


async def test_cache_false_always_reruns(wf: WorkflowHandle):
    """cache=False ⇒ the step always re-runs (e.g. a 'fetch latest' step, §9)."""
    calls, execute = _counter()
    await run_step(wf, name="s", args={"a": 1}, execute=execute, cache=False)
    await run_step(wf, name="s", args={"a": 1}, execute=execute, cache=False)
    assert len(calls) == 2


async def test_per_key_artifacts_are_independent(wf: WorkflowHandle):
    """A loop element's artifact is keyed by its element key (manual §9), so two
    elements of the same step are journaled + skipped independently."""
    calls, execute = _counter()
    await run_step(wf, name="classify", key="f1", args={"f": "f1"}, execute=execute)
    await run_step(wf, name="classify", key="f2", args={"f": "f2"}, execute=execute)
    await run_step(wf, name="classify", key="f1", args={"f": "f1"}, execute=execute)  # skip
    assert len(calls) == 2


async def test_gate_failure_retries_with_feedback_then_aborts(wf: WorkflowHandle):
    """A failing gate re-runs the step up to `retries` times, feeding the failure
    reason back each attempt; exhausting retries raises StepFailed (manual §6)."""

    async def execute(feedback: str | None):
        await wf.write("/out.txt", "")  # always writes an empty file → gate fails
        return {"feedback": feedback}

    with pytest.raises(StepFailed):
        await run_step(
            wf, name="s", args={}, execute=execute, check=file_nonempty("/out.txt"), retries=2
        )
    # no artifact is journaled for a failed step → a later run retries it
    assert not await wf.exists("/.workflow/_default/step_s/main.json")


async def test_gate_passes_after_retry_using_feedback(wf: WorkflowHandle):
    """The fed-back reason lets the step correct itself; once the gate passes the
    step is journaled."""
    attempts: list[str | None] = []

    async def execute(feedback: str | None):
        attempts.append(feedback)
        await wf.write("/out.txt", "" if feedback is None else "fixed")
        return {}

    await run_step(
        wf, name="s", args={}, execute=execute, check=file_nonempty("/out.txt"), retries=2
    )
    assert attempts[0] is None
    assert attempts[1] is not None  # the gate's reason was fed back
    assert await wf.exists("/.workflow/_default/step_s/main.json")


async def test_choice_in_gate_clamps_to_allowed_set(wf: WorkflowHandle):
    """choice_in enforces the agent's recorded decision is within the allowed set
    (manual §8) — a disallowed pick fails the gate."""

    async def writes(value: str):
        async def execute(_feedback: str | None):
            await wf.write_json("/plan.json", {"collection": value})
            return {}

        return execute

    await run_step(
        wf,
        name="ok",
        args={"v": "a"},
        execute=await writes("a"),
        check=choice_in("/plan.json", key="collection", allowed=["a", "b"]),
    )
    with pytest.raises(StepFailed):
        await run_step(
            wf,
            name="bad",
            args={"v": "z"},
            execute=await writes("z"),
            check=choice_in("/plan.json", key="collection", allowed=["a", "b"]),
        )


async def test_fail_helper_raises_step_failed():
    """`fail(reason)` aborts the current step/element."""
    with pytest.raises(StepFailed, match="nope"):
        fail("nope")


async def test_artifact_lives_under_per_workflow_dir():
    """#136: a journaled step's artifact lives under /.workflow/<workflow_id>/ — its
    own folder — instead of being scattered at the workspace root."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", workflow_id="memory")
    _calls, execute = _counter()
    await run_step(wf, name="s", args={"a": 1}, execute=execute)
    assert await wf.exists("/.workflow/memory/step_s/main.json")
    assert not await wf.exists("/step_s/main.json")


async def test_cancel_before_journal_reruns_and_idempotent_side_effects_self_heal():
    """If a run is cancelled after a step's body ran but before it journals, the step has
    NO receipt, so the next run re-executes it (#429 P5). That is safe because workflow
    side-effects are idempotent (create-by-args / update-by-patch / ingest-by-doc-id /
    card-by-key), so a re-do can't duplicate — the orphan self-heals on re-run rather than
    being silently lost."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", workflow_id="pm")
    started = asyncio.Event()
    ran = 0

    async def execute(_feedback):
        nonlocal ran
        ran += 1
        started.set()
        await asyncio.sleep(0.05)  # body in flight when the cancel arrives
        return {"n": ran}

    task = asyncio.create_task(run_step(wf, name="commit", args={"a": 1}, execute=execute))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await wf.exists("/.workflow/pm/step_commit/main.json")  # no receipt → will re-run
    # the re-run re-executes (the cancelled attempt left no journal) and now commits
    assert await run_step(wf, name="commit", args={"a": 1}, execute=execute) == {"n": 2}
    assert await wf.exists("/.workflow/pm/step_commit/main.json")
