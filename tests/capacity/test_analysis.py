"""One analysis of a world whose truth is known (docs/plan-capacity-usage.md §9, §10):
the knees are found where the world slows down, the GPU's capacity is where its
first token slows, the headroom and the backtest are there, and each view says
whether it is ready and why not."""

from __future__ import annotations

import json
from functools import cache
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from workspace_app.capacity.analysis.model import analyse
from workspace_app.capacity.analysis.observe import observe
from workspace_app.capacity.analysis.period import Period
from workspace_app.capacity.sources import CapacitySources

from .world import World, WorldGpus, WorldLlm, WorldPods, WorldRecords


@cache
def _world() -> World:
    return World(days=28, users=40)


def _analysis(sources: CapacitySources | None = None, days: int = 28):  # noqa: ANN202
    w = _world()
    sources = sources or CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    end = w.start_ms + days * 86_400_000
    obs = observe(sources, Period(w.start_ms, end, w.step_s, ZoneInfo(w.tz)))
    return analyse(obs, now_ms=end)


@cache
def _full():  # noqa: ANN202
    return _analysis()


@cache
def _summary() -> dict:
    return _full().summary()


def test_the_summary_is_plain_json():
    assert json.loads(json.dumps(_summary()))["period"]["timezone"] == "Asia/Taipei"


def test_the_requests_knee_is_where_the_world_slows_down():
    pair = next(p for p in _summary()["thresholds"] if p["id"] == "a")

    assert pair["knee"] is not None
    assert pair["knee"]["at"] == pytest.approx(0.35, abs=0.06)
    line = next(line for line in _summary()["lines"] if line["id"] == "api.cpu")
    assert line["threshold_source"] == "knee"
    assert line["threshold"] == pytest.approx(0.35, abs=0.06)


def test_a_models_capacity_is_its_in_flight_level_where_first_tokens_slow():
    w = _world()
    line = next(line for line in _summary()["lines"] if line["id"] == "gpu.qwen3")

    assert line["capacity"] == pytest.approx(w.gpu_knee_per_card * w.gpu_cards, rel=0.15)
    assert line["machines"] == w.gpu_cards and line["machine_label"] == "GPU card"


def test_a_line_with_no_knee_says_its_threshold_is_a_default():
    line = next(line for line in _summary()["lines"] if line["id"] == "postgres.cpu")

    assert (line["threshold"], line["threshold_source"]) == (0.7, "default")


def test_the_headroom_names_the_line_that_binds_first():
    room = _summary()["headroom"]

    assert room is not None and room["max_n"] is not None
    assert room["binding"] in room["by_line"]
    assert room["by_line"][room["binding"]] == room["max_n"]
    assert room["n_now"] == _summary()["people"]["n_now"]


def test_a_larger_n_needs_more_and_says_how_many_machines():
    a = _full().whatif(_summary()["people"]["n_now"])
    b = _full().whatif(_summary()["people"]["n_now"] * 3)

    by_id = {r["id"]: r for r in a["lines"]}
    more = {r["id"]: r for r in b["lines"]}
    assert more["api.cpu"]["p95"] > by_id["api.cpu"]["p95"]
    assert more["sandbox-host.cpu"]["machines_needed"]["p95"] >= 1


def test_the_heaviest_profile_needs_at_least_what_everyone_does():
    n = _summary()["people"]["n_now"] * 2
    everyone = {r["id"]: r["p50"] for r in _full().whatif(n)["lines"]}
    heavy = {r["id"]: r["p50"] for r in _full().whatif(n, "heavy")["lines"]}

    assert heavy["api.cpu"] >= everyone["api.cpu"]


def test_the_backtest_predicts_the_last_two_weeks_peaks():
    backtest = _summary()["confidence"]["backtest"]

    assert backtest is not None and len(backtest["days"]) >= 5
    assert backtest["mape"] < 0.25


def test_every_view_is_ready_with_everything_connected():
    ready = _summary()["readiness"]

    assert {k: v["reason"] for k, v in ready.items() if not v["ready"]} == {}


def test_without_pods_and_machines_the_views_that_need_them_say_so():
    w = _world()
    summary = _analysis(CapacitySources(WorldRecords(w))).summary()

    ready = summary["readiness"]
    assert ready["whatif"]["reason"] == "source_missing"
    assert ready["whatif"]["missing"] == ["resource_metrics"]
    assert ready["allocation"]["ready"]


def test_a_week_is_not_enough_to_size_purchases():
    ready = _analysis(days=7).summary()["readiness"]

    assert ready["whatif"]["reason"] == "accumulating"
    assert (ready["whatif"]["have_days"], ready["whatif"]["need_days"]) == (7, 14)
    assert ready["allocation"]["ready"]


def test_one_persons_detail():
    detail = _full().person("user00")

    assert detail is not None
    lines = {line["id"]: line for line in detail["lines"]}
    assert lines["api.cpu"]["rank"] >= 0.9, "user00 is one of the two heaviest"
    assert detail["apps"] == ["pm"]
    assert any(item.get("sandbox_cpu_s", 0) > 0 for item in detail["items"])
    assert _full().person("nobody") is None


def test_shares_in_the_summary_add_up():
    line = next(line for line in _summary()["lines"] if line["id"] == "api.cpu")
    people = sum(g["avg"] for g in line["groups"]["person"])

    total = line["fixed"]["avg"] + people + line["residual"]["avg"]
    assert total == pytest.approx(line["total"]["avg"], rel=1e-3)
    assert np.isclose(sum(g["avg"] for g in line["groups"]["app"]), people, rtol=1e-3)
