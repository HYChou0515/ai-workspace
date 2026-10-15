"""What the sources say, as arrays over the period's slots
(docs/plan-capacity-usage.md §9.2): against a world whose numbers are known,
and with a source that fails — which fails only itself."""

from __future__ import annotations

import gc
import tracemalloc
from functools import cache
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from workspace_app.capacity.analysis.observe import TooManyRows, observe, parse_tags
from workspace_app.capacity.analysis.period import Period
from workspace_app.capacity.sources import (
    AggregateRow,
    CapacitySources,
    LlmLatencyRow,
    LlmRequestSource,
    LlmUsageRow,
    ResourceMetricsSource,
    UsageRecordSource,
)

from .world import FILES, World, WorldGpus, WorldLlm, WorldPods, WorldRecords


@cache
def _world() -> World:
    return World(days=7, users=12)


def _period(w: World) -> Period:
    return Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))


@cache
def _obs():
    w = _world()
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    return observe(sources, _period(w))


def test_a_week_has_seven_local_days_cut_at_local_midnight():
    p = _period(_world())

    assert len(p.days) == 7 and p.full_days == list(range(7))
    assert p.tod[0] == 0 and p.tod[p.slots_per_day] == 0


def test_every_source_is_connected_and_counted():
    obs = _obs()

    assert {k: (s.connected, s.error) for k, s in obs.status.items()} == {
        "usage_records": (True, None),
        "resource_metrics": (True, None),
        "gpu_metrics": (True, None),
        "llm_requests": (True, None),
    }
    assert all(s.rows > 0 for s in obs.status.values())


def test_a_persons_requests_add_up_to_the_worlds():
    w, obs = _world(), _obs()

    got = obs.work_user[("user03", w.apps[3], "api", FILES)].count
    assert np.array_equal(got, w.counts[FILES][3])


def test_the_pods_cards_and_traffic_are_there():
    w, obs = _world(), _obs()

    assert np.allclose(obs.pods[("api", "api-1")]["cpu"], w.api_cpu_seconds[0] / w.step_s)
    assert obs.cards == {w.gpu_model: w.gpu_cards}
    ours = sum(s.busy for s in obs.llm if s.ours)
    other = sum(s.busy for s in obs.llm if not s.ours)
    assert np.allclose(ours, w.gpu_busy.sum(axis=0))
    assert np.allclose(other, w.other_busy)


def test_a_failing_source_fails_only_itself():
    class _Broken(ResourceMetricsSource):
        def usage(self, start_ms, end_ms, step_s):
            raise ConnectionError("prometheus unreachable")

        def capacity(self):
            return []

    w = _world()
    obs = observe(CapacitySources(WorldRecords(w), _Broken()), _period(w))

    assert obs.status["resource_metrics"].error == "ConnectionError: prometheus unreachable"
    assert obs.connected("usage_records") and not obs.connected("resource_metrics")
    assert not obs.status["gpu_metrics"].connected


@pytest.mark.parametrize(
    ("tags", "want"),
    [
        (("ws", "ws.user:a%2Cb", "ws.app:pm"), {"ws": "1", "user": "a,b", "app": "pm"}),
        (("team-x",), {}),
    ],
)
def test_tags_parse_back_to_what_was_sent(tags, want):
    assert parse_tags(tags) == want


class _ManyRecords(UsageRecordSource):
    """Nothing to aggregate but one thing in bulk: `kind` records, or the
    activity lookback (rows of a day's step)."""

    def __init__(self, kind: str, n: int) -> None:
        self.kind, self.n = kind, n

    def aggregate(self, q):  # noqa: ANN001, ANN201
        if self.kind == "lookback" and q.step_s == 86_400:
            return (
                AggregateRow(q.start_ms, {"user": f"u{i}"}, {"count": 1.0}) for i in range(self.n)
            )
        return ()

    def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
        return ({"usage": {"user": "u"}} for _ in range(self.n)) if kind == self.kind else ()


class _ManyLatency(LlmRequestSource):
    def __init__(self, n: int) -> None:
        self.n = n

    def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
        return ()

    def latency(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
        return (LlmLatencyRow(start_ms, "m", 1, 0.1, 0.2, 30.0) for _ in range(self.n))


@pytest.mark.parametrize(
    "sources",
    [
        CapacitySources(_ManyRecords("turn", 10_000)),
        CapacitySources(_ManyRecords("job", 10_000)),
        CapacitySources(_ManyRecords("lookback", 10_000)),
        CapacitySources(llm_requests=_ManyLatency(10_000)),
    ],
    ids=["turns", "jobs", "lookback", "latency"],
)
def test_every_row_an_analysis_reads_counts_against_the_limit(sources):
    """`capacity.max_rows` is what keeps one analysis from taking the API pod's
    memory (#804): a row it does not count is a row with no bound."""
    with pytest.raises(TooManyRows):
        observe(sources, _period(_world()), max_rows=100)


class _ManyKeys(UsageRecordSource):
    """300 keys of every kind, one row each: a few rollouts' worth of pod names
    × the routes each served, and as many people — 600-odd rows in all."""

    def __init__(self, t_ms: int) -> None:
        self.t = t_ms

    def aggregate(self, q):  # noqa: ANN001, ANN201
        out = []
        for i in range(300):
            keys = {
                "pod": f"api-{i // 30}",
                "component": "api",
                "work": f"req:GET /api/r{i % 30}",
                "user": f"u{i}",
                "app": "a",
            }
            values = {"count": 1.0, "seconds": 0.1, "h0": 1.0, "cpu_s": 1.0, "mem_bs": 1.0}
            if q.kind in ("work", "sandbox") and q.step_s:
                # a field a row lacks groups as "" (the contract)
                out.append(AggregateRow(self.t, {g: keys.get(g, "") for g in q.group_by}, values))
        return out

    def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
        return ()


class _ManyCallers(LlmRequestSource):
    def __init__(self, t_ms: int) -> None:
        self.t = t_ms

    def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
        tags = lambda i: ("ws", f"ws.user:u{i}", "ws.app:a", "ws.purpose:chat")  # noqa: E731
        return (LlmUsageRow(self.t, "m", tags(i), 1, 10, 10, 1.0) for i in range(300))

    def latency(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
        return ()


def test_memory_follows_the_rows_read_not_keys_times_slots():
    """Each key used to get a whole period of slots, mostly zeros — a request
    histogram ten wide per pod and route, and arrays per person and kind of
    work. Pods renamed by every rollout and people multiply keys, so a few
    hundred rows held hundreds of MB in the API pod that serves everyone."""
    import tracemalloc

    w = _world()
    period = Period(w.start_ms, w.start_ms + 28 * 86_400_000, 300, ZoneInfo(w.tz))
    sources = CapacitySources(_ManyKeys(w.start_ms), llm_requests=_ManyCallers(w.start_ms))

    tracemalloc.start()
    obs = observe(sources, period, max_rows=5_000_000)
    held, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(obs.work_pod) == len(obs.work_user) == len(obs.sandbox_user) == 300
    assert held < 30e6, f"{held / 1e6:.0f} MB for {obs.status['usage_records'].rows} rows"


class _CoarseHost(UsageRecordSource):
    """A sandbox-host writing 600-s windows (SANDBOX_HOST_USAGE_WINDOW_S=600)
    for one sandbox using exactly one core all day, read at 300-s slots."""

    def __init__(self, t0: int, windows: int) -> None:
        from workspace_app.capacity.usage_wire import document

        self.docs = [
            document(
                "sandbox",
                t0 + k * 600_000,
                pod="sbh",
                component="sandbox-host",
                fields={"user": "u", "app": "a", "item": "i", "cpu_s": 600.0, "window_s": 600},
            )
            for k in range(windows)
        ]

    def aggregate(self, q):  # noqa: ANN001, ANN201
        from workspace_app.capacity.sources import aggregate_documents

        return aggregate_documents(self.docs, q)

    def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
        return ()


def test_a_row_longer_than_a_slot_is_spread_over_the_slots_it_covers():
    """One core all day is 300 core-seconds in every 300-s slot — not 600 in
    every other one, which doubles every peak."""
    w = _world()
    period = Period(w.start_ms, w.start_ms + 86_400_000, 300, ZoneInfo(w.tz))

    obs = observe(CapacitySources(_CoarseHost(w.start_ms, 144)), period)

    cpu = obs.sandbox_user[("u", "a")]["cpu_s"]
    assert np.allclose(cpu, 300.0)


def test_a_window_that_is_no_number_is_read_as_one_slot():
    """`window_s` comes from the record: an infinite one raised OverflowError,
    and one row failed every view that needs the records."""
    w = _world()
    period = Period(w.start_ms, w.start_ms + 86_400_000, 300, ZoneInfo(w.tz))
    source = _CoarseHost(w.start_ms, 2)
    source.docs[0]["usage"]["window_s"] = float("inf")

    obs = observe(CapacitySources(source), period)

    assert obs.status["usage_records"].error is None
    assert obs.sandbox_user[("u", "a")]["cpu_s"][0] == 600.0


class _Shrinking(UsageRecordSource):
    """A workspace measured at 10 GB in the morning and 8 GB in the evening."""

    def __init__(self, t0: int) -> None:
        from workspace_app.capacity.usage_wire import document

        f = {"scope": "workspace", "item": "i1", "user": "u", "app": "a"}
        self.docs = [
            document(
                "disk", t0 + 6 * 3_600_000, pod="w", component="worker", fields={**f, "bytes": 10e9}
            ),
            document(
                "disk", t0 + 18 * 3_600_000, pod="w", component="worker", fields={**f, "bytes": 8e9}
            ),
        ]

    def aggregate(self, q):  # noqa: ANN001, ANN201
        from workspace_app.capacity.sources import aggregate_documents

        return aggregate_documents(self.docs, q)

    def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
        return ()


def test_a_days_disk_is_its_last_figure_not_its_largest():
    """§5.2: a day's disk is the latest snapshot — files deleted in the
    afternoon are gone, whatever the morning said."""
    w = _world()
    period = Period(w.start_ms, w.start_ms + 86_400_000, 300, ZoneInfo(w.tz))

    obs = observe(CapacitySources(_Shrinking(w.start_ms)), period)

    assert list(obs.disk.values()) == [8e9]


def _retained(w: World) -> int:
    """Bytes an observation of `w`'s pods holds once observed."""
    sources = CapacitySources(resource_metrics=WorldPods(w))
    w.pod_samples()  # the world's own arrays, made before counting
    gc.collect()
    tracemalloc.start()
    obs = observe(sources, _period(w))
    gc.collect()
    held = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    assert obs.pods
    return held


def test_a_pod_that_lived_a_day_holds_a_day_of_samples():
    """Every rollout renames the pods: a month of daily rollouts is dozens of
    pod names, each alive a day. A whole period of slots per name made each
    rollout cost another period of memory, for the same samples."""
    static = _retained(World(days=7, users=4))
    rolled = _retained(World(days=7, users=4, rollout_days=1))

    assert rolled - static < 100_000
