"""The record kinds (docs/plan-capacity-usage.md §5.2).

Every measured number in a windowed kind (`work`, `sandbox`, `sandbox_host`)
is additive — counts, seconds, byte-seconds — so the one aggregation ES has to
do is a sum. The exceptions describe rather than measure: `mem_max` (taken
with max), and `cpu_limit`, `mem_limit` and `window_s` (settings, never
summed). Event kinds (`turn`, `job`) are one record per occurrence; `disk` is
a snapshot.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, fields
from typing import Any, ClassVar

from .usage_wire import document

#: Upper bounds (ms) of request-latency buckets h0..h8; h9 holds everything slower.
LATENCY_BOUNDS_MS: tuple[int, ...] = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)
BUCKETS = len(LATENCY_BOUNDS_MS) + 1


def latency_bucket(ms: float) -> int:
    """The bucket a request of `ms` falls in: the first whose bound it does not exceed."""
    return bisect_left(LATENCY_BOUNDS_MS, ms)


@dataclass(frozen=True)
class WorkRow:
    """What one process did for one person, App and kind of work in one window."""

    kind: ClassVar[str] = "work"
    user: str
    app: str
    work: str
    count: int
    seconds: float
    window_s: int
    #: Requests only: how many fell in each latency bucket (`BUCKETS` long).
    hist: tuple[int, ...] = ()


@dataclass(frozen=True)
class TurnRecord:
    kind: ClassVar[str] = "turn"
    user: str
    app: str
    item: str
    surface: str
    outcome: str
    accepted_ms: int
    started_ms: int
    first_event_ms: int | None
    first_output_ms: int | None
    ended_ms: int
    #: The model that wrote the reply, and the provider's own token counts —
    #: absent when it gave none (a cross-check on ④, not a replacement).
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class JobRecord:
    kind: ClassVar[str] = "job"
    user: str
    jobtype: str
    enqueued_ms: int
    started_ms: int
    ended_ms: int
    outcome: str
    attempt: int


@dataclass(frozen=True)
class DiskRow:
    kind: ClassVar[str] = "disk"
    scope: str
    user: str
    app: str
    bytes: int
    item: str = ""
    collection: str = ""


@dataclass(frozen=True)
class MetaRow:
    """A window in which records were lost (the writer's queue was full)."""

    kind: ClassVar[str] = "meta"
    dropped: int
    window_s: int


UsageRecord = WorkRow | TurnRecord | JobRecord | DiskRow | MetaRow


def to_document(record: UsageRecord, at_ms: int, *, pod: str, component: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(record):
        value = getattr(record, f.name)
        if f.name == "hist":
            out.update((f"h{i}", n) for i, n in enumerate(value) if n)
        elif value != "" or f.name in ("user", "app"):
            out[f.name] = value
    return document(record.kind, at_ms, pod=pod, component=component, fields=out)
