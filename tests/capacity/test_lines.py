"""Each component × resource and each person's share (docs/plan-capacity-usage.md
§9.1, §9.3), against a world whose costs are known: the regression finds them,
the shares add up to what was measured, and direct lines are exactly measured."""

from __future__ import annotations

from functools import cache
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from workspace_app.capacity.analysis.lines import build_lines
from workspace_app.capacity.analysis.observe import observe
from workspace_app.capacity.analysis.period import Period
from workspace_app.capacity.sources import CapacitySources

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
