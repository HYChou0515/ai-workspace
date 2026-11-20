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
    assert purge_telemetry_older_than(spec, CUTOFF).removed == 2
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
    result = purge_telemetry_older_than(spec, CUTOFF, batch=2)
    assert (result.removed, result.more) == (5, False)
    assert _left(spec) == []


def test_it_stops_when_the_time_budget_is_spent_and_the_next_pass_carries_on():
    # Written NEWEST first (review round 1): the in-memory backend lists rows
    # in insertion order, so oldest-first insertion made "the oldest went first"
    # hold with no sort at all.
    spec, _ = _spec_with(5, 6, 7, 8, 9)
    ticks = iter([0.0, 0.0, 100.0])  # start, after batch 1, after batch 2
    clock = lambda: next(ticks, 100.0)  # noqa: E731
    result = purge_telemetry_older_than(spec, CUTOFF, batch=2, budget_s=50, clock=clock)
    assert (result.removed, result.more) == (4, True)  # stopped on the budget
    assert _left(spec) == [1]  # the youngest (5 days) is the one left
    assert purge_telemetry_older_than(spec, CUTOFF, batch=2).removed == 1


def test_a_short_batch_after_the_budget_says_nothing_remains():
    """Review round 2: a pass whose budget ran out on the batch that also
    emptied the backlog has nothing left — `more` is about rows, not time."""
    spec, _ = _spec_with(9, 8, 7)
    ticks = iter([0.0, 100.0])  # start, after batch 1 (full), …
    clock = lambda: next(ticks, 100.0)  # noqa: E731
    result = purge_telemetry_older_than(spec, CUTOFF, batch=2, budget_s=50, clock=clock)
    assert (result.removed, result.more) == (2, True)  # stopped after a full batch
    result = purge_telemetry_older_than(spec, CUTOFF, batch=2, budget_s=0, clock=lambda: 100.0)
    assert (result.removed, result.more) == (1, False)  # budget long gone, batch short


def test_a_spec_without_telemetry_has_nothing_to_purge():
    assert purge_telemetry_older_than(make_spec(default_user="u"), CUTOFF).removed == 0
