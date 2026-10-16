"""The four sources (docs/plan-capacity-usage.md §8): the reference meaning of ①
`aggregate` over records as the sink writes them, and each source loaded by the
dotted path `capacity:` names — refused at boot when it is not one."""

from __future__ import annotations

import pytest

from workspace_app.capacity.sources import (
    AggregateQuery,
    CapacitySources,
    GpuMetricsSource,
    LlmRequestSource,
    ResourceMetricsSource,
    UsageRecordSource,
    aggregate_documents,
)
from workspace_app.capacity.usage_wire import document
from workspace_app.config.schema import CapacitySettings
from workspace_app.factories import get_capacity_sources

T0 = 1_760_501_100_000


def _work(at_ms: int, user: str, work: str, count: int, seconds: float, **more) -> dict:
    fields = {"user": user, "app": "pm", "work": work, "count": count, "seconds": seconds}
    return document("work", at_ms, pod="api-1", component="api", fields=fields | more)


DOCS = [
    _work(T0, "alice", "req:GET /a", 2, 0.5, h1=2),
    _work(T0, "alice", "req:GET /a", 1, 0.25),  # another pod's window, same bucket
    _work(T0 + 60_000, "bob", "req:GET /a", 4, 1.0, h1=3, h9=1),
    _work(T0 + 300_000, "alice", "turn:chat", 1, 120.0),
    document("turn", T0, pod="api-1", component="api", fields={"user": "alice"}),
]


def test_sums_are_grouped_by_the_named_fields_and_by_bucket():
    q = AggregateQuery(
        kind="work",
        start_ms=T0,
        end_ms=T0 + 600_000,
        step_s=300,
        group_by=("user", "work"),
        sums=("count", "seconds", "h1"),
    )

    rows = {
        (r.t_ms, r.keys["user"], r.keys["work"]): r.values for r in aggregate_documents(DOCS, q)
    }

    assert rows[(T0, "alice", "req:GET /a")] == {"count": 3.0, "seconds": 0.75, "h1": 2.0}
    assert rows[(T0, "bob", "req:GET /a")] == {"count": 4.0, "seconds": 1.0, "h1": 3.0}
    assert rows[(T0 + 300_000, "alice", "turn:chat")]["seconds"] == 120.0
    assert len(rows) == 3, "the turn record is another kind"


def test_a_filter_and_one_bucket_for_the_whole_range():
    q = AggregateQuery(
        kind="work",
        start_ms=T0,
        end_ms=T0 + 300_000,
        step_s=None,
        group_by=("work",),
        sums=("count",),
        maxes=("seconds",),
        where={"user": "alice"},
    )

    (row,) = aggregate_documents(DOCS, q)

    assert row.t_ms is None
    assert row.values == {"count": 3.0, "seconds": 0.5}, "the larger of 0.5 and 0.25"


def test_the_range_is_half_open():
    q = AggregateQuery(
        kind="work", start_ms=T0 + 60_000, end_ms=T0 + 300_000, step_s=60, group_by=("user",)
    )

    assert [r.keys["user"] for r in aggregate_documents(DOCS, q)] == ["bob"]


class Records(UsageRecordSource):
    def aggregate(self, q):
        return []

    def records(self, kind, start_ms, end_ms):
        return []


class Pods(ResourceMetricsSource):
    def usage(self, start_ms, end_ms, step_s):
        return []

    def capacity(self):
        return []


class Gpus(GpuMetricsSource):
    def usage(self, start_ms, end_ms, step_s):
        return []

    def cards(self):
        return []


class Llm(LlmRequestSource):
    def usage(self, start_ms, end_ms, step_s):
        return []

    def latency(self, start_ms, end_ms, step_s):
        return []


def test_nothing_named_nothing_connected():
    assert get_capacity_sources(CapacitySettings()) == CapacitySources()


def test_each_named_source_is_built():
    here = "tests.capacity.test_sources"
    sources = get_capacity_sources(
        CapacitySettings(
            usage_records=f"{here}.Records",
            resource_metrics=f"{here}.Pods",
            gpu_metrics=f"{here}.Gpus",
            llm_requests=f"{here}.Llm",
        )
    )

    assert isinstance(sources.usage_records, Records)
    assert isinstance(sources.resource_metrics, Pods)
    assert isinstance(sources.gpu_metrics, Gpus)
    assert isinstance(sources.llm_requests, Llm)


def test_a_source_of_the_wrong_kind_refuses_to_boot_naming_its_key():
    with pytest.raises(TypeError, match="capacity.gpu_metrics"):
        get_capacity_sources(CapacitySettings(gpu_metrics="tests.capacity.test_sources.Pods"))
