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


@pytest.mark.parametrize(("window_s", "step_s"), [(300, 300), (600, 600), (720, 720), (240, 240)])
def test_the_long_periods_slot_is_a_whole_number_of_windows(window_s: int, step_s: int):
    """The 90-day period widens its slot to bound memory; a slot holding one
    and two windows by turn read a steady load as 1.33 and 0.67 by turn."""
    from workspace_app.capacity.service import CapacityService
    from workspace_app.capacity.sources import CapacitySources
    from workspace_app.config.schema import CapacitySettings

    settings = CapacitySettings(window_s=window_s, step_s=step_s)
    step = CapacityService(CapacitySources(), settings).period(90).step_s

    assert step >= 900 and step % window_s == 0 and 86_400 % step == 0


@pytest.mark.parametrize(
    ("zone", "day"),
    [
        ("Europe/Berlin", dt.date(2026, 3, 29)),  # 23 hours
        ("Europe/Berlin", dt.date(2026, 10, 25)),  # 25 hours
        ("America/New_York", dt.date(2026, 3, 8)),
        ("America/New_York", dt.date(2026, 11, 1)),
    ],
)
def test_a_day_the_clocks_change_is_whole_when_the_period_holds_all_of_it(zone: str, day: dt.date):
    """A day is whole when the period holds it from its midnight to the next —
    23 hours on the day the clocks go forward. Counted against 24 hours of
    slots, that day was never whole: never a day's peak, never sized on."""
    tz = ZoneInfo(zone)
    now = dt.datetime.combine(day + dt.timedelta(days=3), dt.time(10), tzinfo=tz)

    p = Period.ending(_ms(now), days=7, step_s=300, lag_s=0, tz=tz)

    assert len(p.full_days) == 7
    assert day in [p.days[d] for d in p.full_days]
