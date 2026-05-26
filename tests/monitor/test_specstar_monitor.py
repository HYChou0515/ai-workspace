"""SpecstarMonitor (issue #11): telemetry events persisted as specstar resources
+ the ABC's in-memory live feed."""

import asyncio
from datetime import UTC, datetime

from specstar import SpecStar

from workspace_app.monitor import IMonitor, SpecstarMonitor


def _spec() -> SpecStar:
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    return spec


def test_recent_returns_persisted_events_in_order_filtered_and_limited():
    m = SpecstarMonitor(_spec())
    m.record({"kind": "span_end", "group_id": "inv-1", "n": 1})
    m.record({"kind": "span_end", "group_id": "inv-2", "n": 2})
    m.record({"kind": "span_end", "group_id": "inv-1", "n": 3})
    assert [e["n"] for e in m.recent()] == [1, 2, 3]
    assert [e["n"] for e in m.recent(group_id="inv-1")] == [1, 3]
    assert [e["n"] for e in m.recent(limit=1)] == [3]


def test_events_survive_a_new_monitor_over_the_same_spec():
    spec = _spec()
    SpecstarMonitor(spec).record({"kind": "span_end", "group_id": "inv-1", "n": 7})
    # a fresh monitor instance over the same spec still sees the persisted event,
    # and continues the order key without colliding
    m2 = SpecstarMonitor(spec)
    m2.record({"kind": "span_end", "group_id": "inv-1", "n": 8})
    assert [e["n"] for e in m2.recent()] == [7, 8]


async def test_live_feed_is_in_memory_fan_out():
    m = SpecstarMonitor(_spec())
    async with m.subscribe() as q:
        m.record({"kind": "span_end", "group_id": "inv-1", "n": 1})
        assert (await asyncio.wait_for(q.get(), 1))["n"] == 1


def test_is_an_imonitor():
    # drop-in for InMemoryMonitor: same IMonitor interface
    assert issubclass(SpecstarMonitor, IMonitor)
