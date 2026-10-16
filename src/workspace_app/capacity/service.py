"""The capacity analysis, on demand (docs/plan-capacity-usage.md §11, E6).

One analysis per period, built in a worker thread (never on the event loop),
one build at a time across periods (each holds a period's records in the API
process), and kept in memory for `TTL_S` — then let go, whether or not anyone
asks again. The build belongs to no request: everyone asking for the period
while it runs waits for the same one, and a request that goes away (a page
closed) neither cancels it for the others nor makes the next one start
another. Nothing is written anywhere (D3). The 90-day period is analysed at
15-minute slots or coarser, so three months of a large deployment stays
within memory.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Callable
from functools import partial
from zoneinfo import ZoneInfo

from ..config.schema import CapacitySettings
from .analysis.model import Analysis, analyse
from .analysis.observe import TooManyRows, observe
from .analysis.period import Period
from .sources import CapacitySources

#: The periods the page offers (E7).
PERIODS = (7, 28, 90)
TTL_S = 600.0
#: The coarsest slot the 90-day period is analysed at.
LONG_STEP_S = 900

Workday = Callable[[dt.date], bool]


class CapacityService:
    def __init__(
        self,
        sources: CapacitySources,
        settings: CapacitySettings,
        *,
        workday: Callable[[], Workday] | None = None,
        clock: Callable[[], float] = time.time,
        ttl_s: float = TTL_S,
    ) -> None:
        """`workday()` is asked per analysis, so a make-up workday recorded in
        the work calendar counts from the next analysis on."""
        self._sources = sources
        self._settings = settings
        self._workday = workday
        self._clock = clock
        self._ttl_s = ttl_s
        #: period → (built at, analysis, the timer that lets it go)
        self._kept: dict[int, tuple[float, Analysis, asyncio.TimerHandle]] = {}
        self._building: dict[int, asyncio.Task[Analysis]] = {}
        self._one_at_a_time = asyncio.Lock()

    def period(self, days: int) -> Period:
        step = self._settings.step_s
        if days > 28:
            step = long_step(step)
        tz = ZoneInfo(self._settings.timezone) if self._settings.timezone else ZoneInfo("UTC")
        now_ms = int(self._clock() * 1000)
        return Period.ending(now_ms, days=days, step_s=step, lag_s=self._settings.lag_s, tz=tz)

    def drop(self, days: int) -> None:
        """Let go of the period's analysis, and cancel the timer that would
        have — left running, it would let go of the next analysis early."""
        kept = self._kept.pop(days, None)
        if kept is not None:
            kept[2].cancel()

    async def analysis(self, days: int) -> Analysis:
        """This period's analysis, built if none is kept or it is older than
        the TTL. Raises `TooManyRows` when it would load more than allowed."""
        kept = self._kept.get(days)
        if kept is not None and self._clock() - kept[0] < self._ttl_s:
            return kept[1]
        task = self._building.get(days)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._build_and_keep(days))
            self._building[days] = task
            task.add_done_callback(partial(self._built, days))
        return await asyncio.shield(task)

    def _built(self, days: int, task: asyncio.Task[Analysis]) -> None:
        if self._building.get(days) is task:
            del self._building[days]
        if not task.cancelled():
            task.exception()  # retrieved: each waiter re-raises it; nobody else must log it

    async def _build_and_keep(self, days: int) -> Analysis:
        async with self._one_at_a_time:
            result = await asyncio.to_thread(self._build, days)
        self.drop(days)
        timer = asyncio.get_running_loop().call_later(self._ttl_s, self.drop, days)
        self._kept[days] = (self._clock(), result, timer)
        return result

    def _build(self, days: int) -> Analysis:
        period = self.period(days)
        obs = observe(self._sources, period, max_rows=self._settings.max_rows)
        is_workday = self._workday() if self._workday is not None else None
        now_ms = int(self._clock() * 1000)
        if is_workday is None:
            return analyse(obs, now_ms=now_ms)
        return analyse(obs, now_ms=now_ms, is_workday=is_workday)


def long_step(step_s: int) -> int:
    """The 90-day period's slot: at least `LONG_STEP_S`, a whole number of
    `step_s` (so of windows — a slot holding one window and two by turn reads
    a steady load as 1.33 and 0.67 by turn), and a whole number per day."""
    step = step_s * -(-LONG_STEP_S // step_s)
    while 86_400 % step:
        step += step_s
    return step


__all__ = ["PERIODS", "CapacityService", "TooManyRows"]
