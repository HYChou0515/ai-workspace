"""The period the page analyses (docs/plan-capacity-usage.md §9.10, E7): N days is
the N whole local days before today plus today so far, so a view that needs N
days of data can be ready in an N-day period."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from workspace_app.capacity.analysis.period import Period


def _ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


@pytest.mark.parametrize("days", [7, 28, 90])
def test_a_period_of_n_days_holds_n_whole_days_and_today_so_far(days: int):
    tz = ZoneInfo("Asia/Taipei")
    now = dt.datetime(2026, 10, 16, 14, 37, 12, tzinfo=tz)

    p = Period.ending(_ms(now), days=days, step_s=300, lag_s=60, tz=tz)

    assert len(p.full_days) == days
    assert p.days[0] == now.date() - dt.timedelta(days=days)
    assert p.days[-1] == now.date()
    assert p.end_ms == _ms(dt.datetime(2026, 10, 16, 14, 35, tzinfo=tz))


def test_the_period_starts_on_the_slot_grid_in_a_half_hour_zone():
    """Local midnight in a +05:30 zone is not on an hourly slot boundary; the
    period starts on the slot that holds it, and every day is still whole."""
    tz = ZoneInfo("Asia/Kolkata")
    now = dt.datetime(2026, 10, 16, 9, 10, tzinfo=tz)

    p = Period.ending(_ms(now), days=28, step_s=3600, lag_s=0, tz=tz)

    assert p.start_ms % 3_600_000 == 0
    assert len(p.full_days) == 28
