"""`cache` is required on every `agent` and `sandbox` step (plan-cache-required).

A scheduled workflow whose steps all kept the old default (`true`) ran once and
then skipped every step on every later fire — `done`, nothing done — because the
step journal is per workflow, not per run (§9), and the author was never asked
which steps read the world. There is no default now: each step says whether it
may be skipped while its inputs are unchanged, and the parse error is the rule.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from workspace_app.workflow.dsl import (
    CACHE_RULE,
    AgentStep,
    DslError,
    describe_dsl_grammar,
    parse_def,
)

REPO = Path(__file__).resolve().parents[2]


def _wf(step: dict) -> str:
    return json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": [step]})


@pytest.mark.parametrize(
    "step",
    [
        {"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"},
        {"type": "sandbox", "run": "echo hi", "phase": "p"},
    ],
    ids=["agent", "sandbox"],
)
def test_a_step_without_cache_is_refused_and_told_the_rule(step: dict) -> None:
    with pytest.raises(DslError) as exc:
        parse_def(_wf(step))
    msg = str(exc.value)
    assert "steps[0]" in msg, msg
    assert "`cache` is required" in msg, msg
    assert "reads the world or has a side effect" in msg, msg
    assert "skipped while its inputs are unchanged" in msg, msg


@pytest.mark.parametrize("value", [True, False])
def test_a_step_that_takes_a_stance_parses(value: bool) -> None:
    d = parse_def(
        _wf({"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md", "cache": value})
    )
    step = d.steps[0]
    assert isinstance(step, AgentStep)
    assert step.cache is value


def test_a_missing_field_that_is_not_cache_keeps_msgspecs_own_message() -> None:
    with pytest.raises(DslError) as exc:
        parse_def(_wf({"type": "agent", "phase": "p", "cache": True}))  # no prompt
    assert "prompt" in str(exc.value)
    assert "`cache` is required" not in str(exc.value)


def test_the_authoring_reference_states_the_rule_the_parse_error_states() -> None:
    """The `author-workflow` skill appends this reference at load time; the model
    reads it before writing a step, and the parse error after — one sentence."""
    ref = describe_dsl_grammar()
    agent = ref[ref.index("**agent**") : ref.index("**sandbox**")]
    sandbox = ref[ref.index("**sandbox**") : ref.index("**gate**")]
    assert "required:" in agent and "cache" in agent.split("optional:")[0]
    assert "required:" in sandbox and "cache" in sandbox.split("optional:")[0]
    assert CACHE_RULE in ref
    assert "every fire" in ref


async def test_a_second_fire_skips_a_cached_step_and_runs_an_uncached_one(spec_instance) -> None:
    """The reason the field is required, pinned: the step journal is per
    workflow (§9), so two fires of the same workflow with the same inputs —
    the shape of every schedule — execute a `cache: true` step ONCE and a
    `cache: false` step twice. A scheduled workflow whose steps are all `true`
    does its work on the first fire and reports `done` on every later one."""
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.workflow.engine import run_step
    from workspace_app.workflow.run import RunStatus, WorkflowRun

    from .test_orchestrator import _orch

    ran = {"cached": 0, "fresh": 0}

    async def cached(_fb):
        ran["cached"] += 1
        return {"n": ran["cached"]}

    async def fresh(_fb):
        ran["fresh"] += 1
        return {"n": ran["fresh"]}

    async def run(wf, inputs):
        await run_step(wf, name="report", phase="p", args={"run": "date"}, execute=cached)
        await run_step(wf, name="send", phase="p", args={"run": "mail"}, execute=fresh, cache=False)
        return {"ok": True}

    orch, _ = _orch(spec_instance, run, store=MemoryFileStore())
    rm = spec_instance.get_resource_manager(WorkflowRun)
    for _fire in range(2):
        run_id = await orch.start(
            slug="rca", item_id="i", profile="echo", captured_user="u", workflow_id="w", chat_id="c"
        )
        await asyncio.sleep(0)
        assert rm.get(run_id).data.status is RunStatus.DONE

    assert ran == {"cached": 1, "fresh": 2}


def test_every_shipped_workflow_json_parses() -> None:
    """The migration, pinned: every DSL file this repo ships — the starter
    templates and the apps' declarative profiles — takes a stance on every step."""
    files = sorted(
        [
            *REPO.glob("sample-workflows/**/workflow.json"),
            *REPO.glob("src/workspace_app/apps/**/workflow.json"),
        ]
    )
    assert files, "no shipped workflow.json found — the glob is wrong"
    for f in files:
        parse_def(f.read_bytes())  # raises DslError with the file's problem
