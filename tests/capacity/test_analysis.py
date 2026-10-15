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


def test_a_what_if_beyond_its_reach_says_which_n_it_answered():
    """The what-if looks at most 20× today's N; asked for more, it answers for
    that many and says so, rather than letting the page show another N's needs
    under the number that was typed."""
    n_now = _summary()["people"]["n_now"]

    out = _full().whatif(1_000_000)

    assert out["n_asked"] == 1_000_000
    assert out["n"] == out["n_max"] == n_now * 20


def test_the_heaviest_profile_needs_at_least_what_everyone_does():
    n = _summary()["people"]["n_now"] * 2
    everyone = {r["id"]: r["p50"] for r in _full().whatif(n)["lines"]}
    heavy = {r["id"]: r["p50"] for r in _full().whatif(n, "heavy")["lines"]}

    assert heavy["api.cpu"] >= everyone["api.cpu"]


def test_the_backtest_predicts_the_last_two_weeks_peaks():
    backtest = _summary()["confidence"]["backtest"]

    assert backtest is not None and len(backtest["days"]) >= 5
    assert backtest["mape"] < 0.25


@cache
def _long_recorded() -> dict:
    """Records that began long before the period, so every day's N is whole."""
    w = World(days=60, users=40)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    period = Period(w.end_ms - 28 * 86_400_000, w.end_ms, w.step_s, ZoneInfo(w.tz))
    return analyse(observe(sources, period), now_ms=w.end_ms).summary()


def test_every_view_is_ready_with_everything_connected():
    ready = _long_recorded()["readiness"]

    assert {k: v["reason"] for k, v in ready.items() if not v["ready"]} == {}
    assert _long_recorded()["people"]["complete"] is True


def test_while_the_first_30_days_fill_no_growth_is_read_into_them():
    """Records began with this period: 'used in the last 30 days' counts only
    the people seen so far, so it rises with no one new. No trend is drawn
    from it, and the page is told the count is still short."""
    s = _summary()

    assert s["people"]["complete"] is False
    assert s["runway"]["trend"] is None
    assert s["readiness"]["runway"]["reason"] == "accumulating"
    assert s["readiness"]["runway"]["have_days"] == 0


def test_days_before_the_records_began_are_not_drawn_as_quiet_days():
    """A day with no records yet is not a day nobody worked: sizing on it
    would dilute the peaks and promise room that is not there."""
    w = World(days=21, users=40)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    zone = ZoneInfo(w.tz)
    own = analyse(observe(sources, Period(w.start_ms, w.end_ms, w.step_s, zone)), now_ms=w.end_ms)
    wider = Period(w.end_ms - 28 * 86_400_000, w.end_ms, w.step_s, zone)

    padded = analyse(observe(sources, wider), now_ms=w.end_ms)

    assert own.headroom is not None and padded.headroom is not None
    assert padded.headroom.max_n == own.headroom.max_n


def test_without_pods_and_machines_the_views_that_need_them_say_so():
    w = _world()
    summary = _analysis(CapacitySources(WorldRecords(w))).summary()

    ready = summary["readiness"]
    assert ready["whatif"]["reason"] == "source_missing"
    assert ready["whatif"]["missing"] == ["resource_metrics"]
    assert ready["allocation"]["ready"]


def test_a_week_long_period_cannot_size_purchases_and_says_so():
    """However much data there is, a 7-day period holds 7 days: the what-if
    needs 14, so it asks for a longer period rather than promising to appear."""
    ready = _analysis(days=7).summary()["readiness"]

    assert ready["whatif"]["reason"] == "period_too_short"
    assert (ready["whatif"]["period_days"], ready["whatif"]["need_days"]) == (7, 14)
    assert ready["distribution"]["ready"]
    assert ready["allocation"]["ready"]


def test_a_week_of_data_in_a_four_week_period_is_still_accumulating():
    w = World(days=7, users=40)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    period = Period(w.end_ms - 28 * 86_400_000, w.end_ms, w.step_s, ZoneInfo(w.tz))

    ready = analyse(observe(sources, period), now_ms=w.end_ms).summary()["readiness"]

    assert ready["whatif"]["reason"] == "accumulating"
    assert (ready["whatif"]["have_days"], ready["whatif"]["need_days"]) == (7, 14)
    assert ready["whatif"]["period_days"] == 28


def test_with_only_pods_and_machines_the_overview_counts_their_days():
    """The overview needs ② alone; its days are ②'s days, not the records'."""
    w = _world()

    ready = _analysis(CapacitySources(resource_metrics=WorldPods(w))).summary()["readiness"]

    assert ready["overview"]["ready"], ready["overview"]
    assert ready["overview"]["have_days"] == 28
    assert ready["trends"]["ready"], ready["trends"]


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


def _pairs(w: World) -> dict[str, dict]:
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))
    return {p["id"]: p for p in analyse(obs, now_ms=w.end_ms).summary()["thresholds"]}


def test_sandboxes_wait_for_a_busy_machine_where_the_world_makes_them():
    """Sixty people push the host past 50%, where the world makes them wait."""
    d = _pairs(World(days=14, users=60))["d"]

    assert d["note"] is None
    assert d["knee"] is not None and d["knee"]["at"] == pytest.approx(0.5, abs=0.05)


def test_sandboxes_held_at_their_own_limit_do_not_read_as_a_busy_machine():
    """A sandbox at its own cpu.max is throttled, and the kernel counts that as
    pressure too (E15). Left in, it draws a knee that says "buy machines" where
    the machine had CPU to spare; it is shown on its own instead."""
    d = _pairs(World(days=14, users=40, sandbox_host_cores=16.0, throttle_from=0.25))["d"]

    assert d["knee"] is None
    assert max(y for _x, y in d["points"]) < 1e-6
    held = np.array(d["throttled"])
    # the world throttles each live sandbox 0.5 × (load − 0.25) of the time
    assert np.allclose(held[:, 1], 0.5 * np.clip(held[:, 0] - 0.25, 0, None), atol=1e-3)
    assert held[:, 1].max() > 0


def test_without_pressure_on_the_kernel_the_page_is_told_it_is_not_measured():
    d = _pairs(World(days=14, users=40, psi=False))["d"]

    assert d["note"] == "no_psi"
    assert d["knee"] is None and d["points"] == []


def test_the_what_if_keeps_a_few_profiles_not_every_one_asked():
    """Each profile's simulator copies every person's day demand: kept for
    every App asked about, they add up in the API process without end."""
    a = _analysis()
    for i in range(10):
        a.whatif(5, "app", f"app-{i}")

    assert len(a._sims) <= 4  # noqa: SLF001


@pytest.mark.parametrize(("days", "ready"), [(2, False), (3, True)])
def test_lines_split_by_regression_wait_for_three_days_the_direct_ones_do_not(days, ready):
    """§9.10: direct lines after a day; a regression needs three days of both
    sources to say what each kind of work costs."""
    w = World(days=days, users=20)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))

    readiness = analyse(obs, now_ms=w.end_ms).summary()["readiness"]

    assert readiness["allocation"]["ready"]
    assert readiness["regression"]["ready"] is ready
    assert (readiness["regression"]["have_days"], readiness["regression"]["need_days"]) == (days, 3)


def test_the_sandbox_scratch_volume_is_a_disk_line_of_its_own():
    """§9.1: filestore and scratch each get a disk line; ② reported scratch
    and the analysis dropped it."""
    from workspace_app.capacity.sources import CapacityEntry, ResourceSample

    w = _world()

    class WithScratch(WorldPods):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            out = list(super().usage(start_ms, end_ms, step_s))
            out += [ResourceSample(s.t_ms, "scratch", "nfs", disk_bytes=5e10) for s in out[::5]]
            return out

        def capacity(self):  # noqa: ANN201
            return [*super().capacity(), CapacityEntry("scratch", None, None, 2e11, 1, "volume")]

    sources = CapacitySources(WorldRecords(w), WithScratch(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    (line,) = [line for line in s["lines"] if line["id"] == "scratch.disk"]
    assert (line["capacity"], line["peak"]) == (2e11, pytest.approx(5e10))
    assert "scratch.disk" in [d["line"] for d in s["runway"]["disk"]]


def test_postgres_disk_growth_is_given_per_active_person_per_month():
    """§9.1 (E16): Postgres disk is not split per person; its growth is, per
    active person per month — what one more person costs in disk."""
    s = _long_recorded()

    (pg,) = [d for d in s["runway"]["disk"] if d["line"] == "postgres.disk"]
    # the world grows Postgres 1e5 bytes a slot: 288 slots a day
    assert pg["per_person_month"] == pytest.approx(288e5 * 30 / s["people"]["n_now"], rel=0.01)
