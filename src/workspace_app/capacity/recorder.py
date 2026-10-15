"""One process's usage counters (docs/plan-capacity-usage.md §5.2, §6.7).

`add` counts finished work (a request); `begin` … `end` brackets work that
runs for a while (a stream, a turn, a job). Both land in the window they
happen in. Running work is charged to EVERY window it runs through — at each
flush the time since its last charge goes to the windows it spans — so a
two-hour stream is not one spike in the window it ends in.

A window is written once it has closed (`flush`, driven by `run`), and the
window in progress is written by `close` at shutdown. Event records (`turn`,
`job`, `disk`) are written as they come (`emit`). Everything goes through one
`UsageWriter`; what its full queue dropped is reported in a `meta` row.

Pod-local by construction (the #804 convention): it touches only this
process's own counts.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .records import BUCKETS, MetaRow, UsageRecord, WorkRow, latency_bucket, to_document
from .sink import UsageSink, UsageWriter
from .usage_wire import pod_name, window_start

#: How long after a window closes its flush runs — late enough that work
#: finishing on the boundary has been counted.
_FLUSH_GRACE_S = 0.1

_Key = tuple[str, str, str]


@dataclass
class _Cell:
    count: int = 0
    seconds: float = 0.0
    hist: list[int] = field(default_factory=list)


class Running:
    """Work in flight. `end()` it exactly once; a second call does nothing."""

    def __init__(self, recorder: UsageRecorder, key: _Key, started_s: float) -> None:
        self._recorder = recorder
        self.key = key
        self.charged_to_s = started_s
        self.done = False

    def end(self) -> None:
        self._recorder._end(self)


class UsageRecorder:
    def __init__(
        self,
        sink: UsageSink,
        *,
        component: str,
        pod: str | None = None,
        window_s: int = 300,
        queue_max: int = 10000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.component = component
        self.pod = pod or pod_name()
        self.window_s = window_s
        self._clock = clock
        self._writer = UsageWriter(sink, queue_max=queue_max)
        self._lock = threading.Lock()
        self._windows: dict[int, dict[_Key, _Cell]] = {}
        self._running: set[Running] = set()

    # ── counting ──────────────────────────────────────────────────────────
    def add(
        self,
        user: str,
        app: str,
        work: str,
        *,
        count: int = 1,
        seconds: float = 0.0,
        latency_ms: float | None = None,
    ) -> None:
        """Count finished work. `latency_ms` (requests) adds it to the histogram."""
        now = self._clock()
        with self._lock:
            cell = self._cell(self._window_of(now), (user, app, work))
            cell.count += count
            cell.seconds += seconds
            if latency_ms is not None:
                if not cell.hist:
                    cell.hist = [0] * BUCKETS
                cell.hist[latency_bucket(latency_ms)] += 1

    def begin(self, user: str, app: str, work: str, *, count: int = 1) -> Running:
        """Start running work: counted now, its seconds charged as it runs."""
        now = self._clock()
        key = (user, app, work)
        running = Running(self, key, now)
        with self._lock:
            self._cell(self._window_of(now), key).count += count
            self._running.add(running)
        return running

    def _end(self, running: Running) -> None:
        now = self._clock()
        with self._lock:
            if running.done:
                return
            running.done = True
            self._running.discard(running)
            self._charge(running.key, running.charged_to_s, now)
            running.charged_to_s = now

    # ── writing ───────────────────────────────────────────────────────────
    def emit(self, record: UsageRecord, *, at_ms: int) -> None:
        """Write an event record now (`at_ms`: when the event began)."""
        self._writer.put(to_document(record, at_ms, pod=self.pod, component=self.component))

    def flush(self, *, final: bool = False) -> None:
        """Write every closed window — and, `final`, the one in progress."""
        now = self._clock()
        current = self._window_of(now)
        until_s = now if final else current / 1000
        with self._lock:
            for running in self._running:
                if running.charged_to_s < until_s:
                    self._charge(running.key, running.charged_to_s, until_s)
                    running.charged_to_s = until_s
            due = sorted(w for w in self._windows if final or w < current)
            batches = [(w, self._windows.pop(w)) for w in due]
        for start, cells in batches:
            for (user, app, work), cell in cells.items():
                row = WorkRow(
                    user=user,
                    app=app,
                    work=work,
                    count=cell.count,
                    seconds=round(cell.seconds, 6),
                    window_s=self.window_s,
                    hist=tuple(cell.hist),
                )
                self._writer.put(to_document(row, start, pod=self.pod, component=self.component))
        dropped = self._writer.take_dropped()
        if dropped:
            meta = MetaRow(dropped=dropped, window_s=self.window_s)
            doc = to_document(meta, current, pod=self.pod, component=self.component)
            if not self._writer.put(doc):
                self._writer.return_dropped(dropped)

    async def run(self) -> None:
        """Flush each window as it closes, until cancelled."""
        while True:
            now = self._clock()
            next_close = self._window_of(now) / 1000 + self.window_s
            await asyncio.sleep(max(0.0, next_close - now) + _FLUSH_GRACE_S)
            self.flush()

    def run_until(self, stop: threading.Event) -> None:
        """`run` for a thread (the worker process): flush each window as it
        closes until `stop` is set, then write everything and stop (`close`)."""
        while not stop.is_set():
            now = self._clock()
            next_close = self._window_of(now) / 1000 + self.window_s
            if stop.wait(max(0.0, next_close - now) + _FLUSH_GRACE_S):
                break
            self.flush()
        self.close()

    def close(self, *, timeout_s: float = 5.0) -> None:
        """Write everything counted so far and stop the writer (shutdown)."""
        self.flush(final=True)
        self._writer.close(timeout_s=timeout_s)

    # ── internals (under the lock) ────────────────────────────────────────
    def _window_of(self, at_s: float) -> int:
        return window_start(int(at_s * 1000), self.window_s)

    def _cell(self, window: int, key: _Key) -> _Cell:
        cells = self._windows.setdefault(window, {})
        cell = cells.get(key)
        if cell is None:
            cell = cells[key] = _Cell()
        return cell

    def _charge(self, key: _Key, from_s: float, to_s: float) -> None:
        """Add the seconds of [from_s, to_s) to each window they fall in."""
        start = from_s
        while start < to_s:
            window = self._window_of(start)
            end = min(to_s, window / 1000 + self.window_s)
            self._cell(window, key).seconds += end - start
            start = end
