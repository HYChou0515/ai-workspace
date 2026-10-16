"""Who counts as one of the N (docs/plan-capacity-usage.md §9.5, D12).

A person is one of the N on a day when they did anything in the 30 days up to
and including it — the records' activity is loaded from 30 days before the
period, so the first day's N is as complete as the last's. Until the records
themselves are 30 days old it is not: N then counts only the people seen so
far and rises with no one new, which `complete` marks. `unknown` and `system`
are not people: their use is kept, as a baseline nobody's N grows.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .observe import LOOKBACK_DAYS, Observations

NOT_PEOPLE = frozenset({"", "unknown", "system"})


@dataclass
class People:
    users: list[str]
    #: [day] how many people were active in the 30 days up to it.
    active_30d: np.ndarray
    #: [day] how many did anything that day.
    active_today: np.ndarray
    #: [day] the people of that day's N who appear in the period (indices into
    #: `users`), and how many more are in it with no use in the period at all.
    pool: list[np.ndarray]
    absent: np.ndarray
    #: [day] whether the 30 days up to it are all recorded; None: every day's are.
    complete: np.ndarray | None = None


def _day_bounds_ms(day: dt.date, tz: dt.tzinfo) -> tuple[int, int]:
    start = dt.datetime.combine(day, dt.time(0), tzinfo=tz)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def count_people(obs: Observations, users: list[str]) -> People:
    p = obs.period
    row = {u: i for i, u in enumerate(users)}
    n_days = len(p.days)
    active_30d = np.zeros(n_days, dtype=np.int64)
    active_today = np.zeros(n_days, dtype=np.int64)
    pool: list[np.ndarray] = []
    absent = np.zeros(n_days, dtype=np.int64)
    complete = np.ones(n_days, dtype=bool)
    used = np.zeros((len(users), p.n), dtype=bool)
    for (user, _app, _comp, _work), series in obs.work_user.items():
        if user in row:
            used[row[user], series.slots()] = True
    for (user, _app), m in obs.sandbox_user.items():
        if user in row:
            used[row[user], m.slots("cpu_s")] = True
    for d, day in enumerate(p.days):
        start, end = _day_bounds_ms(day, p.tz)
        since = end - LOOKBACK_DAYS * 86_400_000
        complete[d] = obs.recorded_since_ms is None or since >= obs.recorded_since_ms
        members = {
            u
            for u, days in obs.activity.items()
            if u not in NOT_PEOPLE and any(since <= t < end for t in days)
        }
        today = used[:, p.day_of == d].any(axis=1)
        members |= {users[i] for i in np.flatnonzero(today) if users[i] not in NOT_PEOPLE}
        active_30d[d] = len(members)
        active_today[d] = sum(1 for i in np.flatnonzero(today) if users[i] not in NOT_PEOPLE)
        present = np.array(sorted(row[u] for u in members if u in row), dtype=np.int64)
        pool.append(present)
        absent[d] = len(members) - len(present)
    return People(
        users, active_30d, active_today, pool, absent, None if complete.all() else complete
    )
