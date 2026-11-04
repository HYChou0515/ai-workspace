"""Whom a turn's `save_schedules` credits (docs/plan-schedule-run-as.md G2).

A row the agent adds runs as the person the turn ACTS FOR — the one whose
values it carries. Each test walks the real door the app composed and reads the
context the runner was handed, like the tool-usage surface tests do.
"""

from __future__ import annotations

from tests.api.test_tool_grant_doors import _build, _chat_ctx
from tests.api.test_tool_usage_surfaces import _run
from workspace_app.workflow.run_identity import RunIdentities


async def test_a_chat_turn_acts_for_the_person_who_sent_the_message(monkeypatch) -> None:  # noqa: ANN001
    spec, builder, _, _, iid = _build(monkeypatch, {})

    ctx = await _chat_ctx(spec, builder, iid)

    assert ctx.acting_for == "u"
    assert ctx.schedule_authorship is not None


async def test_a_scheduled_run_nobody_bound_acts_for_nobody_not_the_owner(monkeypatch) -> None:  # noqa: ANN001
    """It is billed to the owner (`captured_user`), but the owner never asked
    for that schedule — a row its agent saves must not run with their sign-ins."""
    spec, _, executor, runner, iid = _build(monkeypatch, {})
    run_id = _run(spec, iid, from_schedule=True)

    await executor.drive_turn(iid, "no-such-chat", "owner", "hello", None, run_id=run_id)

    ctx = runner.ctxs[-1]
    assert ctx.acting_for == ""
    assert ctx.schedule_authorship is not None


async def test_a_run_acts_for_whoever_it_runs_as(monkeypatch) -> None:  # noqa: ANN001
    spec, _, executor, runner, iid = _build(monkeypatch, {})
    run_id = _run(spec, iid, from_schedule=True)
    RunIdentities(spec).record(
        run_id,
        "carol",
        verb="execute",
        item_id=iid,
        profile="default",
        workflow_id="w",
        workflow_digest="d",
    )

    await executor.drive_turn(iid, "no-such-chat", "owner", "hello", None, run_id=run_id)

    assert runner.ctxs[-1].acting_for == "carol"
