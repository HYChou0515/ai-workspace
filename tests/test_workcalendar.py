"""#615 P1: the off-hours calendar — is this instant inside the off-hours window?

Behaviour-level tests only: everything goes through `OffHoursCalendar.is_offhours`,
the one question every caller actually asks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from workspace_app.workcalendar import OffHoursCalendar

TAIPEI = "Asia/Taipei"


def _at(text: str) -> datetime:
    """A naive local wall-clock instant, e.g. `_at("2026-07-30 14:00")`."""
    return datetime.fromisoformat(text)


def test_workday_office_hours_are_not_offhours() -> None:
    # Thursday 2026-07-30, 14:00 — the middle of a normal working day.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    assert cal.is_offhours(_at("2026-07-30 14:00")) is False


def test_non_workday_is_offhours_all_day() -> None:
    # Saturday 2026-08-01, 10:00 — nobody is in the office, so the whole day is
    # available even though 10:00 falls outside the evening window.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    assert cal.is_offhours(_at("2026-08-01 10:00")) is True


def test_makeup_workday_saturday_is_office_hours() -> None:
    # The case a plain weekday rule cannot express, and the reason this is a
    # calendar and not a weekend flag: Taiwan's flexible holidays turn some
    # Saturdays into working days. People ARE in the office, so an unattended
    # agent must not barge into their chat.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI, overrides={"2026-08-01": "work"})
    assert cal.is_offhours(_at("2026-08-01 10:00")) is False


def test_aware_instant_is_judged_in_the_calendars_zone() -> None:
    # 2026-07-30 12:00 UTC is 20:00 in Taipei — inside the window. Read as a
    # naive UTC wall clock it would be 12:00, i.e. office hours, so this only
    # passes if the instant was actually converted. The container's own TZ must
    # never be what decides this (both k8s CronJobs already pin a zone).
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    assert cal.is_offhours(datetime(2026, 7, 30, 12, 0, tzinfo=UTC)) is True


def test_unknown_timezone_disables_rather_than_guessing() -> None:
    # A typo'd zone means we cannot know when off-hours is. Guessing would run
    # unattended work at the wrong hour; crashing would kill the sweeper every
    # tick. Refusing to run is the only honest option — and it is disclosed on
    # the wire, so the opt-in checkbox shows as unavailable instead of lying.
    # Both ways a zone name can be wrong: unknown to the tz database, and not
    # a key at all (an absolute path, which raises a different exception).
    for bad in ("Mars/Olympus_Mons", "/Asia/Taipei"):
        cal = OffHoursCalendar(window="19:00-08:00", timezone=bad)
        assert cal.enabled is False, bad
        assert cal.is_offhours(_at("2026-07-30 23:00")) is False, bad


def test_a_window_inside_one_day_does_not_wrap() -> None:
    # Not every deployment works nights: "13:00-14:00" means exactly that hour,
    # not "everything except it".
    cal = OffHoursCalendar(window="13:00-14:00", timezone=TAIPEI)
    assert cal.is_offhours(_at("2026-07-30 13:30")) is True
    assert cal.is_offhours(_at("2026-07-30 12:30")) is False
    assert cal.is_offhours(_at("2026-07-30 23:00")) is False


def test_window_wraps_past_midnight() -> None:
    # Both halves of "19:00-08:00" on working days: Thursday night, and the
    # Friday small hours that are really a continuation of it.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    assert cal.is_offhours(_at("2026-07-30 23:00")) is True
    assert cal.is_offhours(_at("2026-07-31 07:00")) is True


def test_window_edges_hand_the_day_back_at_the_morning_edge() -> None:
    # 19:00 is already off-hours; 08:00 is not — the morning edge is where the
    # workday resumes, and a turn still running at 08:00 is what "current turn
    # finishes, then stop" means.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    assert cal.is_offhours(_at("2026-07-30 19:00")) is True
    assert cal.is_offhours(_at("2026-07-30 08:00")) is False


def test_public_holiday_frees_a_weekday_entirely() -> None:
    # The other direction of the override: a weekday nobody is working.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI, overrides={"2026-07-30": "off"})
    assert cal.is_offhours(_at("2026-07-30 14:00")) is True


def test_zone_conversion_moves_the_DAY_not_just_the_clock() -> None:
    # Friday 02:00 UTC is Saturday 10:00 in Taipei, and that Saturday is a
    # make-up workday — so the honest answer is "office hours". Reading the
    # instant naively would land on Friday 02:00, i.e. inside the window, and
    # send an agent into an occupied office. Converting the CLOCK but not the
    # DATE is the classic half-fix; this pins both.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI, overrides={"2026-08-01": "work"})
    assert cal.is_offhours(datetime(2026, 7, 31, 2, 0, tzinfo=UTC)) is False


def test_unset_window_disables_off_hours_autonomy() -> None:
    # The default is OFF: a deploy that never configured a window must never
    # run unattended work, and the panel discloses that via `enabled`.
    cal = OffHoursCalendar()
    assert cal.enabled is False
    assert cal.is_offhours(_at("2026-08-01 03:00")) is False


def test_one_evening_and_its_morning_tail_are_the_same_stretch() -> None:
    # The sweeper claims a stretch once, cluster-wide, so the id must not change
    # at midnight — Thursday 23:00 and Friday 07:00 are one night off.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    assert cal.stretch_id(_at("2026-07-30 23:00")) == cal.stretch_id(_at("2026-07-31 07:00"))
    assert cal.stretch_id(_at("2026-07-30 19:00")) == "2026-07-30"


def test_a_whole_weekend_is_one_stretch_including_monday_morning() -> None:
    # Friday evening through Monday 08:00 is ONE uninterrupted stretch: nobody
    # comes back in between. If it split, a goal would be re-started each day
    # and burn its budget three times over.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI)
    friday_night = cal.stretch_id(_at("2026-07-31 20:00"))
    assert friday_night == "2026-07-31"
    for moment in ("2026-08-01 03:00", "2026-08-01 14:00", "2026-08-02 23:00", "2026-08-03 07:00"):
        assert cal.stretch_id(_at(moment)) == friday_night, moment
    # Monday evening is a NEW stretch — people were in the office in between.
    assert cal.stretch_id(_at("2026-08-03 19:00")) == "2026-08-03"


def test_a_long_holiday_is_one_stretch_and_a_makeup_day_breaks_it() -> None:
    cal = OffHoursCalendar(
        window="19:00-08:00",
        timezone=TAIPEI,
        overrides={"2026-07-30": "off", "2026-07-31": "off", "2026-08-01": "work"},
    )
    # Wed evening opens a stretch that swallows both holiday days…
    assert cal.stretch_id(_at("2026-07-29 20:00")) == "2026-07-29"
    assert cal.stretch_id(_at("2026-07-31 14:00")) == "2026-07-29"
    # …but the make-up Saturday is a working day, so its evening starts a new one.
    assert cal.stretch_id(_at("2026-08-01 20:00")) == "2026-08-01"


def test_a_calendar_with_no_working_days_still_terminates() -> None:
    # Somebody will eventually save an empty working week. The walk back looking
    # for the stretch's opening day must stop rather than spin the sweeper.
    cal = OffHoursCalendar(window="19:00-08:00", timezone=TAIPEI, workdays=())
    assert cal.is_offhours(_at("2026-07-30 14:00")) is True
    assert cal.stretch_id(_at("2026-07-30 14:00"))  # a value, not a hang


def test_malformed_window_disables_instead_of_crashing_the_sweeper() -> None:
    # A config typo must not take down a loop that runs every 60 seconds.
    for bad in ("19:00", "19:00-", "nineteen-eight", "19:00-19:00"):
        cal = OffHoursCalendar(window=bad, timezone=TAIPEI)
        assert cal.enabled is False, bad
        assert cal.is_offhours(_at("2026-07-30 23:00")) is False, bad
