"""One analysis of a world whose truth is known (docs/plan-capacity-usage.md §9, §10):
the knees are found where the world slows down, the GPU's capacity is where its
first token slows, the headroom and the backtest are there, and each view says
whether it is ready and why not."""

from __future__ import annotations

import gc
import json
import tracemalloc
from functools import cache
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from workspace_app.capacity.analysis.model import analyse
from workspace_app.capacity.analysis.observe import observe
from workspace_app.capacity.analysis.period import Period
from workspace_app.capacity.sources import CapacitySources, GpuSample

from .world import FILES, INDEX, KB, SEND, World, WorldGpus, WorldLlm, WorldPods, WorldRecords


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
    # a card, which the page names in its own language
    assert line["machines"] == w.gpu_cards and line["machine_label"] == ""


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
    week = _analysis(days=7)
    ready = week.summary()["readiness"]

    assert ready["whatif"]["reason"] == "period_too_short"
    assert (ready["whatif"]["period_days"], ready["whatif"]["need_days"]) == (7, 14)
    assert ready["distribution"]["ready"]
    assert ready["allocation"]["ready"]
    # and asked directly, it does not answer what it says it cannot
    assert week.whatif(50)["ready"] is False


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


def test_first_output_is_also_paired_with_each_models_load():
    """§9.8 (b): the wait for the first output against API CPU, and against
    each model's requests in flight — where people feel a busy GPU first."""
    w = _world()

    pairs = [p for p in _summary()["thresholds"] if p["id"] == "b" and p["model"] == w.gpu_model]

    (b,) = pairs
    assert b["sets"] == f"gpu.{w.gpu_model}"
    assert b["knee"] is not None
    assert b["knee"]["at"] == pytest.approx(w.gpu_knee_per_card * w.gpu_cards, rel=0.15)


def test_a_turn_names_its_model_with_the_provider_litellm_drops():
    """The platform asks for `openai/qwen3`; LiteLLM's logs name `qwen3`,
    the name the request reached it by. Compared as written, no turn was
    ever any model's, and the pair never appeared."""
    w = _world()

    class Prefixed(WorldRecords):
        def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
            rows = super().records(kind, start_ms, end_ms)
            if kind != "turn":
                return rows
            return [{**r, "model": f"openai/{r['model']}"} for r in rows]

    sources = CapacitySources(Prefixed(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    assert [p for p in s["thresholds"] if p["id"] == "b" and p["model"] == w.gpu_model]


def test_a_models_first_output_pair_takes_only_that_models_turns():
    w = _world()

    class OtherModel(WorldRecords):
        def records(self, kind, start_ms, end_ms):  # noqa: ANN001, ANN201
            rows = super().records(kind, start_ms, end_ms)
            return [{**r, "model": "elsewhere"} for r in rows] if kind == "turn" else rows

    sources = CapacitySources(OtherModel(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    assert not [p for p in s["thresholds"] if p["id"] == "b" and p["model"] == w.gpu_model]


def test_an_hour_with_no_machine_samples_is_listed_as_a_gap():
    """§9.9: coverage, and the gaps — a missing hour of ② is an hour whose
    peak nobody saw, so view 9 names it."""
    w = _world()
    hole = (
        w.start_ms + 2 * 86_400_000 + 10 * 3_600_000,
        w.start_ms + 2 * 86_400_000 + 11 * 3_600_000,
    )

    class Holed(WorldPods):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [
                s
                for s in super().usage(start_ms, end_ms, step_s)
                if not hole[0] <= s.t_ms < hole[1]
            ]

    sources = CapacitySources(WorldRecords(w), Holed(w), WorldGpus(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    assert s["confidence"]["gaps"]["resource_metrics"] == [list(hole)]
    assert s["confidence"]["gaps"]["gpu_metrics"] == []


def test_the_share_of_work_nobody_asked_for_is_reported():
    """E4: `system` is a row of its own, and its share is in view 9. Work with
    nobody behind it is a job or a turn no person started — the share is of
    their running time (requests carry `system` only for probes)."""
    w = _world()

    class TheirIndexingIsSystem(WorldRecords):
        def _kinds(self):  # noqa: ANN202
            def relabel(u: dict) -> dict:
                ran = str(u.get("work", "")).startswith(("job:", "turn:"))
                return {**u, "user": "system"} if ran and u.get("user") == "user00" else u

            return {k: [(at, relabel(u)) for at, u in v] for k, v in super()._kinds().items()}

    sources = CapacitySources(TheirIndexingIsSystem(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    index = w.counts[INDEX]
    assert s["confidence"]["system_share"] == pytest.approx(index[0].sum() / index.sum(), rel=1e-6)


def test_the_unidentified_share_is_of_the_api_requests_people_made():
    """Static files are no person's to identify, and a probe is system work:
    with either in the count, "x% could not be attributed" moved with page
    loads and with how many pods were probed."""
    w = _world()
    t0 = w.start_ms + 86_400_000 + 10 * 3_600_000

    class WithStrangers(WorldRecords):
        def _kinds(self):  # noqa: ANN202
            kinds = super()._kinds()

            def row(user: str, work: str, count: int) -> tuple[int, dict]:
                return t0, {
                    "kind": "work",
                    "pod": "api-1",
                    "component": "api",
                    "user": user,
                    "app": "",
                    "work": work,
                    "count": count,
                    "seconds": 0.0,
                    "window_s": w.step_s,
                }

            extra = [
                row("unknown", "req:GET (static)", 5000),
                row("system", "req:GET /api/readyz", 7000),
                row("unknown", "req:GET /api/public", 300),
            ]
            return {**kinds, "work": [*kinds["work"], *extra]}

    sources = CapacitySources(WithStrangers(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    people = sum(float(w.counts[k].sum()) for k in (FILES, SEND, KB))
    assert s["confidence"]["unknown_share"] == pytest.approx(300 / (people + 300), rel=1e-6)


def test_the_headroom_says_how_many_days_it_sized_on():
    """The page says what the headroom rests on: the workdays it drew from,
    not the period's length."""
    s = _summary()

    workdays = [d for d in s["period"]["full_days"] if d in s["period"]["workdays"]]
    assert s["headroom"]["days"] == len(workdays)


def test_a_persons_rank_is_among_people_not_the_unknown_and_system_rows():
    """ "Uses more than x% of people": the system row is not a person, so it is
    no part of anyone's rank — and has no rank among people itself."""
    w = _world()

    class OneIsSystem(WorldRecords):
        def _kinds(self):  # noqa: ANN202
            return {
                k: [
                    (at, {**u, "user": "system"} if u.get("user") == "user00" else u) for at, u in v
                ]
                for k, v in super()._kinds().items()
            }

    sources = CapacitySources(OneIsSystem(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    a = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    )
    line = a.by_id["sandbox-host.cpu"]
    people = [u for u in a.people.users if u not in ("unknown", "system", "")]
    totals = {u: float(line.of(a.people.users.index(u)).sum()) for u in people}

    for u in people:
        detail = a.person(u)
        assert detail is not None
        (mine,) = [x for x in detail["lines"] if x["id"] == "sandbox-host.cpu"]
        below = sum(1 for v in totals.values() if v < totals[u])
        assert mine["rank"] == pytest.approx(below / (len(people) - 1)), u
    system = a.person("system")
    assert system is not None
    assert {x["rank"] for x in system["lines"]} == {None}


@cache
def _weekend_batch():  # noqa: ANN202
    """Another system's weekend batch makes the shared GPU's weekends its
    busiest days; the platform's own load is a workday thing."""
    w = World(days=14, users=40, gpu_cards=6, other_weekend_batch=2.2)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    obs = observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz)))
    return analyse(obs, now_ms=w.end_ms)


def test_a_lines_peak_rests_on_the_days_the_headroom_does():
    """The overview's peak, utilisation and "holds" sit in one row: a peak
    taken over weekends the headroom never sizes on read 140% beside "holds
    more people than today"."""
    a = _weekend_batch()
    s = a.summary()
    gpu = next(line for line in s["lines"] if line["id"] == "gpu.qwen3")

    sized = [gpu["daily"]["peak"][d] for d in a.sizing_days]
    assert gpu["peak"] == pytest.approx(float(np.quantile(sized, 0.95)))


def test_a_line_that_does_not_grow_with_people_is_not_judged_by_the_what_if():
    """A model's preallocated GPU memory or the database's memory is the same
    for any N: holding it to a safe threshold says "short" at every N, which
    reads as "buy more" for something more people would not use."""
    out = _full().whatif(80)

    fixed = [r for r in out["lines"] if r["id"] in ("postgres.memory", "gpu.qwen3.memory")]
    assert fixed
    for r in fixed:
        assert r["fixed"] is True
        assert r["needed_p95"] == pytest.approx(r["p95"])
        assert r["fits"] == {"p50": None, "p95": None}
        assert r["machines_needed"] == {"p50": None, "p95": None}


def test_asking_every_what_if_profile_copies_nobodys_demand():
    """The page asks for the heaviest tenth and for one App's people beside
    everyone. A simulator per profile that copied the people's day demand,
    and drew its random numbers ahead for twenty times today's N, held the
    period several times over in the API process."""
    a = _analysis(days=21)  # fresh: nothing asked of it yet
    held = sum(line.per_user.nbytes for line in a.lines)
    gc.collect()
    tracemalloc.start()
    for profile, app in (("all", ""), ("heavy", ""), ("app", "pm"), ("app", "rca")):
        a.whatif(80, profile, app)
    gc.collect()
    grown = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    assert grown < 0.25 * held


def test_a_source_that_stopped_before_the_end_has_a_gap_to_the_end():
    """A source that died two hours ago left no later slot with something in
    it: scanning only up to its last sample, the gap it left was no gap."""
    from workspace_app.capacity.analysis.model import find_gaps

    p = Period(0, 24 * 3_600_000, 300, ZoneInfo("UTC"))
    present = np.ones(p.n, dtype=bool)
    present[-24:] = False

    assert find_gaps(p, present) == [[p.end_ms - 2 * 3_600_000, p.end_ms]]


def test_readiness_walks_each_sources_series_once():
    """The trends view counted days by asking every source for all its days
    again for each day of the period: the whole period's series walked once
    a day, then again on every summary and every what-if."""
    a = _analysis(days=28)
    walked: list[str] = []
    real = a.obs._days_with

    def counted(source: str) -> np.ndarray:
        walked.append(source)
        return real(source)

    a.obs._days_with = counted
    a.readiness()
    a.summary()
    a.whatif(50)

    assert len(walked) == len(set(walked))


def test_coverage_counts_the_slots_the_gaps_do_not():
    """A source's coverage and its gaps are one question: a slot with a sample
    is covered and no gap. An idle GPU (utilisation 0, the model still in its
    memory) was a gap-free slot that coverage called missing."""
    w = _world()

    class Idle(WorldGpus):
        def usage(self, start_ms, end_ms, step_s):  # noqa: ANN001, ANN201
            return [
                GpuSample(s.t_ms, s.card, s.model, 0.0, s.memory_bytes)
                for s in super().usage(start_ms, end_ms, step_s)
            ]

    sources = CapacitySources(WorldRecords(w), WorldPods(w), Idle(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    assert s["confidence"]["gaps"]["gpu_metrics"] == []
    assert s["confidence"]["coverage"]["gpu_metrics"] == 1.0


def test_no_headroom_is_served_while_the_what_if_cannot_say():
    """View 1 states the headroom as view 6 does (§9.10). With too few people
    the headline said it could not tell, while the rows under it drew people
    per line and the line that binds first — and the runway dated an order
    by them."""
    w = World(days=20, users=6)
    sources = CapacitySources(WorldRecords(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    s = analyse(
        observe(sources, Period(w.start_ms, w.end_ms, w.step_s, ZoneInfo(w.tz))), now_ms=w.end_ms
    ).summary()

    assert s["readiness"]["whatif"]["reason"] == "too_few_people"
    assert s["headroom"] is None
    assert s["runway"]["reach"] is None


def test_no_knee_is_used_before_the_thresholds_view_can_show_it():
    """Four days in, view 8 says it needs seven; the overview still called a
    knee from those four days the API's measured threshold, and a model's
    capacity."""
    s = _analysis(days=4).summary()
    lines = {line["id"]: line for line in s["lines"]}

    assert s["readiness"]["thresholds"]["reason"] == "period_too_short"
    assert lines["api.cpu"]["threshold_source"] == "default"
    assert lines["gpu.qwen3"]["capacity"] is None
