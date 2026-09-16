"""`schedule_views` and its helpers — the one reading of a schedules file that
the agent's reply and the panel share. The parity test holds it to the sweep
end to end; this file pins the pieces the parity cases do not reach."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from workspace_app.resources import make_spec
from workspace_app.workflow import user_schedules
from workspace_app.workflow.triggers import register_trigger_store
from workspace_app.workflow.user_schedules import (
    UserSchedule,
    describe_row,
    file_rows,
    last_window_lookup,
    parse_row,
    schedule_views,
)

NOW = datetime(2026, 9, 15, 2, 0)  # Tuesday 10:00 Taipei


@pytest.mark.parametrize(
    ("row", "words"),
    [
        (UserSchedule(every="minutes", n=15), "every 15 minutes (UTC)"),
        (UserSchedule(every="hourly", tz="Asia/Taipei"), "hourly (Asia/Taipei)"),
        (UserSchedule(every="daily", at="09:00"), "daily at 09:00 UTC"),
        (UserSchedule(every="weekly", dow="mon", at="08:00"), "weekly on mon at 08:00 UTC"),
        (UserSchedule(every="monthly", dom=1, at="07:30"), "monthly on day 1 at 07:30 UTC"),
    ],
)
def test_every_period_reads_as_words(row: UserSchedule, words: str) -> None:
    assert describe_row(row) == words


def test_file_rows_answers_none_for_every_shape_that_is_not_a_schedules_list() -> None:
    assert file_rows("{not json") is None
    assert file_rows("[1, 2]") is None
    assert file_rows('{"schedules": {"a": 1}}') is None
    assert file_rows('{"schedules": []}') == []


def test_a_row_the_linter_passes_but_the_parser_refuses_costs_its_row_only(monkeypatch) -> None:
    """The belt behind the linter's braces. Every known case is linted; this
    keeps the next unknown one from taking the file's good rows with it."""

    def _boom(_raw: str) -> list[UserSchedule]:
        raise ValueError("a shape the linter did not foresee")

    monkeypatch.setattr(user_schedules, "parse_user_schedules", _boom)

    row, problems = parse_row(3, {"every": "hourly", "run": "x"})

    assert row is None
    assert problems == ["schedules[3]: could not be read (a shape the linter did not foresee)."]


def test_a_ledger_that_cannot_be_read_reads_as_never_fired(monkeypatch) -> None:
    """A listing, not a run: a broken ledger must not take the panel down, and
    "never fired" is the truthful fallback for a file whose window nobody can
    confirm."""
    spec = make_spec()
    register_trigger_store(spec)
    lookup = last_window_lookup(spec, "item-1")

    from workspace_app.workflow import triggers

    monkeypatch.setattr(
        triggers.SpecstarTriggerStore,
        "last_window",
        lambda self, key: (_ for _ in ()).throw(RuntimeError("ledger down")),
    )

    assert lookup(UserSchedule(every="hourly", run="x")) == ""


def test_the_ledger_is_asked_once_per_distinct_row(monkeypatch) -> None:
    """Two identical rows are one trigger key (the sweep collapses them too), so
    a file of duplicates costs one ledger read, not one per row."""
    spec = make_spec()
    register_trigger_store(spec)
    lookup = last_window_lookup(spec, "item-1")
    from workspace_app.workflow import triggers

    reads: list[str] = []
    original = triggers.SpecstarTriggerStore.last_window

    def _counting(self: triggers.SpecstarTriggerStore, trigger_id: str) -> str:
        reads.append(trigger_id)
        return original(self, trigger_id)

    monkeypatch.setattr(triggers.SpecstarTriggerStore, "last_window", _counting)
    row = UserSchedule(every="hourly", run="x")
    assert lookup(row) == lookup(row) == ""
    assert len(reads) == 1


def test_over_the_cap_a_well_formed_row_keeps_its_own_record_clean() -> None:
    """The cap is the FILE's problem. Writing it onto every row made the panel
    call each well-formed row "written wrong" and drop its workflow title, and
    repeat the sentence once per row. `runnable` carries the verdict; the
    file-level problem says why."""
    raw = json.dumps({"schedules": [{"every": "hourly", "run": "x"}] * 3})

    views, problems = schedule_views(
        raw, offered=["x"], now_utc=NOW, last_window=lambda _r: "", max_rows=2
    )

    assert [v.runnable for v in views] == [False, False, False]
    assert [v.problems for v in views] == [[], [], []]
    assert [v.run for v in views] == ["x", "x", "x"]
    assert problems and "over the limit of 2" in problems[0]


def test_a_row_naming_a_workflow_that_wont_parse_is_known_but_not_runnable() -> None:
    """The item HAS the file (so not "no such workflow"), and it will not run:
    the row carries the file's problem, so a person can fix the workflow rather
    than hunt for a typo in the schedule."""
    raw = json.dumps({"schedules": [{"every": "hourly", "run": "x"}]})

    views, _ = schedule_views(
        raw,
        offered=["x"],
        now_utc=NOW,
        last_window=lambda _r: "",
        broken={"x": "steps[0]: `cache` is required"},
    )

    assert views[0].known is True
    assert views[0].runnable is False and views[0].next_run == ""
    assert "`cache` is required" in views[0].run_problem
    assert views[0].problems == []  # the ROW is fine; the workflow is not


def test_a_deployment_with_the_sweep_off_makes_no_row_runnable() -> None:
    raw = json.dumps({"schedules": [{"every": "hourly", "run": "x"}]})

    views, _ = schedule_views(
        raw, offered=["x"], now_utc=NOW, last_window=lambda _r: "", enabled=False
    )

    assert views[0].runnable is False
    assert views[0].next_run == "" and views[0].next_at == "" and views[0].due_now is False
