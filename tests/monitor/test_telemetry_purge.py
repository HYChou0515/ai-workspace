"""docs/plan-telemetry-retention.md P3: telemetry-event rows older than the
cutoff are removed permanently, in batches, within a time budget."""

import datetime as dt

import pytest
from specstar import QB, SpecStar

from workspace_app.monitor import SpecstarMonitor, TelemetryEvent
from workspace_app.monitor.specstar_impl import purge_telemetry_older_than
from workspace_app.resources import make_spec

CUTOFF = dt.datetime(2026, 11, 5, tzinfo=dt.UTC)


def _spec_with(*ages_days: float) -> tuple[SpecStar, list[str]]:
    """One row per age, created that many days before CUTOFF (negative = after)."""
    spec = make_spec(default_user="u")
    SpecstarMonitor(spec)
    rm = spec.get_resource_manager(TelemetryEvent)
    ids = []
    for i, age in enumerate(ages_days):
        info = rm.create(
            TelemetryEvent(kind="span_end", seq=i + 1, payload={"n": i}),
            now=CUTOFF - dt.timedelta(days=age),
        )
        ids.append(info.resource_id)
    return spec, ids


def _left(spec: SpecStar) -> list[int]:
    rm = spec.get_resource_manager(TelemetryEvent)
    return sorted(r.data.seq for r in rm.list_resources(QB.all().build()))  # ty: ignore[unresolved-attribute]


def test_rows_older_than_the_cutoff_go_and_the_rest_stay():
    spec, _ = _spec_with(30, 15, 0, -1)
    assert purge_telemetry_older_than(spec, CUTOFF) == 2
    assert _left(spec) == [3, 4]  # the row exactly at the cutoff stays


def test_they_are_removed_permanently_not_soft_deleted():
    """A soft delete keeps the row and its revisions: no space comes back."""
    spec, ids = _spec_with(30)
    purge_telemetry_older_than(spec, CUTOFF)
    rm = spec.get_resource_manager(TelemetryEvent)
    with pytest.raises(Exception):  # noqa: B017 — any "no such resource" will do
        rm.restore(ids[0])


def test_a_backlog_larger_than_one_batch_is_drained():
    spec, _ = _spec_with(9, 8, 7, 6, 5)
    assert purge_telemetry_older_than(spec, CUTOFF, batch=2) == 5
    assert _left(spec) == []


def test_it_stops_when_the_time_budget_is_spent_and_the_next_pass_carries_on():
    spec, _ = _spec_with(9, 8, 7, 6, 5)
    ticks = iter([0.0, 0.0, 100.0])  # start, after batch 1, after batch 2
    clock = lambda: next(ticks, 100.0)  # noqa: E731
    assert purge_telemetry_older_than(spec, CUTOFF, batch=2, budget_s=50, clock=clock) == 4
    assert _left(spec) == [5]  # the oldest went first
    assert purge_telemetry_older_than(spec, CUTOFF, batch=2) == 1


def test_a_spec_without_telemetry_has_nothing_to_purge():
    assert purge_telemetry_older_than(make_spec(default_user="u"), CUTOFF) == 0
