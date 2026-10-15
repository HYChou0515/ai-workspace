"""What the four sources say about one period, as arrays over its slots
(docs/plan-capacity-usage.md §8, §9).

Each source is asked only for grouped sums at the period's resolution — the
heavy lifting is the source's (an ES aggregation, a SQL GROUP BY). A source
that raises fails only what needs it: its status carries the error, and the
views that need it say so. A source not configured is simply absent.
"""

from __future__ import annotations

import logging
import math
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import unquote

import numpy as np

from ..records import BUCKETS
from ..sources import AggregateQuery, AggregateRow, CapacityEntry, CapacitySources
from .period import Period

logger = logging.getLogger(__name__)

T = TypeVar("T")

HIST = tuple(f"h{i}" for i in range(BUCKETS))
#: "N people" = people active at least once in the last 30 days (D12).
LOOKBACK_DAYS = 30
#: A week before the 30 days: anyone active there shows the records go back
#: further than the 30 days, so every day's N is whole.
PROOF_DAYS = 7


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


class Series:
    """One key's sums per slot, kept as the (slot, value) pairs the source
    returned. A whole period of slots per key, mostly zeros, made memory follow
    keys × slots — and keys multiply: every rollout renames the pods, every
    person brings their kinds of work — so a few hundred rows held hundreds of
    MB in the API process that serves everyone. Now it follows the rows read,
    which `capacity.max_rows` bounds. `dense()` builds the full array for the
    moment it is needed. A value that is not a finite number is dropped (a
    NaN would otherwise poison every sum it joins)."""

    __slots__ = ("_at", "_v", "n")

    def __init__(self, n: int) -> None:
        self.n = n
        self._at = array("q")
        self._v = array("d")

    def add(self, i: int, v: float) -> None:
        if v and math.isfinite(v):
            self._at.append(i)
            self._v.append(v)

    def dense(self) -> np.ndarray:
        if not self._at:
            return np.zeros(self.n)
        at = np.frombuffer(self._at, dtype=np.int64)
        return np.bincount(at, weights=np.frombuffer(self._v), minlength=self.n)

    def slots(self) -> np.ndarray:
        """The slots with something in them."""
        return np.unique(np.frombuffer(self._at, dtype=np.int64)) if self._at else _NONE

    def total(self) -> float:
        return float(np.frombuffer(self._v).sum()) if self._v else 0.0


_NONE = np.zeros(0, dtype=np.int64)


class WorkSeries:
    """One key's work: how many, and how many seconds, per slot."""

    __slots__ = ("_count", "_seconds")

    def __init__(self, n: int) -> None:
        self._count = Series(n)
        self._seconds = Series(n)

    def add(self, i: int, count: float, seconds: float) -> None:
        self._count.add(i, count)
        self._seconds.add(i, seconds)

    @property
    def count(self) -> np.ndarray:
        return self._count.dense()

    @property
    def seconds(self) -> np.ndarray:
        return self._seconds.dense()

    def slots(self) -> np.ndarray:
        """The slots with any count in them."""
        return self._count.slots()

    def total_count(self) -> float:
        return self._count.total()

    def total_seconds(self) -> float:
        return self._seconds.total()


class Measures(Mapping[str, np.ndarray]):
    """Several sums per slot for one key, each kept as a `Series`; reading
    one builds its array."""

    def __init__(self, n: int, names: Iterable[str]) -> None:
        self._series = {name: Series(n) for name in names}

    def add(self, i: int, values: Mapping[str, float]) -> None:
        for name, series in self._series.items():
            series.add(i, values.get(name, 0.0))

    def __getitem__(self, name: str) -> np.ndarray:
        return self._series[name].dense()

    def __iter__(self) -> Iterator[str]:
        return iter(self._series)

    def __len__(self) -> int:
        return len(self._series)

    def slots(self, name: str) -> np.ndarray:
        return self._series[name].slots()


class Samples(Mapping[str, np.ndarray]):
    """One pod's sampled figures per slot (cores, bytes), kept as the samples
    the source returned. Unlike a sum, a slot with no sample is no zero: it
    reads NaN. A whole period of slots per pod made every rollout — each pod
    name a new key — three more arrays of the whole period, though the pod
    lived a day of it."""

    def __init__(self, n: int, names: Iterable[str]) -> None:
        self.n = n
        self._at = {name: array("q") for name in names}
        self._v = {name: array("d") for name in names}

    def set(self, i: int, name: str, value: float | None) -> None:
        if value is not None and math.isfinite(value):
            self._at[name].append(i)
            self._v[name].append(value)

    def __getitem__(self, name: str) -> np.ndarray:
        out = np.full(self.n, np.nan)
        if self._at[name]:
            out[np.frombuffer(self._at[name], dtype=np.int64)] = np.frombuffer(self._v[name])
        return out

    def __iter__(self) -> Iterator[str]:
        return iter(self._at)

    def __len__(self) -> int:
        return len(self._at)

    def slots(self, name: str) -> np.ndarray:
        """The slots with a sample of `name`."""
        at = self._at[name]
        return np.unique(np.frombuffer(at, dtype=np.int64)) if at else _NONE


def _stamp(value: Any) -> int | None:
    """An epoch-ms stamp off a record, or None when it has none."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return int(value)


class Events:
    """Turn or job records, as the few columns the analysis reads. A record
    parsed from ES is a dict of one to two kilobytes, and a period holds
    hundreds of thousands of them for the analysis' whole TTL; here one is
    28 bytes. `at_ms`: when it began — a turn's acceptance, a job's start (−1
    when the record has none); `wait_s`: a turn's acceptance → first output,
    a job's enqueue → start (NaN when it lacks either stamp); `label`: a
    turn's model, a job's type; `user`, `item`, `label` index `names`."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self._index: dict[str, int] = {}
        self._columns: dict[str, array] | None = {
            "user": array("i"),
            "item": array("i"),
            "label": array("i"),
            "at_ms": array("q"),
            "wait_s": array("d"),
        }
        self._arrays: dict[str, np.ndarray] = {}

    def _name(self, value: Any) -> int:
        key = "" if value is None else str(value)
        found = self._index.get(key)
        if found is None:
            found = self._index[key] = len(self.names)
            self.names.append(key)
        return found

    def add(
        self, *, user: Any, item: Any, label: Any, at_ms: Any, since_ms: Any, until_ms: Any
    ) -> None:
        """One record: `at_ms` places it; its wait runs from `since_ms` to `until_ms`."""
        assert self._columns is not None, "read already: add every record first"
        at, since, until = _stamp(at_ms), _stamp(since_ms), _stamp(until_ms)
        wait = (until - since) / 1000 if since is not None and until is not None else math.nan
        c = self._columns
        c["user"].append(self._name(user))
        c["item"].append(self._name(item))
        c["label"].append(self._name(label))
        c["at_ms"].append(-1 if at is None else at)
        c["wait_s"].append(wait)

    def __len__(self) -> int:
        if self._columns is not None:
            return len(self._columns["at_ms"])
        return len(self._arrays["at_ms"])

    def _column(self, name: str) -> np.ndarray:
        if self._columns is not None:
            dtypes = {"user": np.intc, "item": np.intc, "label": np.intc, "at_ms": np.int64}
            self._arrays = {
                key: np.array(col, dtype=dtypes.get(key, np.float64))
                for key, col in self._columns.items()
            }
            self._columns = None
        return self._arrays[name]

    @property
    def user(self) -> np.ndarray:
        return self._column("user")

    @property
    def item(self) -> np.ndarray:
        return self._column("item")

    @property
    def label(self) -> np.ndarray:
        return self._column("label")

    @property
    def at_ms(self) -> np.ndarray:
        return self._column("at_ms")

    @property
    def wait_s(self) -> np.ndarray:
        return self._column("wait_s")

    def index_of(self, name: str) -> int | None:
        """`name`'s index in `names`, or None when no record carries it."""
        return self._index.get(name)


class LlmSeries:
    """One model's traffic from one (person, App, purpose) — or from another system."""

    __slots__ = ("_busy", "_requests", "app", "model", "ours", "purpose", "user")

    def __init__(self, model: str, ours: bool, user: str, app: str, purpose: str, *, n: int):
        self.model, self.ours, self.user, self.app, self.purpose = model, ours, user, app, purpose
        self._busy = Series(n)
        self._requests = Series(n)

    def add(self, i: int, busy: float, requests: float) -> None:
        self._busy.add(i, busy)
        self._requests.add(i, requests)

    @property
    def busy(self) -> np.ndarray:
        return self._busy.dense()

    @property
    def requests(self) -> np.ndarray:
        return self._requests.dense()

    def slots(self) -> np.ndarray:
        return self._requests.slots()


@dataclass
class Observations:
    period: Period
    status: dict[str, SourceStatus] = field(default_factory=dict)
    # ① the platform's records
    work_pod: dict[tuple[str, str, str], WorkSeries] = field(default_factory=dict)
    #: (user, app, component, work): a job counts on the line of the process that ran it.
    work_user: dict[tuple[str, str, str, str], WorkSeries] = field(default_factory=dict)
    #: component → [slot, latency bucket] general requests (`req:` work) — per
    #: component, not per pod: only the component's p95 is ever read (pair a).
    hist: dict[str, np.ndarray] = field(default_factory=dict)
    sandbox_user: dict[tuple[str, str], Measures] = field(default_factory=dict)
    sandbox_pod: dict[tuple[str, str], Measures] = field(default_factory=dict)
    sandbox_items: dict[tuple[str, str], float] = field(default_factory=dict)
    host: dict[str, Measures] = field(default_factory=dict)
    #: (day index, scope, id, user, app) → bytes, the day's latest snapshot.
    disk: dict[tuple[int, str, str, str, str], float] = field(default_factory=dict)
    #: person → the epoch-ms day starts (UTC days) they did anything, from 30
    #: days before the period to its end: "N people" counts the people active
    #: in the 30 days up to each day (D12), which needs those 30 days.
    activity: dict[str, set[int]] = field(default_factory=dict)
    #: When the records began, if that is inside the 30 days before the period
    #: or the period itself (the first day anyone did anything); None when they
    #: go back further. Until 30 days are recorded, N counts only the people
    #: seen so far.
    recorded_since_ms: int | None = None
    #: Whether any sandbox row carried CPU pressure. A kernel without PSI
    #: writes none, and a sum over nothing must not read as "never waited".
    psi_seen: bool = False
    turns: Events = field(default_factory=Events)
    jobs: Events = field(default_factory=Events)
    dropped: float = 0.0
    # ② pods and machines: (component, pod) → resource → [slot] (NaN: no sample)
    pods: dict[tuple[str, str], Samples] = field(default_factory=dict)
    capacity: list[CapacityEntry] = field(default_factory=list)
    # ③ cards: model → util summed over its cards, memory used; cards per model
    gpu: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    cards: dict[str, int] = field(default_factory=dict)
    gpu_memory_total: dict[str, float] = field(default_factory=dict)
    # ④ LiteLLM
    llm: list[LlmSeries] = field(default_factory=list)
    latency: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    #: `days_with`, per source, once the observations are complete
    _days: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def connected(self, source: str) -> bool:
        s = self.status.get(source)
        return s is not None and s.connected and s.error is None

    def days_with(self, source: str) -> np.ndarray:
        """[day] whether `source` has anything that day. A day before a
        source began is not a quiet day — it is no day at all, and the
        analysis must not size on it. Worked out once per source: it walks
        every series the source filled."""
        cached = self._days.get(source)
        if cached is None:
            cached = self._days[source] = self._days_with(source)
            cached.setflags(write=False)
        return cached

    def _days_with(self, source: str) -> np.ndarray:
        p = self.period
        slots = np.zeros(p.n, dtype=bool)
        if source == "usage_records":
            for series in self.work_pod.values():
                slots[series.slots()] = True
            for m in self.sandbox_pod.values():
                slots[m.slots("cpu_s")] = True
        elif source == "resource_metrics":
            for m in self.pods.values():
                slots[m.slots("cpu")] = True
                slots[m.slots("memory")] = True
        elif source == "gpu_metrics":
            for m in self.gpu.values():
                slots |= (m["util"] > 0) | (m["memory"] > 0)
        elif source == "llm_requests":
            for s in self.llm:
                slots[s.slots()] = True
        else:
            raise ValueError(f"unknown source {source!r}")
        days = np.zeros(len(p.days), dtype=bool)
        days[np.unique(p.day_of[slots])] = True
        return days


def _zeros(p: Period) -> np.ndarray:
    return np.zeros(p.n)


def _finite(v: float) -> float:
    return v if math.isfinite(v) else 0.0


def _spread(p: Period, row: AggregateRow, into: Measures) -> None:
    """A sandbox row covers its own `window_s` — sandbox-host's window is its
    own setting. One longer than the slot, and a whole number of slots, is
    spread evenly over the slots it covers: put all in its first, a steady
    load reads double and zero by turn. (One that is neither a divisor nor a
    multiple of the slot cannot be placed exactly; `capacity check` says so.)"""
    i = p.index(row.t_ms or 0)
    if i is None:
        return
    try:
        window = int(float(row.keys.get("window_s") or 0))
    except (ValueError, OverflowError):  # not a number, or not a finite one
        window = 0
    span = window // p.step_s if window > p.step_s and window % p.step_s == 0 else 1
    share = {k: v / span for k, v in row.values.items()} if span > 1 else row.values
    for j in range(i, min(i + span, p.n)):
        into.add(j, share)


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
            series = obs.work_pod[key] = WorkSeries(p.n)
        series.add(i, r.values.get("count", 0.0), r.values.get("seconds", 0.0))
        if key[2].startswith("req:"):
            hist = obs.hist.get(key[1])
            if hist is None:
                hist = obs.hist[key[1]] = np.zeros((p.n, BUCKETS))
            hist[i] += [_finite(r.values.get(h, 0.0)) for h in HIST]
    for r in ask("work", ("user", "app", "component", "work"), ("count", "seconds")):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        key = (r.keys["user"] or "unknown", r.keys["app"], r.keys["component"], r.keys["work"])
        series = obs.work_user.get(key)
        if series is None:
            series = obs.work_user[key] = WorkSeries(p.n)
        series.add(i, r.values.get("count", 0.0), r.values.get("seconds", 0.0))
    measured = ("cpu_s", "mem_bs", "thr_s", "cpu_psi_s", "oom")
    for r in ask("sandbox", ("user", "app", "window_s"), measured):
        key = (r.keys["user"] or "unknown", r.keys["app"])
        acc = obs.sandbox_user.get(key)
        if acc is None:
            acc = obs.sandbox_user[key] = Measures(p.n, measured)
        _spread(p, r, acc)
    for r in ask("sandbox", (), maxes=("cpu_psi_s",), step_s=None):
        obs.psi_seen = obs.psi_seen or "cpu_psi_s" in r.values
    for r in ask("sandbox", ("pod", "component", "window_s"), ("cpu_s", "mem_bs")):
        key = (r.keys["pod"], r.keys["component"])
        acc = obs.sandbox_pod.get(key)
        if acc is None:
            acc = obs.sandbox_pod[key] = Measures(p.n, ("cpu_s", "mem_bs"))
        _spread(p, r, acc)
    for r in ask("sandbox", ("user", "item"), ("cpu_s",), step_s=None):
        obs.sandbox_items[(r.keys["user"], r.keys["item"])] = r.values.get("cpu_s", 0.0)
    for r in ask("sandbox_host", ("pod", "window_s"), ("live_s", "cpu_psi_s")):
        host = obs.host.get(r.keys["pod"])
        if host is None:
            host = obs.host[r.keys["pod"]] = Measures(p.n, ("live_s", "cpu_psi_s"))
        _spread(p, r, host)
    disk_keys = ("scope", "item", "collection", "user", "app")
    latest: dict[tuple[int, str, str, str, str], int] = {}
    for r in ask("disk", disk_keys, maxes=("bytes",), step_s=p.step_s):
        i = p.index(r.t_ms or 0)
        if i is None:
            continue
        k = r.keys
        ident = k["item"] or k["collection"]
        key = (int(p.day_of[i]), k["scope"], ident, k["user"] or "unknown", k["app"])
        # §5.2: a day's figure is its latest snapshot — not its largest
        if i >= latest.get(key, -1):
            latest[key] = i
            obs.disk[key] = r.values.get("bytes", 0.0)
    for r in ask("meta", ("pod",), ("dropped",), step_s=None):
        obs.dropped += r.values.get("dropped", 0.0)
    lookback = AggregateQuery(
        kind="work",
        start_ms=p.start_ms - (LOOKBACK_DAYS + PROOF_DAYS) * 86_400_000,
        end_ms=p.end_ms,
        step_s=86_400,
        group_by=("user",),
        sums=("count",),
    )
    for r in src.aggregate(lookback):
        rows += 1
        budget.take()
        if r.t_ms is not None and r.values.get("count", 0.0) > 0:
            obs.activity.setdefault(r.keys["user"] or "unknown", set()).add(r.t_ms)
    first = min((t for days in obs.activity.values() for t in days), default=None)
    window = p.start_ms - LOOKBACK_DAYS * 86_400_000
    obs.recorded_since_ms = first if first is not None and first >= window else None
    for r in src.records("turn", p.start_ms, p.end_ms):
        budget.take()
        accepted = r.get("accepted_ms")
        obs.turns.add(
            user=r.get("user"),
            item=r.get("item"),
            label=r.get("model"),
            at_ms=accepted,
            since_ms=accepted,
            until_ms=r.get("first_output_ms"),
        )
    for r in src.records("job", p.start_ms, p.end_ms):
        budget.take()
        started = r.get("started_ms")
        obs.jobs.add(
            user=r.get("user"),
            item=None,
            label=r.get("jobtype"),
            at_ms=started,
            since_ms=r.get("enqueued_ms"),
            until_ms=started,
        )
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
        key = (sample.component, sample.pod)
        acc = obs.pods.get(key)
        if acc is None:
            acc = obs.pods[key] = Samples(p.n, ("cpu", "memory", "disk"))
        acc.set(i, "cpu", sample.cpu_cores)
        acc.set(i, "memory", sample.memory_bytes)
        acc.set(i, "disk", sample.disk_bytes)
    for entry in src.capacity():
        budget.take()
        obs.capacity.append(entry)
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
        budget.take()
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
            s = series[key] = LlmSeries(*key, n=p.n)
        s.add(i, row.busy_seconds, row.requests)
    obs.llm = list(series.values())
    for row in src.latency(p.start_ms, p.end_ms, p.step_s):
        budget.take()
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
