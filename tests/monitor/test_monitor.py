"""Live monitor (issue #11): ring buffer + fan-out, and the SDK trace processor
that feeds it."""

import asyncio
import json
from typing import Any

from workspace_app.monitor import InMemoryMonitor, MonitorProcessor


def test_recent_buffers_and_filters_by_group():
    m = InMemoryMonitor(capacity=3)
    m.record({"kind": "x", "group_id": "inv-1", "n": 1})
    m.record({"kind": "x", "group_id": "inv-2", "n": 2})
    m.record({"kind": "x", "group_id": "inv-1", "n": 3})
    assert [e["n"] for e in m.recent()] == [1, 2, 3]
    assert [e["n"] for e in m.recent(group_id="inv-1")] == [1, 3]
    assert [e["n"] for e in m.recent(limit=1)] == [3]


def test_recent_filters_by_kind():
    # #407: the summary endpoint reads only its own event kinds, not agent traces.
    m = InMemoryMonitor()
    m.record({"kind": "mirror", "n": 1})
    m.record({"kind": "restore", "n": 2})
    m.record({"kind": "mirror", "n": 3})
    assert [e["n"] for e in m.recent(kind="mirror")] == [1, 3]
    assert [e["n"] for e in m.recent(kind="restore")] == [2]


def test_capacity_drops_oldest():
    m = InMemoryMonitor(capacity=2)
    for i in range(3):
        m.record({"kind": "x", "n": i})
    assert [e["n"] for e in m.recent()] == [1, 2]


async def test_subscribe_receives_live_events_then_unsubscribes():
    m = InMemoryMonitor()
    async with m.subscribe() as q:
        m.record({"kind": "x", "n": 1})
        assert (await asyncio.wait_for(q.get(), 1))["n"] == 1
    # after leaving the context, recording no longer reaches the queue
    m.record({"kind": "x", "n": 2})
    assert q.empty()


async def test_sse_yields_data_lines_for_matching_events_only():
    m = InMemoryMonitor()
    agen = m.sse(group_id="inv-1")

    async def feed() -> None:
        await asyncio.sleep(0.05)  # let `__anext__` subscribe first
        m.record({"kind": "span_end", "group_id": "inv-2", "skip": True})  # filtered out
        m.record({"kind": "span_end", "group_id": "inv-1", "n": 7})

    feeder = asyncio.create_task(feed())
    line = await asyncio.wait_for(agen.__anext__(), 1)  # subscribes, waits, gets inv-1 event
    assert line.startswith("data: ") and line.endswith("\n\n")
    assert json.loads(line[len("data: ") :])["n"] == 7
    await feeder
    await agen.aclose()  # ty: ignore[unresolved-attribute]


# --- the SDK trace processor ---


class _FakeTrace:
    def __init__(self, trace_id: str, group_id: str | None) -> None:
        self.trace_id = trace_id
        self.group_id = group_id

    def export(self) -> dict[str, Any]:
        # group_id lives in the SDK's export() payload (that's where the
        # processor reads it from).
        return {"object": "trace", "id": self.trace_id, "group_id": self.group_id}


class _FakeSpan:
    def __init__(self, trace_id: str, data: dict[str, Any]) -> None:
        self.trace_id = trace_id
        self._data = data

    def export(self) -> dict[str, Any]:
        return {
            "object": "trace.span",
            "id": "s1",
            "trace_id": self.trace_id,
            "span_data": self._data,
        }


def test_processor_mirrors_traces_and_stamps_span_group_from_trace():
    m = InMemoryMonitor()
    p = MonitorProcessor(m)
    p.on_trace_start(_FakeTrace("t1", "inv-1"))  # ty: ignore[invalid-argument-type]
    # a generation span (LLM call with token usage) under that trace
    p.on_span_end(_FakeSpan("t1", {"type": "generation", "usage": {"input_tokens": 9}}))  # ty: ignore[invalid-argument-type]
    p.on_trace_end(_FakeTrace("t1", "inv-1"))  # ty: ignore[invalid-argument-type]
    p.force_flush()
    p.shutdown()

    events = m.recent()
    assert [e["kind"] for e in events] == ["trace_start", "span_end", "trace_end"]
    span = events[1]
    # the span event inherits the trace's group_id + keeps the SDK usage payload
    assert span["group_id"] == "inv-1"
    assert span["span_data"]["usage"] == {"input_tokens": 9}


def test_processor_span_without_known_trace_has_no_group():
    m = InMemoryMonitor()
    p = MonitorProcessor(m)
    p.on_span_start(_FakeSpan("unknown", {"type": "function", "name": "exec"}))  # ty: ignore[invalid-argument-type]
    assert m.recent()[0]["group_id"] is None
