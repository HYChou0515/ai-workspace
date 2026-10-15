"""One process's usage counters (docs/plan-capacity-usage.md §5.2 `work`, §6.7):
summed per window and written when the window closes; work still running is
charged to every window it runs through, not only the one it ends in."""

from __future__ import annotations

import asyncio
import time

from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.records import TurnRecord
from workspace_app.capacity.sink import UsageSink

T0 = 1_760_501_100.0  # 2025-10-15T04:05:00Z, a multiple of 300 s


class _Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def _recorder(clock: _Clock, sink: _ListSink, **kw) -> UsageRecorder:
    return UsageRecorder(
        sink, component="api", pod="api-1", window_s=300, queue_max=1000, clock=clock, **kw
    )


def _rows(sink: _ListSink, kind: str = "work") -> list[dict]:
    return [d["usage"] | {"@": d["@timestamp"]} for d in sink.docs if d["usage"]["kind"] == kind]


def _delivered(sink: _ListSink, n: int) -> list[dict]:
    """The writer delivers on its own thread: wait (bounded) for `n` work rows."""
    deadline = time.monotonic() + 5
    while len(_rows(sink)) < n and time.monotonic() < deadline:
        time.sleep(0.01)
    return _rows(sink)


def test_a_request_is_written_once_its_window_closes():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)

    rec.add("alice", "pm", "req:GET /api/x", seconds=0.12, latency_ms=120)
    clock.t += 300
    rec.flush()
    rec.close()

    (row,) = _rows(sink)
    assert row["@"] == "2025-10-15T04:05:00.000Z"
    assert (row["user"], row["app"], row["work"], row["count"], row["seconds"]) == (
        "alice",
        "pm",
        "req:GET /api/x",
        1,
        0.12,
    )
    assert row["h2"] == 1
    assert (row["pod"], row["component"], row["window_s"]) == ("api-1", "api", 300)


def test_the_same_person_and_work_in_one_window_is_one_row():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)

    rec.add("alice", "pm", "req:GET /api/x", seconds=0.1, latency_ms=100)
    clock.t += 10
    rec.add("alice", "pm", "req:GET /api/x", seconds=0.3, latency_ms=300)
    rec.add("bob", "pm", "req:GET /api/x", seconds=0.2, latency_ms=200)
    clock.t += 300
    rec.close()

    rows = {r["user"]: r for r in _rows(sink)}
    assert rows["alice"]["count"] == 2
    assert abs(rows["alice"]["seconds"] - 0.4) < 1e-9
    assert (rows["alice"]["h1"], rows["alice"]["h3"]) == (1, 1)
    assert rows["bob"]["count"] == 1


def test_an_open_window_waits_and_closing_the_recorder_writes_it():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)

    rec.add("alice", "pm", "req:GET /api/x", seconds=0.1)
    clock.t += 100
    rec.flush()
    assert _rows(sink) == []

    rec.close()
    assert [r["count"] for r in _rows(sink)] == [1]


def test_running_work_is_charged_to_each_window_it_runs_through():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)

    clock.t += 200  # 04:08:20
    running = rec.begin("alice", "pm", "turn:chat")
    clock.t += 250  # 04:12:30, the first window closed at 04:10:00
    rec.flush()
    first = _delivered(sink, 1)
    assert [(r["@"], r["count"], r["seconds"]) for r in first] == [
        ("2025-10-15T04:05:00.000Z", 1, 100.0)
    ]

    clock.t += 30  # 04:13:00
    running.end()
    clock.t += 300
    rec.flush()
    rec.close()
    second = _rows(sink)[1:]
    assert [(r["@"], r["count"], r["seconds"]) for r in second] == [
        ("2025-10-15T04:10:00.000Z", 0, 180.0)
    ]


def test_windows_flushed_late_each_get_their_own_slice():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)

    running = rec.begin("alice", "pm", "stream:GET /api/s")
    clock.t += 750  # three windows later, nothing flushed meanwhile
    running.end()
    clock.t += 300
    rec.close()

    assert [(r["@"][11:16], r["count"], r["seconds"]) for r in _rows(sink)] == [
        ("04:05", 1, 300.0),
        ("04:10", 0, 300.0),
        ("04:15", 0, 150.0),
    ]


def test_ending_twice_charges_once():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)
    running = rec.begin("alice", "", "job:index")
    clock.t += 10
    running.end()
    clock.t += 10
    running.end()
    rec.close()

    assert [r["seconds"] for r in _rows(sink)] == [10.0]


def test_an_event_record_is_written_at_once_with_its_own_time():
    clock, sink = _Clock(), _ListSink()
    rec = _recorder(clock, sink)
    turn = TurnRecord(
        user="alice",
        app="pm",
        item="i1",
        surface="chat",
        outcome="done",
        accepted_ms=1_760_501_000_000,
        started_ms=1_760_501_000_100,
        first_event_ms=1_760_501_000_200,
        first_output_ms=1_760_501_000_900,
        ended_ms=1_760_501_009_000,
    )

    rec.emit(turn, at_ms=turn.accepted_ms)
    rec.close()

    (row,) = _rows(sink, "turn")
    assert row["@"] == "2025-10-15T04:03:20.000Z"
    assert row["first_output_ms"] == 1_760_501_000_900


def test_records_lost_to_a_full_queue_are_reported_in_a_meta_row():
    class _Slow(UsageSink):
        def __init__(self) -> None:
            self.docs: list[dict] = []

        def write(self, documents):
            time.sleep(0.05)
            self.docs.extend(documents)

    clock, sink = _Clock(), _Slow()
    rec = UsageRecorder(sink, component="api", pod="p", window_s=300, queue_max=2, clock=clock)
    for n in range(40):
        rec.add(f"u{n}", "", "req:GET /api/x")
    clock.t += 300
    rec.flush()  # 40 rows into a queue of 2: most are dropped
    clock.t += 300
    rec.add("late", "", "req:GET /api/x")
    clock.t += 300
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rec.flush()
        if any(d["usage"]["kind"] == "meta" for d in sink.docs):
            break
        time.sleep(0.05)
    rec.close()

    metas = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "meta"]
    assert metas and sum(m["dropped"] for m in metas) > 0


async def test_the_flush_loop_writes_each_window_as_it_closes():
    sink = _ListSink()
    rec = UsageRecorder(sink, component="api", pod="p", window_s=1, queue_max=100)
    loop = asyncio.create_task(rec.run())
    try:
        rec.add("alice", "", "req:GET /api/x")
        deadline = time.monotonic() + 10
        while not _rows(sink) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    finally:
        loop.cancel()
        rec.close()

    assert [r["user"] for r in _rows(sink)] == ["alice"]
