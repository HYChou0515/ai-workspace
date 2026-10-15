"""A deployment with known truth, for the capacity analysis (docs/plan-capacity-usage.md).

People with a daily rhythm and a heavy tail use the platform; every number a
source returns is generated from per-kind costs this module states, so a test
can ask whether the analysis recovers them. The same world backs the demo:
`DemoRecords`, `DemoPods`, `DemoGpus` and `DemoLlm` are zero-argument sources a
`config.yaml` can name (`capacity.usage_records: tests.capacity.world.DemoRecords`).

Deterministic: one seed, one world.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from zoneinfo import ZoneInfo

import numpy as np

from workspace_app.capacity.records import LATENCY_BOUNDS_MS
from workspace_app.capacity.sources import (
    AggregateQuery,
    AggregateRow,
    CapacityEntry,
    GpuCard,
    GpuMetricsSource,
    GpuSample,
    LlmLatencyRow,
    LlmRequestSource,
    LlmUsageRow,
    ResourceMetricsSource,
    ResourceSample,
    UsageRecordSource,
    aggregate_parsed,
)
from workspace_app.capacity.usage_wire import document

FILES = "req:GET /api/a/{slug}/items/{item_id}/files"
SEND = "req:POST /api/a/{slug}/items/{item_id}/chats/{chat_id}/messages"
KB = "req:GET /api/kb/collections"
STREAM = "stream:/api/a/{slug}/items/{item_id}/stream"
EVENTS = "turn-events:chat"
TURN = "turn:chat"
INDEX = "job:index"

GIB = 1024**3


@dataclass
class World:
    days: int = 28
    step_s: int = 300
    users: int = 40
    seed: int = 7
    tz: str = "Asia/Taipei"
    #: A Monday, so the first five days are workdays.
    first_day: dt.date = dt.date(2026, 9, 14)
    api_pods: tuple[str, ...] = ("api-1", "api-2")
    #: CPU-seconds per request, per event, per connection-second (the truth).
    costs: dict[str, float] = field(
        default_factory=lambda: {FILES: 0.3, SEND: 1.0, KB: 0.2, EVENTS: 0.012, STREAM: 0.01}
    )
    #: Cores each API pod uses doing nothing.
    api_idle_cores: float = 0.1
    api_cores_per_pod: float = 0.6
    #: Worker cores per second of indexing.
    index_cores: float = 0.8
    gpu_model: str = "qwen3"
    gpu_cards: int = 2
    #: In-flight requests per card past which the GPU slows (the truth view 8 should find).
    gpu_knee_per_card: float = 0.35
    #: Other systems' mean in-flight requests on the same model (the GPU is shared).
    other_busy_mean: float = 0.4
    #: Extra in-flight requests other systems' weekend batch adds, 13:00-18:00 —
    #: load past the knee that the workday sizing never sees.
    other_weekend_batch: float = 0.0
    sandbox_host_cores: float = 8.0
    #: Host utilisation past which busy sandboxes start hitting their own
    #: `cpu.max`: throttled time, which the kernel counts as pressure too,
    #: though the machine has CPU to spare (E15).
    throttle_from: float | None = None
    #: Whether the kernel offers pressure (PSI); without it no row carries it.
    psi: bool = True
    #: The last `joining` people start partway through, one after another, so the
    #: number of people grows (what the runway view extrapolates).
    joining: int = 0
    #: Every this many days a rollout renames the API pods (0: never) — a
    #: month of daily rollouts is dozens of pod names, each alive a day.
    rollout_days: int = 0

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @cached_property
    def start_ms(self) -> int:
        local = dt.datetime.combine(self.first_day, dt.time(0), tzinfo=self.zone)
        return int(local.timestamp() * 1000)

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.days * 86_400_000

    @property
    def slots(self) -> int:
        return self.days * 86_400 // self.step_s

    @cached_property
    def names(self) -> list[str]:
        return [f"user{i:02d}" for i in range(self.users)]

    @cached_property
    def apps(self) -> list[str]:
        return ["rca" if i % 3 else "pm" for i in range(self.users)]

    @cached_property
    def intensity(self) -> np.ndarray:
        """How heavy each person is: a lognormal tail, the first two the heaviest."""
        rng = np.random.default_rng(self.seed)
        x = rng.lognormal(0.0, 0.6, self.users)
        x[:2] *= 4.0
        return x

    @cached_property
    def active(self) -> np.ndarray:
        """[user, slot] → whether the person is at work in that slot."""
        rng = np.random.default_rng(self.seed + 1)
        t = self.start_ms / 1000 + np.arange(self.slots) * self.step_s
        local = [dt.datetime.fromtimestamp(s, self.zone) for s in t]
        hour = np.array([d.hour + d.minute / 60 for d in local])
        workday = np.array([d.weekday() < 5 for d in local], dtype=float)
        rhythm = np.exp(-(((hour - 11) / 2.2) ** 2)) + 0.85 * np.exp(-(((hour - 15.5) / 2.0) ** 2))
        rhythm = rhythm * (0.15 + 0.85 * workday)
        p = np.clip(
            0.55 * rhythm[None, :] * np.minimum(1.0, 0.4 + 0.3 * self.intensity[:, None]), 0, 1
        )
        # A third of the people skip a given day: absent days are part of the truth.
        day = (np.arange(self.slots) * self.step_s) // 86_400
        present = rng.random((self.users, self.days)) > 0.33
        for k in range(self.joining):
            present[self.users - self.joining + k, : (k + 1) * self.days // (self.joining + 1)] = (
                False
            )
        return (rng.random(p.shape) < p) & present[:, day]

    @cached_property
    def counts(self) -> dict[str, np.ndarray]:
        """[user, slot] counts per kind of work (stream: connection-seconds)."""
        rng = np.random.default_rng(self.seed + 2)
        a = self.active.astype(float)
        k = self.intensity[:, None]
        sends = rng.poisson(0.5 * k * a)
        return {
            FILES: rng.poisson(5.0 * k * a),
            SEND: sends,
            KB: rng.poisson(0.8 * a),
            EVENTS: rng.poisson(260.0 * sends),
            STREAM: a * self.step_s,
            INDEX: rng.poisson(0.05 * k * a) * 60.0,  # one minute of indexing per job
        }

    @cached_property
    def pod_of(self) -> np.ndarray:
        """[user, slot] → which API pod served that person in that slot."""
        rng = np.random.default_rng(self.seed + 3)
        return rng.integers(0, len(self.api_pods), (self.users, self.slots))

    # ── truth, per slot ──────────────────────────────────────────────────
    @cached_property
    def api_cpu_seconds(self) -> np.ndarray:
        """[pod, slot] CPU-seconds the API pods really used."""
        rng = np.random.default_rng(self.seed + 4)
        out = np.full((len(self.api_pods), self.slots), self.api_idle_cores * self.step_s)
        for work, cost in self.costs.items():
            for p in range(len(self.api_pods)):
                out[p] += cost * (self.counts[work] * (self.pod_of == p)).sum(axis=0)
        return out * rng.normal(1.0, 0.03, out.shape)

    @cached_property
    def api_util(self) -> np.ndarray:
        return (
            self.api_cpu_seconds.sum(axis=0)
            / self.step_s
            / (self.api_cores_per_pod * len(self.api_pods))
        )

    @cached_property
    def sandbox_cores(self) -> np.ndarray:
        """[user, slot] cores each person's sandbox used."""
        rng = np.random.default_rng(self.seed + 5)
        return self.active * rng.gamma(
            2.0, 0.08 * self.intensity[:, None], (self.users, self.slots)
        )

    @cached_property
    def sandbox_memory(self) -> np.ndarray:
        return self.active * (300e6 + 150e6 * self.intensity[:, None])

    @cached_property
    def gpu_busy(self) -> np.ndarray:
        """[user, slot] seconds this platform's requests were in flight."""
        return self.counts[SEND] * 3 * 4.0  # three requests a turn, four seconds each

    @cached_property
    def other_busy(self) -> np.ndarray:
        """[slot] other systems' in-flight seconds on the same model."""
        rng = np.random.default_rng(self.seed + 6)
        busy = np.clip(rng.normal(self.other_busy_mean, 0.15, self.slots), 0, None)
        if self.other_weekend_batch:
            t = self.start_ms / 1000 + np.arange(self.slots) * self.step_s
            local = [dt.datetime.fromtimestamp(x, self.zone) for x in t]
            batch = np.array([d.weekday() >= 5 and 13 <= d.hour < 18 for d in local])
            busy = busy + self.other_weekend_batch * batch
        return busy * self.step_s

    @cached_property
    def gpu_inflight(self) -> np.ndarray:
        return (self.gpu_busy.sum(axis=0) + self.other_busy) / self.step_s

    # ── as the sources report it ─────────────────────────────────────────
    def _at(self, slot: int) -> int:
        return self.start_ms + slot * self.step_s * 1000

    def pod(self, p: int, slot: int) -> str:
        """API pod `p`'s name in `slot`: renamed by every rollout."""
        if not self.rollout_days:
            return self.api_pods[p]
        return f"{self.api_pods[p]}-r{slot * self.step_s // 86_400 // self.rollout_days}"

    @cached_property
    def documents(self) -> list[dict]:
        docs: list[dict] = []
        rng = np.random.default_rng(self.seed + 7)
        for u, user in enumerate(self.names):
            app = self.apps[u]
            for s in np.flatnonzero(self.active[u]):
                at = self._at(int(s))
                pod = self.pod(int(self.pod_of[u, s]), int(s))
                util = self.api_util[s]
                for work in (FILES, SEND, KB):
                    n = int(self.counts[work][u, s])
                    if not n:
                        continue
                    # Requests slow down past 35% API CPU: the knee view 8 should find.
                    median = 80 * (1 + 15 * max(0.0, util - 0.35)) * (3 if work == SEND else 1)
                    lat = rng.lognormal(np.log(median), 0.5, n)
                    hist = np.bincount(np.searchsorted(LATENCY_BOUNDS_MS, lat), minlength=10)
                    fields = {
                        "user": user,
                        "app": app,
                        "work": work,
                        "count": n,
                        "seconds": round(float(lat.sum()) / 1000, 6),
                        "window_s": self.step_s,
                    }
                    fields |= {f"h{i}": int(c) for i, c in enumerate(hist) if c}
                    docs.append(document("work", at, pod=pod, component="api", fields=fields))
                for work, seconds in ((STREAM, float(self.counts[STREAM][u, s])),):
                    docs.append(
                        document(
                            "work",
                            at,
                            pod=pod,
                            component="api",
                            fields={
                                "user": user,
                                "app": app,
                                "work": work,
                                "count": 1,
                                "seconds": seconds,
                                "window_s": self.step_s,
                            },
                        )
                    )
                if events := int(self.counts[EVENTS][u, s]):
                    docs.append(
                        document(
                            "work",
                            at,
                            pod=pod,
                            component="api",
                            fields={
                                "user": user,
                                "app": app,
                                "work": EVENTS,
                                "count": events,
                                "seconds": 0.0,
                                "window_s": self.step_s,
                            },
                        )
                    )
                    sends = int(self.counts[SEND][u, s])
                    for _ in range(sends):
                        accepted = at + int(rng.integers(0, self.step_s * 1000))
                        ttft = 0.8 + 3.0 * max(
                            0.0, self.gpu_inflight[s] / self.gpu_cards - self.gpu_knee_per_card
                        )
                        started = accepted + 50
                        first_event = started + 300
                        first_output = first_event + int(ttft * 1000)
                        docs.append(
                            document(
                                "turn",
                                accepted,
                                pod=pod,
                                component="api",
                                fields={
                                    "user": user,
                                    "app": app,
                                    "item": f"{user}-item",
                                    "surface": "chat",
                                    "outcome": "done",
                                    "accepted_ms": accepted,
                                    "started_ms": started,
                                    "first_event_ms": first_event,
                                    "first_output_ms": first_output,
                                    "ended_ms": first_output + 20_000,
                                    "model": self.gpu_model,
                                },
                            )
                        )
                if busy := float(self.counts[INDEX][u, s]):
                    docs.append(
                        document(
                            "work",
                            at,
                            pod="worker-index-1",
                            component="worker",
                            fields={
                                "user": user,
                                "app": "",
                                "work": INDEX,
                                "count": int(busy // 60),
                                "seconds": busy,
                                "window_s": self.step_s,
                            },
                        )
                    )
                    wait = 2_000 + int(30_000 * max(0.0, self.worker_util[s] - 0.5))
                    for _ in range(int(busy // 60)):
                        docs.append(
                            document(
                                "job",
                                at,
                                pod="worker-index-1",
                                component="worker",
                                fields={
                                    "user": user,
                                    "jobtype": "index",
                                    "enqueued_ms": at - wait,
                                    "started_ms": at,
                                    "ended_ms": at + 60_000,
                                    "outcome": "done",
                                    "attempt": 1,
                                },
                            )
                        )
                cores = float(self.sandbox_cores[u, s])
                if cores:
                    waited = self.step_s * 0.02 * max(0.0, self.host_util[s] - 0.5) * 10
                    throttled = (
                        self.step_s * 0.5 * max(0.0, self.host_util[s] - self.throttle_from)
                        if self.throttle_from is not None
                        else 0.0
                    )
                    fields = {
                        "item": f"{user}-item",
                        "user": user,
                        "app": app,
                        "sandbox": f"sb-{user}",
                        "cpu_s": round(cores * self.step_s, 6),
                        "mem_bs": float(self.sandbox_memory[u, s]) * self.step_s,
                        "mem_max": int(self.sandbox_memory[u, s]),
                        "thr_s": round(throttled, 6),
                        "thr_n": 0.0,
                        "periods": 0.0,
                        "cpu_psi_s": round(waited + throttled, 6),
                        "mem_psi_s": 0.0,
                        "oom": 0.0,
                        "cpu_limit": 2.0,
                        "mem_limit": 2 * GIB,
                        "window_s": self.step_s,
                    }
                    if not self.psi:
                        del fields["cpu_psi_s"], fields["mem_psi_s"]
                    docs.append(
                        document(
                            "sandbox",
                            at,
                            pod="sandbox-host-1",
                            component="sandbox-host",
                            fields=fields,
                        )
                    )
        for s in range(self.slots):
            live = float((self.sandbox_cores[:, s] > 0).sum()) * self.step_s
            docs.append(
                document(
                    "sandbox_host",
                    self._at(s),
                    pod="sandbox-host-1",
                    component="sandbox-host",
                    fields={"live_s": live, "window_s": self.step_s}
                    | ({"cpu_psi_s": 0.0} if self.psi else {}),
                )
            )
        for day in range(self.days):
            at = self.start_ms + day * 86_400_000
            for u, user in enumerate(self.names):
                size = int(2e8 * self.intensity[u] * (1 + day / 20))
                docs.append(
                    document(
                        "disk",
                        at,
                        pod="worker-gc",
                        component="worker",
                        fields={
                            "scope": "workspace",
                            "item": f"{user}-item",
                            "user": user,
                            "app": self.apps[u],
                            "bytes": size,
                        },
                    )
                )
        return docs

    @cached_property
    def worker_util(self) -> np.ndarray:
        return (self.counts[INDEX].sum(axis=0) / self.step_s * self.index_cores + 0.1) / 4.0

    @cached_property
    def host_util(self) -> np.ndarray:
        return (self.sandbox_cores.sum(axis=0) + 0.3) / self.sandbox_host_cores

    def pod_samples(self) -> list[ResourceSample]:
        out: list[ResourceSample] = []
        sends = self.counts[SEND].sum(axis=0)
        files = self.counts[FILES].sum(axis=0)
        for s in range(self.slots):
            at = self._at(s)
            for p in range(len(self.api_pods)):
                pod = self.pod(p, s)
                streams = (
                    float((self.counts[STREAM][:, s] * (self.pod_of[:, s] == p)).sum())
                    / self.step_s
                )
                out.append(
                    ResourceSample(
                        at,
                        "api",
                        pod,
                        cpu_cores=float(self.api_cpu_seconds[p, s]) / self.step_s,
                        memory_bytes=0.6 * GIB + 8e6 * streams,
                    )
                )
            out.append(
                ResourceSample(
                    at,
                    "worker",
                    "worker-index-1",
                    cpu_cores=float(self.worker_util[s]) * 4.0,
                    memory_bytes=1.2 * GIB,
                )
            )
            out.append(
                ResourceSample(
                    at,
                    "postgres",
                    "postgres-0",
                    cpu_cores=0.2 + (0.002 * files[s] + 0.01 * sends[s]) / self.step_s,
                    memory_bytes=4 * GIB,
                    disk_bytes=40 * GIB + s * 1e5,
                )
            )
            out.append(
                ResourceSample(
                    at,
                    "sandbox-host",
                    "sandbox-host-1",
                    cpu_cores=float(self.sandbox_cores[:, s].sum()) + 0.3,
                    memory_bytes=float(self.sandbox_memory[:, s].sum()) + 0.5 * GIB,
                )
            )
        return out

    def capacity(self) -> list[CapacityEntry]:
        return [
            CapacityEntry(
                "api",
                self.api_cores_per_pod * len(self.api_pods),
                8 * GIB,
                None,
                len(self.api_pods),
                "pod 2 cores / 4 GiB",
            ),
            CapacityEntry("worker", 4.0, 8 * GIB, None, 1, "pod 4 cores / 8 GiB"),
            CapacityEntry("postgres", 4.0, 16 * GIB, 200 * GIB, 1, "node 4 cores / 16 GiB"),
            CapacityEntry(
                "sandbox-host",
                self.sandbox_host_cores,
                32 * GIB,
                None,
                1,
                f"node {self.sandbox_host_cores:g} cores / 32 GiB",
            ),
            CapacityEntry("filestore", None, None, 500 * GIB, 1, "volume 500 GiB"),
        ]

    def gpu_samples(self) -> list[GpuSample]:
        out = []
        per_card = self.gpu_inflight / self.gpu_cards
        for s in range(self.slots):
            util = float(min(1.0, 0.25 + 0.6 * min(per_card[s], 1.0)))  # saturates: E5
            for c in range(self.gpu_cards):
                out.append(GpuSample(self._at(s), f"gpu{c}", self.gpu_model, util, 70 * GIB))
        return out

    def cards(self) -> list[GpuCard]:
        return [
            GpuCard(f"gpu{c}", "gpu-node-1", self.gpu_model, 80 * GIB)
            for c in range(self.gpu_cards)
        ]

    def llm_rows(self) -> list[LlmUsageRow]:
        out = []
        for s in range(self.slots):
            at = self._at(s)
            for u in np.flatnonzero(self.gpu_busy[:, s]):
                busy = float(self.gpu_busy[u, s])
                tags = (
                    "ws",
                    f"ws.user:{self.names[u]}",
                    f"ws.app:{self.apps[u]}",
                    "ws.purpose:chat",
                    "ws.call:agent",
                )
                out.append(LlmUsageRow(at, self.gpu_model, tags, int(busy // 4), 4000, 300, busy))
            out.append(
                LlmUsageRow(at, self.gpu_model, (), 10, 9000, 900, float(self.other_busy[s]))
            )
        return out

    def latency_rows(self) -> list[LlmLatencyRow]:
        out = []
        per_card = self.gpu_inflight / self.gpu_cards
        for s in range(self.slots):
            slow = max(0.0, per_card[s] - self.gpu_knee_per_card)
            out.append(
                LlmLatencyRow(
                    self._at(s),
                    self.gpu_model,
                    20,
                    0.5 + slow,
                    0.8 + 3.0 * slow,
                    max(2.0, 40.0 - 12.0 * slow),
                )
            )
        return out


# ── the world as the four sources ───────────────────────────────────────────
class WorldRecords(UsageRecordSource):
    """The world's records, parsed once and kept by kind (a stand-in for ES)."""

    def __init__(self, world: World) -> None:
        self.world = world
        self._by_kind: dict[str, list[tuple[int, dict]]] | None = None

    def _kinds(self) -> dict[str, list[tuple[int, dict]]]:
        if self._by_kind is None:
            from workspace_app.capacity.sources import _epoch_ms

            out: dict[str, list[tuple[int, dict]]] = {}
            for doc in self.world.documents:
                at = _epoch_ms(doc["@timestamp"])
                out.setdefault(doc["usage"]["kind"], []).append((at, doc["usage"]))
            self._by_kind = out
        return self._by_kind

    def aggregate(self, q: AggregateQuery) -> list[AggregateRow]:
        return aggregate_parsed(self._kinds().get(q.kind, []), q)

    def records(self, kind: str, start_ms: int, end_ms: int) -> Iterable[dict]:
        return [usage for at, usage in self._kinds().get(kind, []) if start_ms <= at < end_ms]


def _in(rows, start_ms: int, end_ms: int):  # noqa: ANN001, ANN202
    return [r for r in rows if start_ms <= r.t_ms < end_ms]


def _buckets(rows, step_s: int, world_step_s: int, key, merge):  # noqa: ANN001, ANN202
    """The world's rows at a coarser step, as a source asked for one returns
    them: one row per (bucket, key), `merge`d from the rows it holds."""
    if step_s == world_step_s:
        return rows
    step_ms = step_s * 1000
    groups: dict = {}
    for row in rows:
        groups.setdefault((row.t_ms - row.t_ms % step_ms, key(row)), []).append(row)
    return [merge(bucket, group) for (bucket, _k), group in groups.items()]


def _mean(values) -> float | None:  # noqa: ANN001
    kept = [v for v in values if v is not None]
    return sum(kept) / len(kept) if kept else None


class WorldPods(ResourceMetricsSource):
    def __init__(self, world: World) -> None:
        self.world = world

    def usage(self, start_ms: int, end_ms: int, step_s: int) -> list[ResourceSample]:
        rows = _in(self.world.pod_samples(), start_ms, end_ms)
        return _buckets(
            rows,
            step_s,
            self.world.step_s,
            lambda r: (r.component, r.pod),
            lambda t, g: ResourceSample(
                t,
                g[0].component,
                g[0].pod,
                cpu_cores=_mean(r.cpu_cores for r in g),
                memory_bytes=_mean(r.memory_bytes for r in g),
                disk_bytes=max((r.disk_bytes for r in g if r.disk_bytes is not None), default=None),
            ),
        )

    def capacity(self) -> list[CapacityEntry]:
        return self.world.capacity()


class WorldGpus(GpuMetricsSource):
    def __init__(self, world: World) -> None:
        self.world = world

    def usage(self, start_ms: int, end_ms: int, step_s: int) -> list[GpuSample]:
        rows = _in(self.world.gpu_samples(), start_ms, end_ms)
        return _buckets(
            rows,
            step_s,
            self.world.step_s,
            lambda r: (r.card, r.model),
            lambda t, g: GpuSample(
                t,
                g[0].card,
                g[0].model,
                sum(r.util for r in g) / len(g),
                sum(r.memory_bytes for r in g) / len(g),
            ),
        )

    def cards(self) -> list[GpuCard]:
        return self.world.cards()


class WorldLlm(LlmRequestSource):
    def __init__(self, world: World) -> None:
        self.world = world

    def usage(self, start_ms: int, end_ms: int, step_s: int) -> list[LlmUsageRow]:
        rows = _in(self.world.llm_rows(), start_ms, end_ms)
        return _buckets(
            rows,
            step_s,
            self.world.step_s,
            lambda r: (r.model, r.tags),
            lambda t, g: LlmUsageRow(
                t,
                g[0].model,
                g[0].tags,
                sum(r.requests for r in g),
                sum(r.prompt_tokens for r in g),
                sum(r.completion_tokens for r in g),
                sum(r.busy_seconds for r in g),
            ),
        )

    def latency(self, start_ms: int, end_ms: int, step_s: int) -> list[LlmLatencyRow]:
        rows = _in(self.world.latency_rows(), start_ms, end_ms)
        return _buckets(
            rows,
            step_s,
            self.world.step_s,
            lambda r: r.model,
            lambda t, g: LlmLatencyRow(
                t,
                g[0].model,
                sum(r.requests for r in g),
                _mean(r.ttft_p50_s for r in g),
                max((r.ttft_p95_s for r in g if r.ttft_p95_s is not None), default=None),
                _mean(r.tokens_per_s_p50 for r in g),
            ),
        )


# ── zero-argument sources for a demo config ─────────────────────────────────
_DEMO: World | None = None


def demo_world() -> World:
    """One shared world ending today, so every period the dashboard offers has
    data, recorded long enough (70 days) for the runway: 70 people, 20 of whom
    joined along the way; a sandbox host with room to grow into, and a shared
    GPU that other systems' weekend batch pushes past its knee — so the GPU's
    capacity is measured while the workdays still fit."""
    global _DEMO
    if _DEMO is None:
        today = dt.datetime.now(ZoneInfo("Asia/Taipei")).date()
        _DEMO = World(
            days=70,
            users=70,
            first_day=today - dt.timedelta(days=70),
            joining=20,
            api_cores_per_pod=2.0,
            sandbox_host_cores=16.0,
            gpu_cards=6,
            other_busy_mean=0.3,
            other_weekend_batch=2.2,
        )
    return _DEMO


class DemoRecords(WorldRecords):
    def __init__(self) -> None:
        super().__init__(demo_world())


class DemoPods(WorldPods):
    def __init__(self) -> None:
        super().__init__(demo_world())


class DemoGpus(WorldGpus):
    def __init__(self) -> None:
        super().__init__(demo_world())


class DemoLlm(WorldLlm):
    def __init__(self) -> None:
        super().__init__(demo_world())
