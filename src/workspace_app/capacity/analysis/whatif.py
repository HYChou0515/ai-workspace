"""What N people would need (docs/plan-capacity-usage.md §9.6, E10).

Two-stage bootstrap over whole person-days (resampling hierarchical data:
Davison & Hinkley, *Bootstrap Methods and their Application*, 1997; the same
shape as the ICU-capacity bootstrap the plan cites): draw a day, keep that
day's real people, and add N − N(day) people drawn from the chosen pool —
everyone (zero days included: an N-person who did nothing that day is part of
the truth), the heaviest tenth, or the people of one App. Each line's day peak
is the maximum over its slots of the sum, plus what nobody's N drives (the
fixed part, other systems, `unknown`/`system`). Repeated `draws` times.

Every N reuses the same random draws — nested: the people drawn for N are the
first N − N(day) of one fixed sequence — so a larger N can never need less.
The sequence stretches when an N needs more of it, in blocks whose numbers
depend only on the seed and where they sit, so what was asked before changes
nothing. The cost is a matrix product per day, whatever N is.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .lines import Line
from .people import NOT_PEOPLE, People
from .period import Period

DRAWS = 400
#: How far beyond today's N the what-if and the headroom look.
MAX_GROWTH = 20
#: Columns the draw sequence stretches by at a time.
_BLOCK = 256


@dataclass
class _Day:
    day: int
    #: positions in `users` of the day's N who appear in the period
    present: np.ndarray
    absent: int
    #: line → [person, slot of day] demand of `present`
    demand: dict[str, np.ndarray]
    #: line → [slot of day] what nobody's N drives
    base: dict[str, np.ndarray]


def _slots_of_day(period: Period, d: int) -> np.ndarray:
    idx = np.flatnonzero(period.day_of == d)
    return idx[np.argsort(period.tod[idx], kind="stable")]


class Simulator:
    def __init__(
        self,
        lines: Sequence[Line],
        people: People,
        period: Period,
        *,
        days: Sequence[int],
        pool: Callable[[np.ndarray], np.ndarray] | None = None,
        draws: int = DRAWS,
        seed: int = 0,
    ) -> None:
        """`pool(present)` picks, from a day's present N (their indices into
        `people.users`), whom extra people are drawn like — as POSITIONS within
        `present` (None: everyone, absent people included)."""
        self.lines = [line for line in lines if line.per_user.size and line.resource != "disk"]
        self.fixed_lines = [
            line for line in lines if line not in self.lines and line.resource != "disk"
        ]
        self._pool = pool
        not_people = [i for i, u in enumerate(people.users) if u in NOT_PEOPLE]
        self._days: list[_Day] = []
        for d in days:
            slots = _slots_of_day(period, d)
            present = people.pool[d]
            demand, base = {}, {}
            for line in self.lines:
                demand[line.id] = line.per_user[present][:, slots]
                extra = line.fixed[slots] + (line.other[slots] if line.other is not None else 0.0)
                if not_people:
                    extra = extra + line.per_user[not_people][:, slots].sum(axis=0)
                base[line.id] = extra
            for line in self.fixed_lines:
                base[line.id] = np.nan_to_num(line.total[slots])
            self._days.append(_Day(d, present, int(people.absent[d]), demand, base))
        self.n_now = int(people.active_30d[days[-1]]) if days else 0
        self._seed = seed
        rng = np.random.default_rng(seed)
        self._which = rng.integers(0, len(self._days), draws) if self._days else np.zeros(0, int)
        longest = max((len(day.present) + day.absent for day in self._days), default=0)
        self._seq = rng.random((draws, max(1, MAX_GROWTH * max(longest, 1))))
        self._perm = [
            rng.permutation(len(self._days[w].present) + self._days[w].absent) for w in self._which
        ]

    @property
    def usable(self) -> bool:
        return bool(self._days)

    def _stretch(self, length: int) -> None:
        """Make the draw sequence at least `length` long. A block's numbers
        depend on the seed and the column it starts at, never on when it was
        added, so a stretched sequence is the same whatever was asked first."""
        while self._seq.shape[1] < length:
            at = self._seq.shape[1]
            block = np.random.default_rng([self._seed, at]).random((self._seq.shape[0], _BLOCK))
            self._seq = np.hstack([self._seq, block])

    def run(self, n: int) -> dict[str, np.ndarray]:
        """line → [draw] the day's peak with N = n."""
        peaks = {line.id: np.zeros(len(self._which)) for line in self.lines + self.fixed_lines}
        for k, day in enumerate(self._days):
            draws = np.flatnonzero(self._which == k)
            if not len(draws):
                continue
            size = len(day.present)
            everyone = size + day.absent
            counts = np.zeros((len(draws), size))
            for row, b in enumerate(draws):
                if n >= everyone:
                    counts[row] = 1.0
                    chosen = self._pool(day.present) if self._pool is not None else None
                    extra = n - everyone
                    if extra > self._seq.shape[1]:
                        self._stretch(extra)
                    if chosen is None:
                        picks = (self._seq[b, :extra] * everyone).astype(np.int64)
                        picks = picks[picks < size]  # an absent person adds nothing
                    elif len(chosen):
                        picks = chosen[(self._seq[b, :extra] * len(chosen)).astype(np.int64)]
                    else:
                        picks = np.zeros(0, np.int64)
                    counts[row] += np.bincount(picks, minlength=size)
                else:
                    keep = self._perm[b][:n]
                    counts[row, keep[keep < size]] = 1.0
            for line in self.lines:
                total = counts @ day.demand[line.id] + day.base[line.id]
                peaks[line.id][draws] = total.max(axis=1)
            for line in self.fixed_lines:
                peaks[line.id][draws] = day.base[line.id].max()
        return peaks


def quantiles(peaks: dict[str, np.ndarray]) -> dict[str, tuple[float, float]]:
    return {k: (float(np.quantile(v, 0.5)), float(np.quantile(v, 0.95))) for k, v in peaks.items()}


@dataclass
class Headroom:
    n_now: int
    #: the most people every line still fits at p95 — None when no line has a limit
    max_n: int | None
    #: the first line to stop fitting past `max_n`
    binding: str | None
    #: line → the most people that line alone fits
    by_line: dict[str, int]


def headroom(sim: Simulator, limits: dict[str, float]) -> Headroom:
    """The largest N whose p95 day peak stays under each line's limit (capacity ×
    safe threshold), found by doubling then bisecting — sound because a larger N
    never needs less (nested draws)."""
    ids = [k for k in limits if any(line.id == k for line in sim.lines + sim.fixed_lines)]
    if not sim.usable or not ids:
        return Headroom(sim.n_now, None, None, {})
    cache: dict[int, dict[str, float]] = {}

    def p95(n: int) -> dict[str, float]:
        if n not in cache:
            peaks = sim.run(n)
            cache[n] = {k: float(np.quantile(peaks[k], 0.95)) for k in ids}
        return cache[n]

    def fits(n: int, which: list[str]) -> bool:
        got = p95(n)
        return all(got[k] <= limits[k] for k in which)

    ceiling = max(1, sim.n_now) * MAX_GROWTH

    def largest(which: list[str]) -> int:
        if not fits(0, which):
            return 0
        lo, hi = 0, max(1, sim.n_now)
        while hi <= ceiling and fits(hi, which):
            lo, hi = hi, hi * 2
        if hi > ceiling:
            return ceiling
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if fits(mid, which) else (lo, mid)
        return lo

    by_line = {k: largest([k]) for k in ids}
    max_n = min(by_line.values())
    binding = min(by_line, key=lambda k: by_line[k])
    return Headroom(sim.n_now, max_n, binding, by_line)
