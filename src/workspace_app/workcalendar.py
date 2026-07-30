"""#615 P1: the off-hours calendar — "may an unattended agent work right now?"

One question, one entry point: :meth:`OffHoursCalendar.is_offhours`. Everything
it has to get right — a window that wraps past midnight, a real work calendar,
a timezone that is NOT the container's — lives behind it, so callers never
assemble that answer themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Sentinel for "the tz database does not know this name" — distinct from None,
# which legitimately means "no zone configured, use the local wall clock".
_UNKNOWN = object()

# How far `stretch_id` walks back over consecutive non-working days. Longer
# than any real holiday, short enough that a misconfigured calendar (nothing
# is a working day) terminates instead of looping.
_MAX_STRETCH_DAYS = 30


def _parse_window(value: str) -> tuple[time, time] | None:
    """``"19:00-08:00"`` → ``(19:00, 08:00)``. ``None`` for anything unusable —
    unset, malformed, or a zero-length window — which reads as "off-hours
    autonomy is disabled" rather than crashing a sweeper on a config typo."""
    start, sep, end = value.partition("-")
    if not sep:
        return None
    try:
        lo, hi = time.fromisoformat(start.strip()), time.fromisoformat(end.strip())
    except ValueError:
        return None
    return None if lo == hi else (lo, hi)


@dataclass(frozen=True)
class OffHoursCalendar:
    """When unattended work is allowed. ``window=""`` disables it entirely."""

    window: str = ""
    """``"HH:MM-HH:MM"`` in ``timezone``. Wrapping past midnight is the normal
    case (``"19:00-08:00"``), not an edge case."""

    timezone: str = ""
    """IANA zone name (``"Asia/Taipei"``). Deliberately NOT the container's
    local time: the two k8s CronJobs already pin a zone, and a schedule that
    silently moves when the base image changes is a bug nobody will trace."""

    workdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    """Which weekdays are working days, Monday=0 … Sunday=6."""

    overrides: Mapping[str, str] = field(default_factory=dict)
    """``"YYYY-MM-DD" -> "off" | "work"``, and it has to work BOTH ways. A
    holiday list alone is not enough here: Taiwan's flexible holidays create
    make-up workdays, where a Saturday is a working day — the one case a plain
    weekday rule gets exactly backwards, sending an unattended agent into a
    chat while its owner sits in the office."""

    @property
    def enabled(self) -> bool:
        """Is unattended off-hours work configured at all? False when the window
        is unset/malformed or the zone name is unknown — see `is_offhours`."""
        return _parse_window(self.window) is not None and self._zone() is not _UNKNOWN

    def _zone(self) -> tzinfo | None | object:
        """The calendar's zone: ``None`` for "server local wall clock" (an unset
        name), or `_UNKNOWN` for a name this machine's tz database rejects."""
        if not self.timezone:
            return None
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return _UNKNOWN

    def is_workday(self, day: date) -> bool:
        """Is ``day`` a working day? An override wins over the weekday rule."""
        override = self.overrides.get(day.isoformat())
        if override is not None:
            return override == "work"
        return day.weekday() in self.workdays

    def stretch_id(self, now: datetime) -> str:
        """A stable name for the CONTIGUOUS off-hours stretch ``now`` falls in —
        the date its first evening began, e.g. ``"2026-07-31"``.

        This is the sweeper's cluster-wide claim key, so it must not change
        while the office stays empty. A whole weekend (Friday 19:00 through
        Monday 08:00) is ONE stretch, and so is a four-day holiday: if each
        calendar day were its own key, a goal would be restarted every morning
        and spend its whole budget by Sunday. The walk back is bounded — a
        deployment that never works again is not a case worth looping over."""
        moment = self._localized(now)
        span = _parse_window(self.window)
        day = moment.date()
        # The morning tail of a wrapping window belongs to the previous evening.
        if (
            span is not None
            and span[0] > span[1]
            and moment.time() < span[1]
            and self.is_workday(day)
        ):
            day -= timedelta(days=1)
        # Whole non-working days belong to the stretch that opened before them.
        for _ in range(_MAX_STRETCH_DAYS):
            if self.is_workday(day):
                break
            day -= timedelta(days=1)
        return day.isoformat()

    def _localized(self, now: datetime) -> datetime:
        """``now`` read in this calendar's zone (unchanged when it is naive, or
        when no zone is configured — then it is already local wall clock)."""
        zone = self._zone()
        if zone is _UNKNOWN or zone is None or now.tzinfo is None:
            return now
        assert isinstance(zone, tzinfo)  # narrow for ty
        return now.astimezone(zone)

    def is_offhours(self, now: datetime) -> bool:
        """Is ``now`` inside the off-hours window?"""
        span = _parse_window(self.window)
        zone = self._zone()
        if span is None or zone is _UNKNOWN:
            return False
        # An aware instant is read in the CALENDAR's zone, not the container's —
        # a schedule that moves when the base image changes is a bug nobody
        # would think to look for.
        now = self._localized(now)
        if not self.is_workday(now.date()):
            return True  # nobody is in the office — the whole day is available
        lo, hi = span
        moment = now.time()
        if lo <= hi:
            return lo <= moment < hi
        # Wraps past midnight: inside means "after the evening edge OR before
        # the morning one" — the union of two spans, not their intersection.
        return moment >= lo or moment < hi
