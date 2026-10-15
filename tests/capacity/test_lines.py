"""Each component × resource and each person's share (docs/plan-capacity-usage.md
§9.1, §9.3), against a world whose costs are known: the regression finds them,
the shares add up to what was measured, and direct lines are exactly measured."""

from __future__ import annotations

import gc
import tracemalloc
from functools import cache
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from workspace_app.capacity.analysis.lines import build_lines
from workspace_app.capacity.analysis.observe import (
    Measures,
    Observations,
    Samples,
    SourceStatus,
    WorkSeries,
    observe,
)
from workspace_app.capacity.analysis.period import Period
from workspace_app.capacity.sources import CapacityEntry, CapacitySources

from .world import (
    EVENTS,
    FILES,
    KB,
    SEND,
    STREAM,
    World,
    WorldGpus,
    WorldLlm,
    WorldPods,
    WorldRecords,
)


@cache
def _world() -> World:
    return World(days=10, users=20)


@cache
def _lines() -> dict:
    w = _world()
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))
    return {line.id: line for line in build_lines(obs)}


def test_the_regression_finds_what_each_kind_of_request_costs():
    w, api = _world(), _lines()["api.cpu"]
    assert api.fit is not None
    costs = dict(api.fit.columns)

    assert costs[FILES] == pytest.approx(w.costs[FILES], rel=0.15)
    assert costs["stream"] == pytest.approx(w.costs[STREAM], rel=0.15)
    # A send and its turn's events always come together (≈260 events a send):
    # only their sum is identifiable, and it is said so rather than guessed.
    per_send = costs[SEND] + 260 * costs[EVENTS]
    assert per_send == pytest.approx(w.costs[SEND] + 260 * w.costs[EVENTS], rel=0.15)
    assert (SEND, EVENTS) in api.fit.collinear or (EVENTS, SEND) in api.fit.collinear
    assert api.fit.r2 is not None and api.fit.r2 > 0.9
    assert api.fit.holdout_mape is not None and api.fit.holdout_mape < 0.1
    # The idle pods: fixed is their idle cost, wherever they were measured.
    assert np.median(api.fixed) == pytest.approx(w.api_idle_cores * len(w.api_pods), rel=0.15)
    assert KB in costs or "req:(other GET)" in costs


def test_a_persons_api_share_is_their_own_work_at_those_costs():
    w, api = _world(), _lines()["api.cpu"]
    u = api.users.index("user00")
    truth = sum(w.costs[k] * w.counts[k][0] for k in w.costs) / w.step_s

    assert api.per_user[u].sum() == pytest.approx(truth.sum(), rel=0.15)


@pytest.mark.parametrize(
    "line_id",
    [
        "api.cpu",
        "api.memory",
        "postgres.cpu",
        "sandbox-host.cpu",
        "sandbox-host.memory",
        "gpu.qwen3",
    ],
)
def test_every_share_adds_up_to_the_measured_total(line_id):
    line = _lines()[line_id]
    other = line.other if line.other is not None else 0.0

    rebuilt = line.fixed + line.variable + other + line.residual
    ok = np.isfinite(line.total)
    assert np.allclose(rebuilt[ok], line.total[ok], rtol=1e-4, atol=1e-6)
    by_app = sum(line.by_app.values())
    by_purpose = sum(line.by_purpose.values())
    assert np.allclose(by_app, line.variable, rtol=1e-4, atol=1e-6)
    assert np.allclose(by_purpose, line.variable, rtol=1e-4, atol=1e-6)


def test_a_sandbox_is_measured_not_estimated():
    w, sb = _world(), _lines()["sandbox-host.cpu"]
    u = sb.users.index("user05")

    assert np.allclose(sb.per_user[u], w.sandbox_cores[5], rtol=1e-4, atol=1e-6)
    assert sb.method == "direct"
    assert np.median(sb.fixed) == pytest.approx(0.3, rel=0.05), "the host's own overhead"
    assert sb.capacity == 8.0 and sb.machine_label == "node 8 cores / 32 GiB"


def test_a_gpu_splits_by_tag_and_keeps_other_systems_apart():
    w, gpu = _world(), _lines()["gpu.qwen3"]
    u = gpu.users.index("user01")

    assert np.allclose(gpu.per_user[u], w.gpu_busy[1] / w.step_s, rtol=1e-4, atol=1e-6)
    assert gpu.other is not None and np.allclose(gpu.other, w.other_busy / w.step_s)
    assert gpu.unit == "inflight"


def test_a_line_without_its_source_is_simply_not_there():
    w = _world()
    obs = observe(
        CapacitySources(WorldRecords(w)), Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))
    )

    ids = {line.id for line in build_lines(obs)}

    assert "api.cpu" not in ids, "a regression needs ②"
    assert {"sandbox-host.cpu", "filestore.disk"} <= ids


def _api_fit(records, pods, w: World):  # noqa: ANN001, ANN202
    obs = observe(
        CapacitySources(records, pods), Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))
    )
    return {line.id: line for line in build_lines(obs)}["api.cpu"].fit


def test_the_holdout_predicts_the_last_seven_days_from_the_ones_before():
    w = World(days=14, users=20)

    fit = _api_fit(WorldRecords(w), WorldPods(w), w)

    assert fit.holdout_days == 7


def test_a_rollout_in_the_held_out_days_does_not_ruin_the_holdout():
    """k8s renames pods on every rollout. A pod born in the held-out days had
    no training rows, so its idle cost was predicted as nothing and the error
    reported that, not the model: the rows of a pod training never saw are
    not scored."""
    import dataclasses

    w = World(days=10, users=20)
    roll = w.start_ms + 8 * 86_400_000
    rename = {"api-1": "api-1b", "api-2": "api-2b"}

    class Rolled(WorldRecords):
        def _kinds(self):  # noqa: ANN202
            return {
                k: [
                    (
                        at,
                        {**u, "pod": rename.get(u.get("pod", ""), u.get("pod"))}
                        if at >= roll
                        else u,
                    )
                    for at, u in v
                ]
                for k, v in super()._kinds().items()
            }

    class RolledPods(WorldPods):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN202
            return [
                dataclasses.replace(s, pod=rename.get(s.pod, s.pod)) if s.t_ms >= roll else s
                for s in super().usage(start_ms, end_ms, step_s)
            ]

    same = _api_fit(WorldRecords(w), WorldPods(w), w).holdout_mape
    rolled = _api_fit(Rolled(w), RolledPods(w), w).holdout_mape

    assert same is not None and rolled is not None
    assert rolled < 2 * same + 0.01


def test_routes_too_rare_for_their_own_column_merge_by_method():
    """§9.3: a POST and a DELETE cost differently; one "write" column made
    them one cost."""
    from workspace_app.capacity.analysis.lines import cpu_column

    named = [
        cpu_column(w, set())
        for w in ("req:GET /a", "req:POST /b", "req:DELETE /c", "req:PUT /d", "req:POST /e")
    ]
    columns = {col[0] for col in named if col is not None}

    assert columns == {
        "req:(other GET)",
        "req:(other POST)",
        "req:(other DELETE)",
        "req:(other PUT)",
    }


def _two_worker_types(days: int = 3):  # noqa: ANN202
    """Two worker Deployments — one indexes, one builds wikis — whose pods'
    CPU is their idle cost plus what each job-second costs (the truth)."""
    from workspace_app.capacity.sources import (
        CapacityEntry,
        ResourceMetricsSource,
        ResourceSample,
        aggregate_documents,
    )
    from workspace_app.capacity.usage_wire import document

    w = World(days=days, users=4)
    rng = np.random.default_rng(5)
    cost = {"index": 0.6, "wiki": 0.3}
    docs, samples = [], []
    for s in range(w.slots):
        at = w.start_ms + s * w.step_s * 1000
        for job, pod in (("index", "w-index-1"), ("wiki", "w-wiki-1")):
            busy = 0.0
            for u in range(4):
                seconds = float(rng.integers(0, 120))
                if seconds:
                    busy += seconds
                    fields = {
                        "user": f"user{u}",
                        "app": "",
                        "work": f"job:{job}",
                        "count": 1,
                        "seconds": seconds,
                        "window_s": w.step_s,
                    }
                    docs.append(document("work", at, pod=pod, component="worker", fields=fields))
                    waited = {"index": 1_000, "wiki": 60_000}[job]
                    jobf = {
                        "user": f"user{u}",
                        "jobtype": job,
                        "enqueued_ms": at - waited,
                        "started_ms": at,
                        "ended_ms": at + 1000,
                        "outcome": "done",
                        "attempt": 1,
                    }
                    docs.append(document("job", at, pod=pod, component="worker", fields=jobf))
            samples.append(
                ResourceSample(
                    at, "worker", pod, cpu_cores=0.1 + cost[job] * busy / w.step_s, memory_bytes=1e9
                )
            )

    class Records(WorldRecords):
        def aggregate(self, q):  # noqa: ANN001, ANN201
            return aggregate_documents(docs, q)

        def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
            return [d["usage"] for d in docs if d["usage"]["kind"] == kind]

    class Pods(ResourceMetricsSource):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return samples

        def capacity(self):  # noqa: ANN201
            return [CapacityEntry("worker", 8.0, 32e9, None, 2, "pod 4 cores / 16 GiB")]

    obs = observe(
        CapacitySources(Records(w), Pods()), Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))
    )
    return obs, cost


def test_each_job_type_is_a_worker_line_of_its_own():
    """§9.1: worker CPU and memory per job type — each worker Deployment
    scales on its own, and a slow graph queue is not an index queue's."""
    obs, cost = _two_worker_types()

    lines = {line.id: line for line in build_lines(obs)}

    for job in ("index", "wiki"):
        line = lines[f"worker.{job}.cpu"]
        assert line.job == job and line.fit is not None
        assert dict(line.fit.columns)[f"job:{job}"] == pytest.approx(cost[job], rel=0.05)
        # the worker entry, split by the pods each type runs on
        assert (line.capacity, line.machines) == (4.0, 1)
    assert "worker.cpu" not in lines


def test_each_job_type_has_its_own_queue_wait_pair():
    from workspace_app.capacity.analysis.thresholds import build_pairs

    obs, _ = _two_worker_types()
    lines = {line.id: line for line in build_lines(obs)}

    pairs = [p for p in build_pairs(obs, lines) if p.id == "e"]

    assert sorted((p.job, p.sets) for p in pairs) == [
        ("index", "worker.index.cpu"),
        ("wiki", "worker.wiki.cpu"),
    ]
    # each queue is its own: index jobs wait a second, wiki jobs a minute
    waits = {p.job: float(np.nanmax(p.y)) for p in pairs}
    assert waits == {"index": pytest.approx(1.0), "wiki": pytest.approx(60.0)}


def test_a_line_holds_a_row_only_for_each_person_it_costs():
    """Every line is per person per slot. A row for everyone any record names
    made a job type three people ran as large as the API's own line — and a
    deployment has a line per job type and per model."""
    t0 = 1_760_400_000_000 - 1_760_400_000_000 % 86_400_000
    p = Period(t0, t0 + 86_400_000, 3600, ZoneInfo("UTC"))
    obs = Observations(p, status={"usage_records": SourceStatus(connected=True)})
    for user in ("alice", "bob", "carol"):
        work = WorkSeries(p.n)
        work.add(10, 5.0, 0.1)
        obs.work_user[(user, "", "api", "req:GET /api/x")] = work
    used = Measures(p.n, ("cpu_s", "mem_bs", "thr_s", "cpu_psi_s", "oom"))
    used.add(10, {"cpu_s": 360.0})
    obs.sandbox_user[("bob", "")] = used

    line = {line.id: line for line in build_lines(obs)}["sandbox-host.cpu"]

    assert line.per_user.shape == (1, p.n)
    assert line.of(line.users.index("bob"))[10] == pytest.approx(0.1)
    assert not line.of(line.users.index("alice")).any()


def _build_peak(w: World) -> int:
    """The most memory `build_lines` holds at once for `w`'s API."""
    sources = CapacitySources(WorldRecords(w), WorldPods(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))
    gc.collect()
    tracemalloc.start()
    build_lines(obs)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak


def test_a_pod_that_lived_a_day_costs_the_regression_a_day():
    """Every rollout renames the pods. The regression's columns were a whole
    period per pod: dozens of pod names a month, each alive a day, each held
    a period of zeros per column."""
    static = _build_peak(World(days=7, users=6))
    rolled = _build_peak(World(days=7, users=6, rollout_days=1))

    assert rolled < 1.2 * static


def _workers(alive: dict[str, tuple[str, int]]) -> dict[str, float | None]:
    """Worker pods `pod → (job type, slots it lived)` sharing one `worker`
    entry of 8 cores on 2 machines: each job type's line's capacity."""
    t0 = 1_760_400_000_000 - 1_760_400_000_000 % 86_400_000
    p = Period(t0, t0 + 86_400_000, 3600, ZoneInfo("UTC"))
    connected = SourceStatus(connected=True)
    obs = Observations(p, status={"usage_records": connected, "resource_metrics": connected})
    rng = np.random.default_rng(0)
    for pod, (job, lived) in alive.items():
        samples = obs.pods[("worker", pod)] = Samples(p.n, ("cpu", "memory", "disk"))
        work = obs.work_pod[(pod, "worker", f"job:{job}")] = WorkSeries(p.n)
        for i in range(lived):
            busy = float(rng.uniform(100, 3000))
            work.add(i, 1, busy)
            samples.set(i, "cpu", 0.1 + busy / 3600)
            samples.set(i, "memory", 1e9)
    obs.capacity = [CapacityEntry("worker", 8.0, 16e9, None, 2, "pod 4 cores")]
    lines = build_lines(obs)
    return {line.job: line.capacity for line in lines if line.id.endswith(".cpu")}


def test_job_types_sharing_the_worker_entry_share_it_by_their_pods():
    """Three worker pods on two machines: pods are not machines, and each job
    type's share of the entry was its pods ÷ the machines — 1/2 and 2/2, half
    as much again as the entry holds."""
    got = _workers({"w1": ("index", 24), "w2": ("wiki", 24), "w3": ("wiki", 24)})

    assert got["index"] == pytest.approx(8.0 / 3)
    assert got["wiki"] == pytest.approx(16.0 / 3)


def test_a_job_type_whose_pods_all_stopped_gets_no_share():
    """Each job type was counted at its own last slot: the wiki workers that
    scaled to zero at noon still claimed the whole entry beside the indexer
    running now."""
    got = _workers({"w1": ("index", 24), "w2": ("wiki", 12), "w3": ("wiki", 12)})

    assert got["index"] == pytest.approx(8.0)
    assert got["wiki"] is None


def test_the_kept_request_templates_are_worked_out_once():
    """Which route templates are columns of their own is one question per
    analysis; every regression line walked every request series again."""
    import workspace_app.capacity.analysis.lines as lines_module

    w = _world()
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))
    calls = []
    real = lines_module._kept_templates

    def counted(o):  # noqa: ANN001, ANN202
        calls.append(1)
        return real(o)

    lines_module._kept_templates = counted
    try:
        built = build_lines(obs)
    finally:
        lines_module._kept_templates = real

    assert sum(1 for line in built if line.method == "regression") > 1
    assert len(calls) == 1
