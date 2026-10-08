"""Cron rows in a schedules file (`docs/plan-schedule-cron.md`): one row for
what took several `every` rows — read, linted, identified and fired by the same
code the `every` rows go through.

`now` is pinned to Thursday 2026-10-08 00:30 UTC (08:30 in Taipei).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from workspace_app.workflow.triggers import fire_window
from workspace_app.workflow.user_schedules import (
    UserSchedule,
    describe_row,
    in_zone,
    parse_row,
    schedule_views,
    trigger_id_for,
    validate_user_schedules,
)

NOW = datetime(2026, 10, 8, 0, 30)  # naive UTC, as the sweep holds it
TPE = "Asia/Taipei"


def _file(*rows: dict) -> str:
    return json.dumps({"schedules": list(rows)})


def _ms(y: int, mo: int, d: int, hh: int, mm: int) -> int:
    return int(datetime(y, mo, d, hh, mm, tzinfo=UTC).timestamp() * 1000)


def _view(row: dict, *, last: str = "", landed: int | None = 0):
    views, problems = schedule_views(
        _file(row),
        offered={"w"},
        now_utc=NOW,
        last_window=lambda _r: last,
        landed_ms=landed,
    )
    assert problems == []
    (view,) = views
    return view


# --- the row ------------------------------------------------------------------


def test_a_cron_row_is_read_with_its_zone():
    row, problems = parse_row(0, {"cron": "0 9 * * 1-5", "tz": "asia/taipei", "run": "w"})
    assert problems == []
    assert row is not None
    assert (row.cron, row.tz, row.run) == ("0 9 * * 1-5", TPE, "w")


@pytest.mark.parametrize(
    ("row", "said"),
    [
        ({"cron": "0 9 * * 1-5", "every": "daily", "run": "w"}, "either `cron` or `every`"),
        ({"cron": "0 9 * * *", "at": "09:00", "run": "w"}, "`at` does not apply to a `cron`"),
        ({"cron": "0 9 * * *", "dow": "mon", "run": "w"}, "`dow` does not apply to a `cron`"),
        ({"cron": "0 9 * *", "run": "w"}, "5 fields"),
        ({"cron": "0 0 9 * * 1-5", "run": "w"}, "5 fields"),
        ({"cron": "@daily", "run": "w"}, "5 fields"),
        ({"cron": "0 25 * * *", "run": "w"}, "not a cron expression"),
        ({"cron": 5, "run": "w"}, "must be text"),
    ],
)
def test_a_cron_the_sweep_cannot_follow_is_refused_with_why(row: dict, said: str):
    problems = validate_user_schedules(_file(row))
    assert any(said in p for p in problems), problems


def test_a_valid_cron_file_has_nothing_to_say():
    assert (
        validate_user_schedules(_file({"cron": "*/15 9-17 * * MON-FRI", "tz": TPE, "run": "w"}))
        == []
    )


# --- identity -------------------------------------------------------------------


def test_every_rows_keep_the_identity_they_had_before_cron_existed():
    # Computed on the code before cron (9ddda722): an existing schedule must
    # keep its ledger, its chat and its "run as me" binding.
    weekly = UserSchedule(run="w", every="weekly", dow="mon", at="08:30", tz=TPE, payload={"a": 1})
    minutes = UserSchedule(run="w", every="minutes", n=15)
    assert trigger_id_for("it", "/reports", weekly) == "wui:it:7c1f7028298f4272"
    assert trigger_id_for("it", "/reports", minutes) == "wui:it:c144a2b574e574ef"


def test_a_different_cron_is_a_different_schedule():
    a = UserSchedule(run="w", cron="0 9 * * 1-5", tz=TPE)
    b = UserSchedule(run="w", cron="0 9 * * 1-6", tz=TPE)
    assert trigger_id_for("it", "", a) != trigger_id_for("it", "", b)


# --- when it fires ----------------------------------------------------------------


def test_next_run_is_the_next_occurrence_in_the_rows_zone():
    # 08:30 Thursday in Taipei; the last occurrence (Wed 09:00) already fired.
    view = _view({"cron": "0 9 * * 1-5", "tz": TPE, "run": "w"}, last="cron:2026-10-07T09:00")
    assert view.runnable
    assert (view.next_at, view.due_now) == ("2026-10-08 09:00", False)
    assert view.next_ms == _ms(2026, 10, 8, 1, 0)


def test_a_mondays_cron_that_ran_this_monday_runs_next_monday():
    view = _view({"cron": "0 9 * * 1", "tz": TPE, "run": "w"}, last="cron:2026-10-05T09:00")
    assert (view.next_at, view.due_now) == ("2026-10-12 09:00", False)


def test_a_missed_occurrence_fires_once_late():
    # The sweep was down for Wednesday 09:00 (the ledger shows Tuesday's):
    # the latest occurrence fires on the next tick — once, not once per miss.
    row = {"cron": "0 9 * * 1-5", "tz": TPE, "run": "w"}
    view = _view(row, last="cron:2026-10-06T09:00")
    assert view.due_now
    # …and the fire claims Wednesday's occurrence — the window the ledger then
    # holds, so the next tick does not fire it again.
    parsed, _ = parse_row(0, row)
    assert parsed is not None
    assert fire_window(parsed.as_schedule(), in_zone(NOW, TPE)) == "cron:2026-10-07T09:00"


def test_a_cron_saved_after_its_moment_does_not_fire_for_it():
    # Saved Thursday 08:20 Taipei: Wednesday 09:00 passed before it existed.
    landed = _ms(2026, 10, 8, 0, 20)
    view = _view({"cron": "0 9 * * 1-5", "tz": TPE, "run": "w"}, landed=landed)
    assert (view.due_now, view.next_at) == (False, "2026-10-08 09:00")


def test_the_agent_hears_the_cron_and_its_zone():
    assert (
        describe_row(UserSchedule(run="w", cron="0 9 * * 1-5", tz=TPE))
        == "cron `0 9 * * 1-5` (Asia/Taipei)"
    )


# --- review round 1 -----------------------------------------------------------------


@pytest.mark.parametrize("cron", ["0 0 30 2 *", "0 0 31 4 *"])
def test_a_cron_that_never_comes_round_is_refused_not_a_crash(cron: str):
    """`0 0 30 2 *` parses — croniter calls it valid — and has no occurrence.
    Left to the sweep it raised out of the whole file, the good rows beside it
    included; refused here it costs its own row."""
    raw = _file({"cron": cron, "run": "w"}, {"every": "daily", "at": "09:00", "run": "w"})
    problems = validate_user_schedules(raw)
    assert any("never" in p for p in problems), problems
    views, _ = schedule_views(raw, offered={"w"}, now_utc=NOW, last_window=lambda _r: "")
    assert [v.runnable for v in views] == [False, True]


@pytest.mark.parametrize("left_out", [None, "", 0])
def test_a_cron_left_out_the_way_a_generator_writes_it_is_an_every_row(left_out):
    """The parser's `or` rule: `null`, `""` and `0` are "not written" — an
    `every` row, not a broken cron row."""
    row = {"every": "daily", "at": "09:00", "cron": left_out, "run": "w"}
    assert validate_user_schedules(_file(row)) == []


def test_crons_that_differ_only_in_spacing_are_one_schedule():
    a, _ = parse_row(0, {"cron": "0 9 * * 1-5", "run": "w"})
    b, _ = parse_row(1, {"cron": " 0  9 * *   1-5 ", "run": "w"})
    assert a is not None and b is not None
    assert b.cron == "0 9 * * 1-5"
    assert trigger_id_for("it", "", a) == trigger_id_for("it", "", b)
