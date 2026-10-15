"""The capacity analysis, on demand (docs/plan-capacity-usage.md §11, E6).

One analysis per period, built in a worker thread (never on the event loop)
and kept in memory for `TTL_S`; a second request for the same period while
one is being built waits for it rather than building another. Nothing is
written anywhere (D3). The 90-day period is analysed at 15-minute slots or
coarser, so three months of a large deployment stays within memory.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Callable
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
        self._kept: dict[int, tuple[float, Analysis]] = {}
        self._building: dict[int, asyncio.Future[Analysis]] = {}

    def period(self, days: int) -> Period:
        step = self._settings.step_s
        if days > 28:
            step = max(step, LONG_STEP_S)
        tz = ZoneInfo(self._settings.timezone) if self._settings.timezone else ZoneInfo("UTC")
        now_ms = int(self._clock() * 1000)
        return Period.ending(now_ms, days=days, step_s=step, lag_s=self._settings.lag_s, tz=tz)

    def drop(self, days: int) -> None:
        self._kept.pop(days, None)

    async def analysis(self, days: int) -> Analysis:
        """This period's analysis, built if none is kept or it is older than
        the TTL. Raises `TooManyRows` when it would load more than allowed."""
        kept = self._kept.get(days)
        if kept is not None and self._clock() - kept[0] < self._ttl_s:
            return kept[1]
        building = self._building.get(days)
        if building is not None:
            return await asyncio.shield(building)
        future: asyncio.Future[Analysis] = asyncio.get_running_loop().create_future()
        self._building[days] = future
        try:
            result = await asyncio.to_thread(self._build, days)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # retrieved: a waiter re-raises it, nobody else must log it
            raise
        else:
            self._kept[days] = (self._clock(), result)
            future.set_result(result)
            return result
        finally:
            self._building.pop(days, None)

    def _build(self, days: int) -> Analysis:
        period = self.period(days)
        obs = observe(self._sources, period, max_rows=self._settings.max_rows)
        is_workday = self._workday() if self._workday is not None else None
        now_ms = int(self._clock() * 1000)
        if is_workday is None:
            return analyse(obs, now_ms=now_ms)
        return analyse(obs, now_ms=now_ms, is_workday=is_workday)


__all__ = ["PERIODS", "CapacityService", "TooManyRows"]
