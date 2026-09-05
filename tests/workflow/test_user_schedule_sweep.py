"""The sweep that fires a page's own schedules.

Everything it needs already exists — the index says which items to read, the
window ledger says what has already fired, and `is_due` says whether the current
period's moment has passed. This is the piece that joins them, and its whole job
is to be boring:

* read a SHORT list, never every item
* survive one broken file without dropping everyone else's schedules
* claim before firing, so two pods produce one run
* drop what it cannot read, which is what lets deletes have no hook
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from specstar import SpecStar

from workspace_app.api.schedule_index import (
    SCHEDULES_FILE,
    ScheduleIndex,
    register_schedule_index,
)
from workspace_app.resources import make_spec
from workspace_app.workflow.triggers import register_trigger_store
from workspace_app.workflow.user_schedule_sweep import UserScheduleSweeper

ITEM = "i1"
PAGE = "/scrap-review"
PATH = f"{PAGE}/{SCHEDULES_FILE}"

DAILY = {"every": "daily", "at": "09:00", "run": "build-report", "with": {"line": "A"}}
NOON = {"every": "daily", "at": "12:00", "run": "build-report"}


class _Files:
    """The item's files, as a double. Raises for what is not there — the shape
    the real read has, because "gone" is the case the sweep must handle."""

    def __init__(self, **files: str):
        self.files = dict(files)

    async def read(self, item_id: str, path: str) -> bytes:
        try:
            return self.files[f"{item_id}{path}"].encode()
        except KeyError:
            raise FileNotFoundError(path) from None


class _Started:
    """Records what was launched, so a test asserts on the LAUNCH rather than on
    a status somebody set."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str, str, dict]] = []

    async def __call__(self, *, item_id: str, workflow_id: str, acting_user: str, payload: dict):
        self.runs.append((item_id, workflow_id, acting_user, payload))
        return "run-1"


def _spec() -> SpecStar:
    """Both models the sweep touches. Registered here rather than relied on from
    a lifespan, so a missing one fails the test instead of being absorbed by the
    sweep's own per-item resilience — which is exactly what happened first."""
    s = make_spec(default_user="alice")
    register_schedule_index(s)
    register_trigger_store(s)
    return s


@pytest.fixture
def spec() -> SpecStar:
    return _spec()


def _sweeper(spec: SpecStar, files: _Files, started: _Started, now: datetime):
    return UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        now=lambda: now,
    )


def _file(*rows: dict) -> str:
    return json.dumps({"schedules": list(rows)})


# ── firing ───────────────────────────────────────────────────────────────────


def test_a_due_schedule_starts_its_workflow_with_its_payload():
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()

    asyncio.run(
        _sweeper(
            spec, _Files(**{f"{ITEM}{PATH}": _file(DAILY)}), started, datetime(2026, 9, 5, 9, 30)
        ).tick()
    )

    assert started.runs == [(ITEM, "build-report", "alice", {"line": "A"})]


def test_a_schedule_whose_moment_has_not_come_does_not_fire():
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()

    asyncio.run(
        _sweeper(
            spec, _Files(**{f"{ITEM}{PATH}": _file(NOON)}), started, datetime(2026, 9, 5, 9, 30)
        ).tick()
    )

    assert started.runs == []


def test_losing_the_claim_stops_the_fire_even_though_the_ledger_looked_clear():
    """What the SECOND pod sees, and the only situation the claim is for.

    In one process the ledger read always wins: there is no await between
    reading it and claiming, so a second sweep in the same process has already
    seen the first one's write. Two PODS have no such ordering — both read an
    empty ledger, then both try to claim, and only the CAS decides. Simulated
    here rather than reasoned about, with a store that answers the way a losing
    pod is answered: "nobody has fired this" followed by "you did not get it".

    Without this, a mutation that fired without checking the claim passed every
    test in the file — including one that ran two sweeps concurrently, because
    asyncio interleaves at the awaits and there is none in the gap that matters.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    sweeper = _sweeper(
        spec, _Files(**{f"{ITEM}{PATH}": _file(DAILY)}), started, datetime(2026, 9, 5, 9, 30)
    )
    sweeper._store.last_window = lambda _tid: ""  # type: ignore[method-assign]
    sweeper._store.try_claim = lambda _tid, _w: False  # type: ignore[method-assign]

    asyncio.run(sweeper.tick())

    assert started.runs == []


def test_an_already_fired_window_costs_no_write():
    """The ledger read and the claim are BOTH correct on their own, which is why
    a mutation removing either one passed. What the read buys is that a sweep
    over a schedule that has already fired attempts no write at all — and at one
    sweep a minute, per schedule, that is the difference between a quiet ledger
    and a hot one."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})
    now = datetime(2026, 9, 5, 9, 30)

    sweeper = _sweeper(spec, files, started, now)
    asyncio.run(sweeper.tick())

    claims: list[str] = []
    real_claim = sweeper._store.try_claim

    def _counting(trigger_id: str, window: str) -> bool:
        claims.append(trigger_id)
        return real_claim(trigger_id, window)

    sweeper._store.try_claim = _counting  # type: ignore[method-assign]
    asyncio.run(sweeper.tick())

    assert claims == []


def test_the_same_window_fires_once_however_often_the_sweep_runs():
    """The sweep wakes every minute; a daily schedule must not fire sixty times
    an hour. The claim is the whole mechanism, and it is the SAME claim the
    engineer-authored triggers use."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    for _ in range(3):
        asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert len(started.runs) == 1


def test_the_next_day_is_a_new_window():
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())
    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 6, 9, 30)).tick())

    assert len(started.runs) == 2


def test_a_missed_window_fires_late_rather_than_being_dropped():
    """The catch-up rule, inherited unchanged: due is "this period's target has
    passed AND it has not fired for this window", not "it is exactly 09:00". A
    pod that was down at nine still sends the report at half past ten."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()

    asyncio.run(
        _sweeper(
            spec, _Files(**{f"{ITEM}{PATH}": _file(DAILY)}), started, datetime(2026, 9, 5, 10, 30)
        ).tick()
    )

    assert len(started.runs) == 1


# ── surviving what a page wrote ──────────────────────────────────────────────


def test_one_unreadable_file_does_not_stop_the_others():
    """The sweep reads every item that has schedules. One page's broken JSON
    must not cost every other item its schedules — the failure mode that would
    be discovered weeks later, by somebody asking why their report stopped."""
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record("bad", f"{PAGE}/{SCHEDULES_FILE}")
    index.record("good", f"{PAGE}/{SCHEDULES_FILE}")
    started = _Started()
    files = _Files(
        **{
            f"bad{PATH}": "{ this is not json",
            f"good{PATH}": _file(DAILY),
        }
    )

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert [r[0] for r in started.runs] == ["good"]


def test_an_invalid_row_is_skipped_and_its_neighbours_still_fire():
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file({"every": "fortnightly", "run": "x"}, DAILY)})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert [r[1] for r in started.runs] == ["build-report"]


def test_a_deleted_file_drops_out_of_the_index():
    """The index is stale in one direction on purpose: a delete has no hook, so
    the sweep is what notices. Leaving the path behind would make every future
    pass pay for a file that is gone."""
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)
    started = _Started()

    asyncio.run(_sweeper(spec, _Files(), started, datetime(2026, 9, 5, 9, 30)).tick())

    assert index.items() == []


def test_a_failing_start_does_not_stop_the_sweep():
    """One item's workflow refusing to launch must not hold up everyone else's
    — the same per-item resilience the mirror and reaper sweeps keep."""

    class _Boom(_Started):
        async def __call__(self, **kw):
            if kw["item_id"] == "bad":
                raise RuntimeError("no such workflow")
            return await super().__call__(**kw)

    spec = _spec()
    index = ScheduleIndex(spec)
    index.record("bad", PATH)
    index.record("good", PATH)
    started = _Boom()
    files = _Files(**{f"bad{PATH}": _file(DAILY), f"good{PATH}": _file(DAILY)})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert [r[0] for r in started.runs] == ["good"]


def test_a_failure_outside_the_start_still_leaves_the_other_items_alone():
    """The outer guard, which no test reached: every failure this file raised was
    inside `start`, and the INNER handler catches those. A mutation deleting the
    outer one changed nothing.

    Anything before the row loop — resolving the owner, listing the paths — is
    outside it, and one item failing there must still not cost the rest."""
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record("bad", PATH)
    index.record("good", PATH)
    started = _Started()
    files = _Files(**{f"bad{PATH}": _file(DAILY), f"good{PATH}": _file(DAILY)})

    def _owner(item_id: str) -> str:
        if item_id == "bad":
            raise RuntimeError("no owner on record")
        return "alice"

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=index,
        read=files.read,
        start=started,
        owner_of=_owner,
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )
    asyncio.run(sweeper.tick())

    assert [r[0] for r in started.runs] == ["good"]


def test_a_failing_start_does_not_stop_the_REST_OF_THE_SAME_FILE():
    """The inner guard's actual job. Every file in this suite had ONE row, so
    "the rest of the file" was empty and a mutation that aborted it passed.

    A page with two reports must not lose the second because the first names a
    workflow that no longer exists."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    files = _Files(
        **{
            f"{ITEM}{PATH}": _file(
                {"every": "daily", "at": "09:00", "run": "gone-workflow"},
                {"every": "daily", "at": "09:00", "run": "build-report"},
            )
        }
    )

    class _OneBad(_Started):
        async def __call__(self, **kw):
            if kw["workflow_id"] == "gone-workflow":
                raise RuntimeError("no such workflow")
            return await super().__call__(**kw)

    started = _OneBad()
    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert [r[1] for r in started.runs] == ["build-report"]


def test_nothing_indexed_reads_nothing():
    """The whole point of the index. An empty one must not walk items."""
    spec = _spec()
    started = _Started()

    read_calls: list[str] = []

    class _Counting(_Files):
        async def read(self, item_id: str, path: str) -> bytes:
            read_calls.append(path)
            return await super().read(item_id, path)

    asyncio.run(_sweeper(spec, _Counting(), started, datetime(2026, 9, 5, 9, 30)).tick())

    assert read_calls == []
