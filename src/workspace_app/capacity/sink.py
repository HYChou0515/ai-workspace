"""Where usage records go (docs/plan-capacity-usage.md §5, §6.7).

The platform stores nothing (D3): by default each record is one JSON line on
stdout, which the deploy's collector ships to ES. A deploy that would rather
write elsewhere names its own `UsageSink` in `capacity.sink`.

Records reach the sink through `UsageWriter`: a bounded queue drained by one
thread. Producing a record is a `put_nowait` — a slow or stuck stdout costs the
request path nothing — and a full queue drops the record and counts it, so the
loss is reported (`meta` rows) rather than paid for in memory.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from .usage_wire import line

logger = logging.getLogger(__name__)

#: How many records one `UsageSink.write` call receives at most.
BATCH_MAX = 500
#: A failing sink is logged at most this often.
_COMPLAIN_EVERY_S = 60.0


class UsageSink(ABC):
    """Receives finished records (the envelope of §5.1), in batches, on the
    writer thread — never on the event loop, so it may block. An exception
    loses that batch and is logged; the next batch is tried as usual.

    Configured by dotted path (`capacity.sink`), so a subclass must be
    constructible with no arguments."""

    @abstractmethod
    def write(self, documents: Sequence[Mapping[str, Any]]) -> None: ...


class StdoutSink(UsageSink):
    """One JSON line per record on stdout (the default, D3 「A」)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def write(self, documents: Sequence[Mapping[str, Any]]) -> None:
        out = self._stream if self._stream is not None else sys.stdout
        out.write("".join(line(doc) + "\n" for doc in documents))
        out.flush()


class UsageWriter:
    """The bounded queue and the one thread that hands its records to a sink."""

    def __init__(self, sink: UsageSink, *, queue_max: int) -> None:
        self._sink = sink
        self._queue: queue.Queue[Mapping[str, Any] | None] = queue.Queue(maxsize=queue_max)
        self._dropped = 0
        self._lock = threading.Lock()
        self._last_complaint = 0.0
        self._thread = threading.Thread(target=self._run, name="usage-writer", daemon=True)
        self._thread.start()

    def put(self, doc: Mapping[str, Any]) -> bool:
        """Queue one record; False (and counted) when the queue is full."""
        try:
            self._queue.put_nowait(doc)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        return True

    def take_dropped(self) -> int:
        """How many records were dropped since the last call."""
        with self._lock:
            dropped, self._dropped = self._dropped, 0
        return dropped

    def close(self, *, timeout_s: float) -> None:
        """Deliver what is queued, then stop. Bounded: a stuck sink cannot hold
        a shutdown longer than `timeout_s` (the thread is a daemon)."""
        try:
            self._queue.put(None, timeout=timeout_s)
        except queue.Full:
            return
        self._thread.join(timeout_s)

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            if first is None:
                return
            batch = [first]
            stop = False
            while len(batch) < BATCH_MAX:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    stop = True
                    break
                batch.append(more)
            self._write(batch)
            if stop:
                return

    def _write(self, batch: list[Mapping[str, Any]]) -> None:
        try:
            self._sink.write(batch)
        except Exception:
            now = time.monotonic()
            if now - self._last_complaint >= _COMPLAIN_EVERY_S:
                self._last_complaint = now
                logger.warning("usage: the sink failed; %d records lost", len(batch), exc_info=True)
