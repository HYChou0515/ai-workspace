"""`next_run` — when the sweep fires a schedule next, by the rule `is_due` fires it.

Three answers, and the middle one is the point: still ahead in this period →
that target; this period's target passed and the window has NOT fired → `None`,
meaning the very next sweep (a missed window fires late); already fired this
window → the next period's target.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from workspace_app.workflow.triggers import Schedule, fire_window, is_due, next_run

TUE = datetime(2026, 9, 15, 10, 0)  # a Tuesday


def test_still_ahead_in_this_period_is_this_periods_target() -> None:
    s = Schedule(every="daily", at="18:00")
    assert next_run(s, TUE, "") == datetime(2026, 9, 15, 18, 0)


def test_due_and_not_fired_means_the_next_sweep() -> None:
    s = Schedule(every="daily", at="09:00")
    assert next_run(s, TUE, "") is None
    assert is_due(s, TUE, ""), "None must mean exactly what is_due means"


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        (Schedule(every="daily", at="09:00"), datetime(2026, 9, 16, 9, 0)),
        # `dow: mon` — this ISO week's Monday has passed; next Monday.
        (Schedule(every="weekly", dow="mon", at="08:00"), datetime(2026, 9, 21, 8, 0)),
        # Day 1 of this month has passed; the first of next month.
        (Schedule(every="monthly", dom=1, at="07:30"), datetime(2026, 10, 1, 7, 30)),
        # Sub-daily periods are buckets: the next bucket starts when this one ends.
        (Schedule(every="hourly"), datetime(2026, 9, 15, 11, 0)),
        (Schedule(every="minutes:15"), datetime(2026, 9, 15, 10, 15)),
    ],
)
def test_already_fired_this_window_means_the_next_periods_target(
    schedule: Schedule, expected: datetime
) -> None:
    fired = fire_window(schedule, TUE)
    assert next_run(schedule, TUE, fired) == expected


def test_a_monthly_day_past_the_months_end_clamps_next_month_too() -> None:
    # Fired for September (day 31 clamps to the 30th); October has a 31st.
    s = Schedule(every="monthly", dom=31, at="07:00")
    now = datetime(2026, 9, 30, 8, 0)
    assert next_run(s, now, fire_window(s, now)) == datetime(2026, 10, 31, 7, 0)


def test_a_monthly_row_saved_in_december_rolls_the_year() -> None:
    s = Schedule(every="monthly", dom=1, at="07:00")
    now = datetime(2026, 12, 15, 8, 0)
    assert next_run(s, now, fire_window(s, now)) == datetime(2027, 1, 1, 7, 0)
