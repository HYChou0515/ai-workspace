"""Schedules a PAGE declares, as opposed to ones an engineer writes.

The profile's `triggers.json` is authored once by whoever builds the app. This
is the other half: a file a WUI writes into its own folder, so a domain expert
can say "every weekday at 09:00, build my report" without anyone editing the
repo.

Two properties carry the whole design:

* **The declaration is data and the state is the platform's.** A page writes the
  file with `writeFile`, which REPLACES — so pressing save five times is one
  schedule, not five, and there is no idempotency key to get wrong.
* **The lease key comes from the CONTENT, never from an id the page chose.** A
  page that regenerates a random id on every save would otherwise look like a
  brand-new schedule each time, and the window ledger would reset — firing again
  for a window it had already fired for, and sending the mail twice.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from workspace_app.workflow.triggers import fire_window, is_due, period_target
from workspace_app.workflow.user_schedules import (
    UserSchedule,
    parse_user_schedules,
    trigger_id_for,
    validate_user_schedules,
)

ITEM = "rca:i1"
FOLDER = "/scrap-review"


def _file(*rows: dict) -> str:
    import json

    return json.dumps({"schedules": list(rows)})


DAILY = {"every": "daily", "at": "09:00", "run": "build-report", "with": {"line": "A"}}
POLLER = {"every": "minutes", "n": 5, "run": "check-arrivals"}


# ── the format ───────────────────────────────────────────────────────────────


def test_reads_the_rows_a_page_wrote():
    rows = parse_user_schedules(_file(DAILY, POLLER))

    assert [r.run for r in rows] == ["build-report", "check-arrivals"]
    assert rows[0].at == "09:00"
    assert rows[0].payload == {"line": "A"}
    assert rows[1].n == 5


def test_an_unreadable_file_is_a_problem_not_a_crash():
    """A page writes this file, and a page is written by an LLM. A malformed one
    must report itself the way a lint does — the same contract `validate_triggers`
    already keeps — because raising here would take down the sweep for every
    OTHER item too."""
    problems = validate_user_schedules("not json at all")

    assert problems and "could not be read" in problems[0].lower()


def test_a_row_missing_its_workflow_is_named_not_dropped():
    problems = validate_user_schedules(_file({"every": "daily", "at": "09:00"}))

    assert any("run" in p for p in problems)


def test_a_bad_period_is_named_with_what_is_allowed():
    """The reader cannot open a console. "invalid" tells them nothing; the list
    of words that work tells them everything."""
    problems = validate_user_schedules(_file({**DAILY, "every": "fortnightly"}))

    assert any("fortnightly" in p and "daily" in p for p in problems)


def test_minutes_needs_a_count_and_the_others_must_not_have_one():
    assert any("n" in p for p in validate_user_schedules(_file({"every": "minutes", "run": "x"})))
    assert any("n" in p for p in validate_user_schedules(_file({**DAILY, "n": 5})))


def test_a_minutes_interval_the_hour_cannot_hold_is_refused():
    """The bucket is `(minute // n) * n`, anchored to the top of each hour. That
    is exact for a divisor of 60 and a lie for anything else:

    * `n: 90` → `minute // 90` is always 0, so "every 90 minutes" fires HOURLY.
      So does `n: 1440`, which is how somebody spells "daily" in minutes.
    * `n: 7` → buckets at :00 :07 … :56, and the last one is four minutes long,
      so "every 7 minutes" is nine fires an hour rather than eight and a half.

    Neither says anything. The page gets a cadence it did not ask for and the
    only way to notice is to watch a clock, so this is refused at the door with
    the alternative named.
    """
    # `n: 60` is NOT in this list: it divides 60, so its bucket is the top of
    # each hour and "every 60 minutes" means exactly what it says. Redundant
    # with `every: hourly`, not wrong.
    for bad in (90, 1440, 7, 45):
        problems = validate_user_schedules(_file({"every": "minutes", "n": bad, "run": "x"}))
        assert problems, f"n={bad} was accepted"
        assert "60" in problems[0], f"n={bad} was refused without saying what would work"


def test_every_interval_that_divides_the_hour_is_accepted():
    """The positive control. A rule that refused every `n` would pass the test
    above and delete the feature."""
    for good in (1, 2, 3, 5, 10, 15, 20, 30):
        assert validate_user_schedules(_file({"every": "minutes", "n": good, "run": "x"})) == [], (
            f"n={good} should be fine"
        )


def test_a_zone_that_is_not_a_zone_is_refused():
    """Nothing validated `tz` at all, so a typo reached `ZoneInfo` — which raises
    `ValueError` for an absolute path or a traversal, not the
    `ZoneInfoNotFoundError` the sweep catches. One bad zone took the WHOLE file
    down and the valid rows in it never fired, which is the exact thing this
    file's per-row linting exists to prevent."""
    for bad in ("/absolute", "../x", "Not/A/Zone", "\x00"):
        problems = validate_user_schedules(_file({**DAILY, "tz": bad}))
        assert problems, f"tz={bad!r} was accepted"
        assert "tz" in problems[0], f"tz={bad!r} was refused without naming the field"


def test_a_real_zone_is_accepted():
    """The control. Refusing every zone would pass the test above and break every
    schedule that names one."""
    for good in ("Asia/Taipei", "UTC", "Europe/Berlin", ""):
        assert validate_user_schedules(_file({**DAILY, "tz": good})) == [], good


def test_a_valid_file_has_nothing_to_say():
    assert validate_user_schedules(_file(DAILY, POLLER)) == []


# ── the lease key ────────────────────────────────────────────────────────────


def test_the_same_declaration_always_gets_the_same_key():
    """The reported failure this prevents: a page regenerates a random row id on
    every save, so the same logical schedule looks new, the window ledger resets,
    and it fires a second time for a day it had already fired for."""
    a = parse_user_schedules(_file(DAILY))[0]
    b = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, a) == trigger_id_for(ITEM, FOLDER, b)


def test_two_identical_rows_in_one_file_are_one_schedule():
    """Nothing wants to run the same thing twice at the same moment. Colliding on
    the key means the CAS lease lets exactly one of them through — duplication
    becomes structurally impossible rather than something a rule has to police."""
    rows = parse_user_schedules(_file(DAILY, DAILY))

    assert trigger_id_for(ITEM, FOLDER, rows[0]) == trigger_id_for(ITEM, FOLDER, rows[1])


@pytest.mark.parametrize(
    "change",
    [
        {"at": "10:00"},  # when
        {"run": "other-report"},  # what
        {"with": {"line": "B"}},  # with what
        {"every": "weekly", "dow": "mon"},  # how often
        {"tz": "Asia/Taipei"},  # 09:00 where
    ],
)
def test_changing_anything_that_matters_gets_a_new_key(change: dict):
    """Each of these is a different piece of work. Sharing a key with the old one
    would let the ledger say "already fired today" about something that has never
    run at all."""
    before = parse_user_schedules(_file(DAILY))[0]
    after = parse_user_schedules(_file({**DAILY, **change}))[0]

    assert trigger_id_for(ITEM, FOLDER, before) != trigger_id_for(ITEM, FOLDER, after)


def test_the_same_declaration_in_another_folder_is_another_schedule():
    """Two pages in one item may legitimately want the same report at the same
    time. They are different schedules and must not share a lease."""
    row = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, row) != trigger_id_for(ITEM, "/other", row)


def test_the_same_declaration_in_another_item_is_another_schedule():
    row = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, row) != trigger_id_for("rca:i2", FOLDER, row)


def test_the_key_ignores_the_order_of_the_payload():
    """`{a, b}` and `{b, a}` are the same payload. A key that disagreed would let
    a page fire twice by re-serialising its own file."""
    one = parse_user_schedules(_file({**DAILY, "with": {"a": 1, "b": 2}}))[0]
    two = parse_user_schedules(_file({**DAILY, "with": {"b": 2, "a": 1}}))[0]

    assert trigger_id_for(ITEM, FOLDER, one) == trigger_id_for(ITEM, FOLDER, two)


def test_the_key_says_where_it_came_from():
    """An operator reading the window ledger sees raw keys. One that names the
    item and the source is one they can act on; a bare hash is not."""
    row = parse_user_schedules(_file(DAILY))[0]

    assert trigger_id_for(ITEM, FOLDER, row).startswith(f"wui:{ITEM}:")


# ── finer than a day (Q7: the floor was removed) ─────────────────────────────


def test_a_minutes_schedule_buckets_by_its_own_interval():
    """A poller is the one thing that legitimately runs often, and it is always
    ONE row — the fan-out lives inside the workflow. So the window has to be able
    to be smaller than a day, or an arrival check can only run once a day."""
    row = UserSchedule(every="minutes", n=5, run="check-arrivals")

    at_02 = fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 2))
    at_04 = fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 4))
    at_07 = fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 7))

    assert at_02 == at_04  # same five-minute bucket
    assert at_02 != at_07  # the next one


def test_an_hourly_schedule_buckets_by_the_hour():
    row = UserSchedule(every="hourly", run="check-arrivals")

    assert fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 2)) != fire_window(
        row.as_schedule(), datetime(2026, 9, 5, 10, 2)
    )


def test_a_daily_schedule_still_buckets_by_the_day():
    """The existing vocabulary must not move. `window_key` is shared with
    `send_notification`'s per-window fingerprint, so a "daily" schedule and a
    "daily" notify have to keep bucketing identically."""
    row = UserSchedule(every="daily", at="09:00", run="build-report")

    assert fire_window(row.as_schedule(), datetime(2026, 9, 5, 9, 30)) == "2026-09-05"


def test_a_sub_daily_period_targets_its_own_bucket_not_midnight():
    """Asserted on `period_target` directly, because `is_due` cannot see it: the
    default `at` is 00:00, which is always already past, so a branch returning
    "today at 00:00" and one returning the bucket's start both make `is_due`
    say yes. A mutation proved the point — replacing this branch changed no test
    until this one existed."""
    row = UserSchedule(every="minutes", n=5, run="check-arrivals").as_schedule()

    assert period_target(row, datetime(2026, 9, 5, 9, 7)) == datetime(2026, 9, 5, 9, 5)
    assert period_target(
        UserSchedule(every="hourly", run="x").as_schedule(), datetime(2026, 9, 5, 9, 7)
    ) == datetime(2026, 9, 5, 9, 0)


def test_a_wall_time_on_a_repeating_period_is_refused():
    """`every: minutes` plus `at: 09:00` is two answers to one question. Refused
    at declaration, so `period_target` has exactly one meaning for these."""
    problems = validate_user_schedules(
        _file({"every": "minutes", "n": 5, "run": "x", "at": "09:00"})
    )

    assert any("at" in p for p in problems)


def test_a_poller_is_due_again_in_the_next_bucket_but_not_the_same_one():
    row = UserSchedule(every="minutes", n=5, run="check-arrivals").as_schedule()
    now = datetime(2026, 9, 5, 9, 2)
    window = fire_window(row, now)

    assert is_due(row, now, last_window="") is True
    assert is_due(row, now, last_window=window) is False
    assert is_due(row, datetime(2026, 9, 5, 9, 7), last_window=window) is True
