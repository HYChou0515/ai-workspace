"""What the four sources say about one period, as arrays over its slots
(docs/plan-capacity-usage.md §8, §9).

Each source is asked only for grouped sums at the period's resolution — the
heavy lifting is the source's (an ES aggregation, a SQL GROUP BY). A source
that raises fails only what needs it: its status carries the error, and the
views that need it say so. A source not configured is simply absent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import unquote

import numpy as np

from ..records import BUCKETS
from ..sources import AggregateQuery, CapacityEntry, CapacitySources
from .period import Period

logger = logging.getLogger(__name__)

T = TypeVar("T")

HIST = tuple(f"h{i}" for i in range(BUCKETS))
#: "N people" = people active at least once in the last 30 days (D12).
LOOKBACK_DAYS = 30


class TooManyRows(Exception):
    """An analysis would load more rows than `capacity.max_rows` allows."""

    def __init__(self, rows: int, limit: int) -> None:
        super().__init__(f"more than {limit} rows ({rows} so far)")
        self.rows = rows
        self.limit = limit


@dataclass
class SourceStatus:
    connected: bool
    error: str | None = None
    rows: int = 0


@dataclass
class WorkSeries:
    count: np.ndarray
    seconds: np.ndarray
    hist: np.ndarray | None = None  # [slot, bucket], requests only


@dataclass
class LlmSeries:
    """One model's traffic from one (person, App, purpose) — or from another system."""

    model: str
    ours: bool
    user: str
    app: str
    purpose: str
    busy: np.ndarray
    requests: np.ndarray


@dataclass
class Observations:
    period: Period
    status: dict[str, SourceStatus] = field(default_factory=dict)
    # ① the platform's records
    work_pod: dict[tuple[str, str, str], WorkSeries] = field(default_factory=dict)
    #: (user, app, component, work): a job counts on the line of the process that ran it.
    work_user: dict[tuple[str, str, str, str], WorkSeries] = field(default_factory=dict)
    sandbox_user: dict[tuple[str, str], dict[str, np.ndarray]] = field(default_factory=dict)
    sandbox_pod: dict[tuple[str, str], dict[str, np.ndarray]] = field(default_factory=dict)
    sandbox_items: dict[tuple[str, str], float] = field(default_factory=dict)
    host: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    #: (day index, scope, id, user, app) → bytes, the day's latest snapshot.
    disk: dict[tuple[int, str, str, str, str], float] = field(default_factory=dict)
    #: person → the epoch-ms day starts (UTC days) they did anything, from 30
    #: days before the period to its end: "N people" counts the people active
    #: in the 30 days up to each day (D12), which needs those 30 days.
    activity: dict[str, set[int]] = field(default_factory=dict)
    turns: list[Mapping[str, Any]] = field(default_factory=list)
    jobs: list[Mapping[str, Any]] = field(default_factory=list)
    dropped: float = 0.0
    # ② pods and machines: (component, pod) → resource → [slot] (NaN: no sample)
    pods: dict[tuple[str, str], dict[str, np.ndarray]] = field(default_factory=dict)
    capacity: list[CapacityEntry] = field(default_factory=list)
    # ③ cards: model → util summed over its cards, memory used; cards per model
    gpu: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    cards: dict[str, int] = field(default_factory=dict)
    gpu_memory_total: dict[str, float] = field(default_factory=dict)
    # ④ LiteLLM
    llm: list[LlmSeries] = field(default_factory=list)
    latency: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)

    def connected(self, source: str) -> bool:
        s = self.status.get(source)
        return s is not None and s.connected and s.error is None


def _zeros(p: Period) -> np.ndarray:
    return np.zeros(p.n)


def _nans(p: Period) -> np.ndarray:
    return np.full(p.n, np.nan)


def _guard(obs: Observations, name: str, connected: bool, load: Callable[[], int]) -> None:
    if not connected:
        obs.status[name] = SourceStatus(connected=False)
        return
    try:
        obs.status[name] = SourceStatus(connected=True, rows=load())
    except TooManyRows:
        raise  # the whole analysis stops: shorter period, or a larger limit
    except Exception as exc:  # noqa: BLE001 — one source failing fails only its views
        logger.exception("capacity: source %s failed", name)
        obs.status[name] = SourceStatus(connected=True, error=f"{type(exc).__name__}: {exc}")


def parse_tags(tags: Iterable[str]) -> dict[str, str]:
    """`ws.user:alice` → {"user": "alice"}; "ws" alone marks this platform."""
    out: dict[str, str] = {}
    for tag in tags:
        if tag == "ws":
            out["ws"] = "1"
        elif tag.startswith("ws.") and ":" in tag:
            key, _, value = tag[3:].partition(":")
            out[key] = unquote(value)
    return out


class _Budget:
    """Counts rows across every source; past `limit`, stops the analysis."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.rows = 0

    def take(self, n: int = 1) -> None:
        self.rows += n
        if self.limit is not None and self.rows > self.limit:
            raise TooManyRows(self.rows, self.limit)


def observe(
    sources: CapacitySources, period: Period, *, max_rows: int | None = None
) -> Observations:
    budget = _Budget(max_rows)
    obs = Observations(period)
    _guard(
        obs,
        "usage_records",
        sources.usage_records is not None,
        lambda: _records(sources, obs, budget),
    )
    _guard(
        obs,
        "resource_metrics",
        sources.resource_metrics is not None,
        lambda: _pods(sources, obs, budget),
    )
    _guard(obs, "gpu_metrics", sources.gpu_metrics is not None, lambda: _gpus(sources, obs, budget))
    _guard(
        obs, "llm_requests", sources.llm_requests is not None, lambda: _llm(sources, obs, budget)
    )
    return obs


def _records(sources: CapacitySources, obs: Observations, budget: _Budget) -> int:
    src = sources.usage_records
    assert src is not None
    p = obs.period
    rows = 0

    def ask(kind: str, group_by: tuple[str, ...], sums: tuple[str, ...] = (), **kw: Any):  # noqa: ANN202
        """The source's grouped rows, one at a time (counted as they pass)."""
        nonlocal rows
        query = AggregateQuery(
            kind=kind,
            start_ms=p.start_ms,
            end_ms=p.end_ms,
            step_s=kw.pop("step_s", p.step_s),
            group_by=group_by,
            sums=sums,
            **kw,
        )
        for row in src.aggregate(query):
            rows += 1
            budget.take()
            yield row

    for r in ask("work", ("pod", "component", "work"), ("count", "seconds", *HIST)):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        key = (r.keys["pod"], r.keys["component"], r.keys["work"])
        series = obs.work_pod.get(key)
        if series is None:
            hist = np.zeros((p.n, BUCKETS)) if key[2].startswith("req:") else None
            series = obs.work_pod[key] = WorkSeries(_zeros(p), _zeros(p), hist)
        series.count[i] += r.values.get("count", 0.0)
        series.seconds[i] += r.values.get("seconds", 0.0)
        if series.hist is not None:
            series.hist[i] += [r.values.get(h, 0.0) for h in HIST]
    for r in ask("work", ("user", "app", "component", "work"), ("count", "seconds")):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        key = (r.keys["user"] or "unknown", r.keys["app"], r.keys["component"], r.keys["work"])
        series = obs.work_user.setdefault(key, WorkSeries(_zeros(p), _zeros(p)))
        series.count[i] += r.values.get("count", 0.0)
        series.seconds[i] += r.values.get("seconds", 0.0)
    measured = ("cpu_s", "mem_bs", "thr_s", "cpu_psi_s", "oom")
    for r in ask("sandbox", ("user", "app"), measured):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        acc = obs.sandbox_user.setdefault(
            (r.keys["user"] or "unknown", r.keys["app"]), {m: _zeros(p) for m in measured}
        )
        for m in measured:
            acc[m][i] += r.values.get(m, 0.0)
    for r in ask("sandbox", ("pod", "component"), ("cpu_s", "mem_bs")):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        acc = obs.sandbox_pod.setdefault(
            (r.keys["pod"], r.keys["component"]), {m: _zeros(p) for m in ("cpu_s", "mem_bs")}
        )
        acc["cpu_s"][i] += r.values.get("cpu_s", 0.0)
        acc["mem_bs"][i] += r.values.get("mem_bs", 0.0)
    for r in ask("sandbox", ("user", "item"), ("cpu_s",), step_s=None):
        obs.sandbox_items[(r.keys["user"], r.keys["item"])] = r.values.get("cpu_s", 0.0)
    for r in ask("sandbox_host", ("pod",), ("live_s", "cpu_psi_s")):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        acc = obs.host.setdefault(r.keys["pod"], {"live_s": _zeros(p), "cpu_psi_s": _zeros(p)})
        acc["live_s"][i] += r.values.get("live_s", 0.0)
        acc["cpu_psi_s"][i] += r.values.get("cpu_psi_s", 0.0)
    disk_keys = ("scope", "item", "collection", "user", "app")
    for r in ask("disk", disk_keys, maxes=("bytes",), step_s=p.step_s):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        k = r.keys
        ident = k["item"] or k["collection"]
        key = (int(p.day_of[i]), k["scope"], ident, k["user"] or "unknown", k["app"])
        obs.disk[key] = max(obs.disk.get(key, 0.0), r.values.get("bytes", 0.0))
    for r in ask("meta", ("pod",), ("dropped",), step_s=None):
        obs.dropped += r.values.get("dropped", 0.0)
    lookback = AggregateQuery(
        kind="work",
        start_ms=p.start_ms - LOOKBACK_DAYS * 86_400_000,
        end_ms=p.end_ms,
        step_s=86_400,
        group_by=("user",),
        sums=("count",),
    )
    for r in src.aggregate(lookback):
        rows += 1
        if r.t_ms is not None and r.values.get("count", 0.0) > 0:
            obs.activity.setdefault(r.keys["user"] or "unknown", set()).add(r.t_ms)
    obs.turns = list(src.records("turn", p.start_ms, p.end_ms))
    obs.jobs = list(src.records("job", p.start_ms, p.end_ms))
    return rows + len(obs.turns) + len(obs.jobs)


def _pods(sources: CapacitySources, obs: Observations, budget: _Budget) -> int:
    src = sources.resource_metrics
    assert src is not None
    p = obs.period
    rows = 0
    for sample in src.usage(p.start_ms, p.end_ms, p.step_s):
        budget.take()
        i = p.index(sample.t_ms)
        if i is None:
            continue
        rows += 1
        acc = obs.pods.setdefault(
            (sample.component, sample.pod), {r: _nans(p) for r in ("cpu", "memory", "disk")}
        )
        for name, value in (
            ("cpu", sample.cpu_cores),
            ("memory", sample.memory_bytes),
            ("disk", sample.disk_bytes),
        ):
            if value is not None:
                acc[name][i] = value
    obs.capacity = list(src.capacity())
    return rows


def _gpus(sources: CapacitySources, obs: Observations, budget: _Budget) -> int:
    src = sources.gpu_metrics
    assert src is not None
    p = obs.period
    rows = 0
    for sample in src.usage(p.start_ms, p.end_ms, p.step_s):
        budget.take()
        i = p.index(sample.t_ms)
        if i is None:
            continue
        rows += 1
        acc = obs.gpu.setdefault(sample.model, {"util": _zeros(p), "memory": _zeros(p)})
        acc["util"][i] += sample.util
        acc["memory"][i] += sample.memory_bytes
    for card in src.cards():
        obs.cards[card.model] = obs.cards.get(card.model, 0) + 1
        obs.gpu_memory_total[card.model] = (
            obs.gpu_memory_total.get(card.model, 0.0) + card.memory_total_bytes
        )
    return rows


def _llm(sources: CapacitySources, obs: Observations, budget: _Budget) -> int:
    src = sources.llm_requests
    assert src is not None
    p = obs.period
    rows = 0
    series: dict[tuple[str, bool, str, str, str], LlmSeries] = {}
    for row in src.usage(p.start_ms, p.end_ms, p.step_s):
        budget.take()
        i = p.index(row.t_ms)
        if i is None:
            continue
        rows += 1
        tags = parse_tags(row.tags)
        ours = "ws" in tags
        key = (
            row.model,
            ours,
            (tags.get("user") or "unknown") if ours else "",
            tags.get("app", "") if ours else "",
            tags.get("purpose", "") if ours else "",
        )
        s = series.get(key)
        if s is None:
            s = series[key] = LlmSeries(*key, busy=_zeros(p), requests=_zeros(p))
        s.busy[i] += row.busy_seconds
        s.requests[i] += row.requests
    obs.llm = list(series.values())
    for row in src.latency(p.start_ms, p.end_ms, p.step_s):
        i = p.index(row.t_ms)
        if i is None:
            continue
        rows += 1
        acc = obs.latency.setdefault(
            row.model, {"ttft_p95": _nans(p), "tps_p50": _nans(p), "requests": _zeros(p)}
        )
        if row.ttft_p95_s is not None:
            acc["ttft_p95"][i] = row.ttft_p95_s
        if row.tokens_per_s_p50 is not None:
            acc["tps_p50"][i] = row.tokens_per_s_p50
        acc["requests"][i] += row.requests
    return rows
