"""What N people would need (docs/plan-capacity-usage.md §9.6, E10): on people
whose days are known, the bootstrap's day peaks scale as they must, never
shrink as N grows, and the headroom is where the limit is crossed."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pytest

from workspace_app.capacity.analysis.lines import Line
from workspace_app.capacity.analysis.people import People
from workspace_app.capacity.analysis.period import Period
from workspace_app.capacity.analysis.whatif import Simulator, headroom

UTC = ZoneInfo("UTC")
T0 = 1_760_400_000_000 - 1_760_400_000_000 % 86_400_000  # a UTC midnight
STEP = 3600  # hourly slots keep the arrays small


def _period(days: int = 3) -> Period:
    return Period(T0, T0 + days * 86_400_000, STEP, UTC)


def _line(per_user: np.ndarray, fixed: float = 0.0, line_id: str = "api.cpu") -> Line:
    n = per_user.shape[1]
    total = per_user.sum(axis=0) + fixed
    return Line(
        id=line_id,
        component="api",
        resource="cpu",
        unit="cores",
        method="direct",
        total=total,
        fixed=np.full(n, fixed),
        users=[f"u{i}" for i in range(per_user.shape[0])],
        rows=np.arange(per_user.shape[0]),
        per_user=per_user.astype(np.float32),
    )


def _people(users: int, days: int, absent: int = 0) -> People:
    everyone = np.arange(users)
    return People(
        users=[f"u{i}" for i in range(users)],
        active_30d=np.full(days, users + absent),
        active_today=np.full(days, users),
        pool=[everyone for _ in range(days)],
        absent=np.full(days, absent),
    )


def _noon_bump(users: int, days: int, height: float = 1.0) -> np.ndarray:
    """Every person uses `height` cores from 11:00 to 14:00, every day."""
    hours = np.tile(np.arange(24), days)
    one = np.where((hours >= 11) & (hours < 14), height, 0.0)
    return np.tile(one, (users, 1))


def test_identical_people_peak_at_n_times_one():
    period = _period()
    sim = Simulator([_line(_noon_bump(10, 3), fixed=0.5)], _people(10, 3), period, days=[0, 1, 2])

    peaks = sim.run(25)["api.cpu"]

    assert np.allclose(peaks, 25 * 1.0 + 0.5)


def test_a_larger_n_never_needs_less():
    rng = np.random.default_rng(0)
    period = _period()
    per_user = _noon_bump(12, 3) * rng.lognormal(0, 1, (12, 1))
    sim = Simulator([_line(per_user)], _people(12, 3, absent=4), period, days=[0, 1, 2])

    p95 = [float(np.quantile(sim.run(n)["api.cpu"], 0.95)) for n in range(0, 80, 3)]

    assert all(b >= a for a, b in zip(p95, p95[1:], strict=False))


def test_a_simulator_of_small_days_can_still_be_asked_for_a_large_n():
    """The backtest asks the days before a day for that day's N. When a team
    joins at once, that N is far beyond the earlier days': the draw stretches
    rather than failing the page — and still never needs less for more."""
    period = _period()
    sim = Simulator([_line(_noon_bump(2, 3))], _people(2, 3), period, days=[0, 1, 2])

    small = sim.run(30)["api.cpu"]
    large = sim.run(300)["api.cpu"]

    assert np.allclose(large, 300 * 1.0)
    assert (large >= small).all()


def test_a_stretched_draw_does_not_depend_on_what_was_asked_before():
    rng = np.random.default_rng(1)
    per_user = _noon_bump(3, 3) * rng.lognormal(0, 1, (3, 1))
    a = Simulator([_line(per_user)], _people(3, 3, absent=2), _period(), days=[0, 1, 2])
    b = Simulator([_line(per_user)], _people(3, 3, absent=2), _period(), days=[0, 1, 2])

    a.run(200)
    first = a.run(500)["api.cpu"]
    second = b.run(500)["api.cpu"]

    assert np.array_equal(first, second)


def test_people_who_did_nothing_that_day_are_drawn_as_nothing():
    """N counts everyone active in 30 days; a day's absent ones draw as zero, so
    doubling N with half of it absent needs less than doubling the present."""
    period = _period()
    present_only = Simulator([_line(_noon_bump(10, 3))], _people(10, 3), period, days=[0, 1, 2])
    half_absent = Simulator(
        [_line(_noon_bump(10, 3))], _people(10, 3, absent=10), period, days=[0, 1, 2]
    )

    assert np.median(half_absent.run(40)["api.cpu"]) < np.median(present_only.run(40)["api.cpu"])


def test_the_heaviest_pool_needs_at_least_what_everyone_does():
    period = _period()
    per_user = _noon_bump(10, 3) * np.linspace(0.1, 2.0, 10)[:, None]
    people = _people(10, 3)
    heavy = np.array([9])
    everyone = Simulator([_line(per_user)], people, period, days=[0, 1, 2])
    heaviest = Simulator(
        [_line(per_user)], people, period, days=[0, 1, 2], pool=lambda present: heavy
    )

    assert np.median(heaviest.run(30)["api.cpu"]) >= np.median(everyone.run(30)["api.cpu"])


def test_the_headroom_is_where_the_limit_is_crossed():
    period = _period()
    sim = Simulator([_line(_noon_bump(10, 3), fixed=0.5)], _people(10, 3), period, days=[0, 1, 2])

    room = headroom(sim, {"api.cpu": 20.0})

    assert room.max_n == 19  # 19 × 1.0 + 0.5 fits under 20; 20 does not
    assert room.binding == "api.cpu"
    assert room.n_now == 10


def test_the_headroom_is_the_limit_even_just_below_the_furthest_it_looks():
    """Doubling from today's 10 passes 160 and would step past 200 (20× today),
    the furthest it looks. The limit is at 180: the answer is 180, not "200 or
    more" — that claim is only made when 200 itself fits."""
    period = _period()
    sim = Simulator([_line(_noon_bump(10, 3))], _people(10, 3), period, days=[0, 1, 2])

    room = headroom(sim, {"api.cpu": 180.5})

    assert room.by_line["api.cpu"] == 180
    assert headroom(sim, {"api.cpu": 1e9}).by_line["api.cpu"] == 200


def test_no_limit_no_headroom():
    period = _period()
    sim = Simulator([_line(_noon_bump(4, 3))], _people(4, 3), period, days=[0, 1, 2])

    assert headroom(sim, {}).max_n is None


@pytest.mark.parametrize("n", [0, 3])
def test_fewer_than_todays_n_draws_from_todays_people(n):
    period = _period()
    sim = Simulator([_line(_noon_bump(10, 3))], _people(10, 3), period, days=[0, 1, 2])

    assert np.allclose(sim.run(n)["api.cpu"], n * 1.0)


def test_a_run_reads_each_day_not_the_whole_period():
    """A run takes the day's demand of the day's people. Indexing the people
    first copied their whole period — every run, day and line — only to keep
    one day of it: the headroom's fifty-odd runs paid it over and over."""
    import tracemalloc

    days = 14
    rng = np.random.default_rng(3)
    per_user = _noon_bump(200, days) * rng.lognormal(0, 1, (200, 1))
    line = _line(per_user)
    sim = Simulator([line], _people(200, days), _period(days), days=list(range(days)), draws=20)
    sim.run(250)  # anything made once, made before counting
    tracemalloc.start()
    sim.run(250)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    assert peak < 0.5 * line.per_user.nbytes, f"{peak} bytes at the peak of one run"
