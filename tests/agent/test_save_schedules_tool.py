"""`save_schedules` — the agent's way to put a workflow on a clock.

A schedule is a file the platform sweeps (`.workflows/schedules.json`), and a
file written blind is a file whose one mistyped row silently never fires. So
the agent does not write it: the tool checks first, the way `save_workflow`
does, and hands the problems back to be fixed. What it accepts is the WHOLE
list — one call, one file, the same replace-not-append rule a page's
`writeFile` has — and what it answers with is what it saved and when each row
next runs, so the agent tells the user something it checked rather than
something it believes.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import save_schedules_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow import user_schedules
from workspace_app.workflow.user_schedules import SchedulePolicy

_WORKFLOW = json.dumps(
    {
        "id": "ignored",
        "title": "Nightly",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
).encode()

SCHEDULES = "/.workflows/schedules.json"


@pytest.fixture
def clock(monkeypatch):
    """Pin the tool's clock: Tuesday 2026-09-15, 08:00 in Taipei (00:00 UTC)."""

    def _set(utc: datetime) -> None:
        monkeypatch.setattr(user_schedules, "utc_now", lambda: utc)

    _set(datetime(2026, 9, 15, 0, 0))
    return _set


_ON = SchedulePolicy(max_rows=100, sweep_enabled=True)


def _ctx(*, policy: SchedulePolicy | None = _ON):
    files = WorkspaceFiles(MemoryFileStore())
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=files,
            app_slug="playground",
            template_profile="default",
            schedule_policy=policy,
        )
    )


async def _with_workflow(ctx, workflow_id: str = "nightly") -> None:
    await ctx.context.files.write(
        ctx.context.investigation_id, f"/.workflows/{workflow_id}.json", _WORKFLOW
    )


def _rows(*rows: dict) -> str:
    return json.dumps({"schedules": list(rows)})


async def test_a_valid_file_naming_the_items_own_workflow_is_saved(clock) -> None:
    ctx = _ctx()
    await _with_workflow(ctx)

    out = await save_schedules_impl(
        ctx, _rows({"every": "daily", "at": "09:00", "tz": "Asia/Taipei", "run": "nightly"})
    )

    saved = await ctx.context.files.read(ctx.context.investigation_id, SCHEDULES)
    assert json.loads(saved)["schedules"][0]["run"] == "nightly"
    assert "saved 1 schedule" in out
    # 08:00 Taipei: today's 09:00 is still ahead, and the reply names it in the
    # row's own zone — the one the person chose.
    assert "2026-09-15 09:00 Asia/Taipei" in out


async def test_a_bad_row_is_returned_to_be_fixed_and_nothing_is_written(clock) -> None:
    """The sweep skips a bad row and keeps the rest — it has nobody to tell.
    This has the author on the line, so it refuses the whole file: a half-right
    file reported as saved is the failure the tool exists to prevent."""
    ctx = _ctx()
    await _with_workflow(ctx)

    out = await save_schedules_impl(
        ctx,
        _rows(
            {"every": "daily", "at": "09:00", "run": "nightly"},
            {"every": "day", "at": "09:00", "run": "nightly"},  # `day` is not a period
        ),
    )

    assert out.startswith("error:")
    assert "schedules[1]" in out, "the message must point at the row to fix"
    assert not await ctx.context.files.exists(ctx.context.investigation_id, SCHEDULES)


async def test_a_run_this_item_does_not_have_is_refused_by_name(clock) -> None:
    """Caught at save time, where the author can act — not at 03:00 in a log
    line. Names what the item DOES offer, and the order to do things in."""
    ctx = _ctx()
    await _with_workflow(ctx, "nightly")

    out = await save_schedules_impl(ctx, _rows({"every": "hourly", "run": "nigthly"}))

    assert out.startswith("error:")
    assert "'nigthly'" in out and "nightly" in out
    assert "save_workflow" in out
    assert not await ctx.context.files.exists(ctx.context.investigation_id, SCHEDULES)


async def test_more_rows_than_the_deployment_allows_is_refused(clock) -> None:
    """The sweep's runaway guard, applied where the author is listening."""
    ctx = _ctx(policy=SchedulePolicy(max_rows=1, sweep_enabled=True))
    await _with_workflow(ctx)

    out = await save_schedules_impl(
        ctx,
        _rows({"every": "hourly", "run": "nightly"}, {"every": "daily", "run": "nightly"}),
    )

    assert out.startswith("error:") and "cap of 1" in out
    assert not await ctx.context.files.exists(ctx.context.investigation_id, SCHEDULES)


async def test_a_row_already_due_today_says_it_runs_on_the_next_sweep(clock) -> None:
    """The catch-up rule, stated rather than rounded away: a daily 09:00 saved
    at 10:00 fires within the minute, because a missed window fires late. A
    reply that said "tomorrow 09:00" would be the sweep's own manual contradicted
    by the tool standing in for it — and the report would land while the agent
    was still promising it for tomorrow."""
    ctx = _ctx()
    await _with_workflow(ctx)
    clock(datetime(2026, 9, 15, 2, 0))  # 10:00 Taipei

    out = await save_schedules_impl(
        ctx, _rows({"every": "daily", "at": "09:00", "tz": "Asia/Taipei", "run": "nightly"})
    )

    assert "next run on the next sweep" in out
    assert "2026-09-16" not in out


async def test_a_row_that_already_fired_this_period_says_the_next_one(clock) -> None:
    """The ledger, not a guess: an unchanged row re-saved after today's run
    keeps its identity, so the sweep will NOT fire it again today — and the
    reply says tomorrow, in the row's zone."""
    from workspace_app.resources import make_spec
    from workspace_app.workflow.triggers import SpecstarTriggerStore, register_trigger_store
    from workspace_app.workflow.user_schedules import parse_user_schedules, trigger_id_for

    spec = make_spec()
    register_trigger_store(spec)
    ctx = _ctx()
    ctx.context.spec = spec
    await _with_workflow(ctx)
    raw = _rows({"every": "daily", "at": "09:00", "tz": "Asia/Taipei", "run": "nightly"})
    (row,) = parse_user_schedules(raw)
    # Today's window fired already (the sweep claimed it at 09:00 Taipei).
    SpecstarTriggerStore(spec).try_claim(trigger_id_for("inv-1", "/.workflows", row), "2026-09-15")
    clock(datetime(2026, 9, 15, 2, 0))  # 10:00 Taipei

    out = await save_schedules_impl(ctx, raw)

    assert "next run 2026-09-16 09:00 Asia/Taipei" in out


async def test_a_deployment_with_the_sweep_off_saves_but_says_so_loudly(clock) -> None:
    """Saved, because the file is harmless and starts working the day an
    operator turns the sweep on — and LOUD, because "set up" on a deploy where
    nothing will run is the one thing the agent must not be allowed to say."""
    ctx = _ctx(policy=SchedulePolicy(max_rows=100, sweep_enabled=False))
    await _with_workflow(ctx)

    out = await save_schedules_impl(ctx, _rows({"every": "hourly", "run": "nightly"}))

    assert await ctx.context.files.exists(ctx.context.investigation_id, SCHEDULES)
    assert "WARNING" in out and "trigger_check_interval_sec" in out
    # And the row itself says so — a "next run" on a deployment where nothing
    # runs is the sentence the agent would relay.
    assert "will not run" in out
    assert "next run" not in out


async def test_an_empty_list_cancels_everything(clock) -> None:
    """Replace-not-append means "no rows" is a valid thing to say."""
    ctx = _ctx()
    await _with_workflow(ctx)
    await save_schedules_impl(ctx, _rows({"every": "hourly", "run": "nightly"}))

    out = await save_schedules_impl(ctx, _rows())

    saved = await ctx.context.files.read(ctx.context.investigation_id, SCHEDULES)
    assert json.loads(saved) == {"schedules": []}
    assert "saved 0 schedules" in out


async def test_the_payload_is_echoed_so_the_agent_relays_what_it_set(clock) -> None:
    ctx = _ctx()
    await _with_workflow(ctx)

    out = await save_schedules_impl(
        ctx, _rows({"every": "hourly", "run": "nightly", "with": {"line": "A"}})
    )

    assert '{"line": "A"}' in out


async def test_a_turn_without_the_deployments_policy_declines(clock) -> None:
    """Not "assume the sweep runs": a context that cannot say what the deploy
    does with the file cannot promise anything about it."""
    ctx = _ctx(policy=None)
    await _with_workflow(ctx)

    out = await save_schedules_impl(ctx, _rows({"every": "hourly", "run": "nightly"}))

    assert out.startswith("error:")
    assert not await ctx.context.files.exists(ctx.context.investigation_id, SCHEDULES)


async def test_a_workflow_cannot_take_the_schedules_files_name(clock) -> None:
    """`save_workflow("Schedules")` slugified to `schedules` and wrote
    `.workflows/schedules.json` — over the item's schedules. Every schedule was
    silently cancelled, the new workflow was unrunnable (the read side reserves
    the name) and absent from the panel, and the index pointed at a file the
    sweep reports as "has no `schedules` list". The read side reserving a name
    the write side still hands out is half a rule."""
    from workspace_app.agent.tools import save_workflow_impl

    ctx = _ctx()
    await _with_workflow(ctx)
    await save_schedules_impl(ctx, _rows({"every": "hourly", "run": "nightly"}))
    before = await ctx.context.files.read(ctx.context.investigation_id, SCHEDULES)

    out = await save_workflow_impl(ctx, "Schedules", _WORKFLOW.decode())

    assert out.startswith("error:") and "save_schedules" in out
    after = await ctx.context.files.read(ctx.context.investigation_id, SCHEDULES)
    assert after == before, "the schedules file was overwritten"


async def test_no_workspace_on_this_turn_is_an_error() -> None:
    out = await save_schedules_impl(RunContextWrapper(AgentToolContext()), _rows())
    assert out.startswith("error:")
