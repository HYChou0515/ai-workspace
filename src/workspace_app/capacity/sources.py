"""The four data sources the operator implements (docs/plan-capacity-usage.md §8, D2).

Each is configured by dotted path (`capacity.usage_records`, …) and constructed
with no arguments. Every method is synchronous and runs on a worker thread,
never the event loop, so it may block on a network call. Times are epoch
milliseconds; a bucket's `t_ms` is its start, aligned to a multiple of
`step_s * 1000` since the epoch — the same alignment the platform's own
windows use, so the two can be laid side by side.

`aggregate_documents` below is the reference meaning of ① `aggregate`: what
an ES composite aggregation over the records must return, run here over a
plain list of records. The test fakes use it, and so can a small deploy that
keeps its records in a file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: The components ② reports (`ResourceSample.component`, `CapacityEntry.component`).
COMPONENTS = ("api", "worker", "sandbox-host", "postgres", "filestore", "scratch")


# ── ① the platform's own records, read back ──────────────────────────────
@dataclass(frozen=True)
class AggregateQuery:
    kind: str
    start_ms: int
    end_ms: int
    step_s: int | None
    group_by: tuple[str, ...]
    sums: tuple[str, ...] = ()
    maxes: tuple[str, ...] = ()
    where: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AggregateRow:
    t_ms: int | None
    keys: Mapping[str, str]
    values: Mapping[str, float]


class UsageRecordSource(ABC):
    """① The records this platform wrote (§5), as the deploy stored them."""

    @abstractmethod
    def aggregate(self, q: AggregateQuery) -> Iterable[AggregateRow]:
        """Sum `q.sums` and take the max of `q.maxes` over the `usage` objects of
        kind `q.kind` whose `@timestamp` is in [start_ms, end_ms) and whose
        fields equal `q.where`, grouped by the `q.group_by` fields (a missing
        field groups as "") and by `step_s` buckets (None: one bucket, t_ms None).
        A field missing from a record adds nothing to its sum. A max over
        records none of which carries the field is left out of `values`, not
        0: that is how the analysis tells a kernel without pressure (PSI)
        from one where nothing waited. A field is named in `sums` or in
        `maxes`, never both: `values` is keyed by field name."""

    @abstractmethod
    def records(self, kind: str, start_ms: int, end_ms: int) -> Iterable[Mapping[str, Any]]:
        """Every `usage` object of `kind` ("turn" or "job") whose `@timestamp` is
        in [start_ms, end_ms)."""


# ── ② pods and machines ───────────────────────────────────────────────────
@dataclass(frozen=True)
class ResourceSample:
    t_ms: int
    component: str
    pod: str
    cpu_cores: float | None = None
    memory_bytes: float | None = None
    disk_bytes: float | None = None


@dataclass(frozen=True)
class CapacityEntry:
    component: str
    cpu_cores: float | None
    memory_bytes: float | None
    disk_bytes: float | None
    machines: int
    machine_label: str


class ResourceMetricsSource(ABC):
    """② What each pod and volume used, and what the machines can give."""

    @abstractmethod
    def usage(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[ResourceSample]:
        """Per bucket and pod: mean CPU cores and memory bytes over the bucket;
        for a volume (`filestore`, `scratch`, a database's), bytes used."""

    @abstractmethod
    def capacity(self) -> Iterable[CapacityEntry]:
        """Per component: the total it may use, and what one more machine is."""


# ── ③ GPU cards ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GpuSample:
    t_ms: int
    card: str
    model: str
    util: float
    memory_bytes: float


@dataclass(frozen=True)
class GpuCard:
    card: str
    node: str
    model: str
    memory_total_bytes: float


class GpuMetricsSource(ABC):
    """③ Each card's use, and which model it serves (named as LiteLLM names it)."""

    @abstractmethod
    def usage(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[GpuSample]: ...

    @abstractmethod
    def cards(self) -> Iterable[GpuCard]: ...


# ── ④ LiteLLM's request log ───────────────────────────────────────────────
@dataclass(frozen=True)
class LlmUsageRow:
    """Grouped by bucket × model × the request's whole tag list."""

    t_ms: int
    model: str
    tags: tuple[str, ...]
    requests: int
    prompt_tokens: int
    completion_tokens: int
    busy_seconds: float


@dataclass(frozen=True)
class LlmLatencyRow:
    t_ms: int
    model: str
    requests: int
    ttft_p50_s: float | None
    ttft_p95_s: float | None
    tokens_per_s_p50: float | None


class LlmRequestSource(ABC):
    """④ Every LLM request through LiteLLM — this platform's and other systems'."""

    @abstractmethod
    def usage(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[LlmUsageRow]:
        """`busy_seconds` = Σ (end − start) of the requests that started in the bucket."""

    @abstractmethod
    def latency(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[LlmLatencyRow]:
        """Time to first token (start → first token) and tokens per second
        (completion tokens ÷ (end − first token)), per bucket and model."""


@dataclass(frozen=True)
class CapacitySources:
    usage_records: UsageRecordSource | None = None
    resource_metrics: ResourceMetricsSource | None = None
    gpu_metrics: GpuMetricsSource | None = None
    llm_requests: LlmRequestSource | None = None


# ── the reference meaning of ① `aggregate` ───────────────────────────────
def _epoch_ms(stamp: str) -> int:
    import datetime as dt

    return int(dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def aggregate_documents(
    documents: Iterable[Mapping[str, Any]], q: AggregateQuery
) -> list[AggregateRow]:
    """`UsageRecordSource.aggregate` over records as the sink wrote them
    (`{"@timestamp", "event", "usage"}`)."""
    return aggregate_parsed(
        ((_epoch_ms(doc["@timestamp"]), doc.get("usage", {})) for doc in documents), q
    )


def aggregate_parsed(
    records: Iterable[tuple[int, Mapping[str, Any]]], q: AggregateQuery
) -> list[AggregateRow]:
    """`aggregate_documents` over `(epoch ms, usage)` pairs already parsed."""
    groups: dict[tuple[int | None, tuple[str, ...]], dict[str, float]] = {}
    step_ms = q.step_s * 1000 if q.step_s else None
    for at, usage in records:
        if usage.get("kind") != q.kind:
            continue
        if not q.start_ms <= at < q.end_ms:
            continue
        if any(str(usage.get(k, "")) != v for k, v in q.where.items()):
            continue
        bucket = at - at % step_ms if step_ms else None
        key = (bucket, tuple(str(usage.get(g, "")) for g in q.group_by))
        values = groups.setdefault(key, {})
        for name in q.sums:
            if (v := usage.get(name)) is not None:
                values[name] = values.get(name, 0.0) + float(v)
        for name in q.maxes:
            if (v := usage.get(name)) is not None:
                values[name] = max(values.get(name, float(v)), float(v))
    return [
        AggregateRow(t_ms=bucket, keys=dict(zip(q.group_by, key, strict=True)), values=values)
        for (bucket, key), values in sorted(
            groups.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1])
        )
    ]
