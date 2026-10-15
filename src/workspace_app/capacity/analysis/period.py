"""The period an analysis covers, cut into slots (docs/plan-capacity-usage.md §9.2).

`days` whole local days before today, plus today so far, in slots of `step_s`
(E7): a view that needs N days of data can be ready in an N-day period. `end`
is now minus the ingest lag, floored to a slot. Every slot also knows its local
day and its place within that day (in `capacity.timezone`), which is what a
person's day is made of.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import cached_property
from zoneinfo import ZoneInfo

import numpy as np

#: A run of this many slots with nothing from a source is a gap (15 minutes at
#: 5-minute slots); one slot missing is a missed sample.
GAP_SLOTS = 3


@dataclass(frozen=True)
class Period:
    start_ms: int
    end_ms: int
    step_s: int
    tz: ZoneInfo

    @classmethod
    def ending(cls, now_ms: int, *, days: int, step_s: int, lag_s: int, tz: ZoneInfo) -> Period:
        """From local midnight `days` days before today to now − `lag_s`. It
        starts on the slot that holds that midnight: in a +05:30 zone with
        hourly slots, half an hour before it."""
        step_ms = step_s * 1000
        end = (now_ms - lag_s * 1000) // step_ms * step_ms
        today = dt.datetime.fromtimestamp(end / 1000, tz).date()
        first = dt.datetime.combine(today - dt.timedelta(days=days), dt.time(0), tzinfo=tz)
        start = int(first.timestamp() * 1000) // step_ms * step_ms
        return cls(start_ms=start, end_ms=end, step_s=step_s, tz=tz)

    @property
    def step_ms(self) -> int:
        return self.step_s * 1000

    @property
    def n(self) -> int:
        return (self.end_ms - self.start_ms) // self.step_ms

    @property
    def slots_per_day(self) -> int:
        return 86_400 // self.step_s

    def index(self, t_ms: int) -> int | None:
        """The slot `t_ms` falls in, or None outside the period."""
        if not self.start_ms <= t_ms < self.end_ms:
            return None
        return (t_ms - self.start_ms) // self.step_ms

    @cached_property
    def _local(self) -> list[dt.datetime]:
        return [
            dt.datetime.fromtimestamp((self.start_ms + i * self.step_ms) / 1000, self.tz)
            for i in range(self.n)
        ]

    @cached_property
    def days(self) -> list[dt.date]:
        """The local days the period touches, in order."""
        return sorted({d.date() for d in self._local})

    @cached_property
    def day_of(self) -> np.ndarray:
        """[slot] → index into `days`."""
        where = {d: i for i, d in enumerate(self.days)}
        return np.array([where[d.date()] for d in self._local], dtype=np.int64)

    @cached_property
    def tod(self) -> np.ndarray:
        """[slot] → its place within its local day (0 .. slots_per_day − 1)."""
        return np.array(
            [(d.hour * 3600 + d.minute * 60 + d.second) // self.step_s for d in self._local],
            dtype=np.int64,
        )

    @cached_property
    def full_days(self) -> list[int]:
        """Indices of the days the period holds whole, from the slot holding
        their local midnight to the next midnight — 23 or 25 hours on a day
        the clocks change. A partial day's peak is not a day's peak."""
        out = []
        for i, day in enumerate(self.days):
            start = _midnight_ms(day, self.tz)
            end = _midnight_ms(day + dt.timedelta(days=1), self.tz)
            if self.start_ms <= start // self.step_ms * self.step_ms and end <= self.end_ms:
                out.append(i)
        return out


def _midnight_ms(day: dt.date, tz: ZoneInfo) -> int:
    return int(dt.datetime.combine(day, dt.time(0), tzinfo=tz).timestamp() * 1000)
