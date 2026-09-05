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
import time
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
from workspace_app.workflow.user_schedule_sweep import (
    MAX_START_ATTEMPTS,
    UserScheduleSweeper,
)

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


def test_one_bad_zone_does_not_take_the_whole_file_down():
    """`ZoneInfo` raises `ValueError` — not `ZoneInfoNotFoundError` — for an
    absolute path or a traversal, and the sweep caught only the latter. So a
    single typo'd zone raised out of the row loop, `_one_file` never returned,
    and every OTHER schedule in that file silently stopped firing.

    That is the module's stated property ("one page's mistake costs that page
    only") failing on the row that was supposed to be linted rather than fatal.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(
        **{
            f"{ITEM}{PATH}": _file(
                {"every": "daily", "at": "09:00", "run": "broken", "tz": "/absolute"},
                {"every": "daily", "at": "09:00", "run": "build-report"},
            )
        }
    )

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert [r[1] for r in started.runs] == ["build-report"]


def test_a_schedule_fires_in_the_zone_it_named():
    """`tz` was accepted, copied onto the Schedule and hashed into the lease key
    — and then never used to decide anything. The sweep asked the server what
    time it was.

    So a page saying "09:00, Asia/Taipei" on a UTC pod got 09:00 UTC, which is
    17:00 in Taipei. Nothing warned, and the schedule DID fire — at the wrong
    time, every day, which is the version of this bug that survives longest
    because the report keeps arriving.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    row = {"every": "daily", "at": "09:00", "run": "build-report", "tz": "Asia/Taipei"}
    files = _Files(**{f"{ITEM}{PATH}": _file(row)})

    # 01:30 UTC is 09:30 in Taipei: past this schedule's moment.
    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 1, 30)).tick())

    assert [r[1] for r in started.runs] == ["build-report"]


def test_a_schedule_in_a_zone_does_not_fire_before_its_moment_there():
    """The control for the test above. Reading `tz` as "always fire" would pass
    that one and break every schedule that has a zone."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    row = {"every": "daily", "at": "09:00", "run": "build-report", "tz": "Asia/Taipei"}
    files = _Files(**{f"{ITEM}{PATH}": _file(row)})

    # 23:00 UTC is 07:00 the next day in Taipei — before nine.
    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 23, 0)).tick())

    assert started.runs == []


def test_the_sweep_never_holds_the_event_loop():
    """Every blocking call the sweep makes must be off the loop — all of them,
    not most of them.

    Not a style point. This runs on every API pod, un-gated by `run_consumers`,
    at O(items × paths × rows) per tick, so a loop it holds is holding every
    request that pod is serving. That was the incident PR#657 fixed.

    Measured as the LONGEST GAP between heartbeats, not as a count of them.
    A count is the wrong instrument: with six of seven calls offloaded the
    heartbeat still ticks a few times, so `beats > 0` passed while one call was
    still blocking — the test would have reported the property as held while it
    was broken. The gap is the property itself: if any call runs on the loop,
    nothing else runs for as long as that call takes, and that shows up here
    whichever call it is.

    Every store call is slowed, so the test does not depend on knowing which one
    somebody forgets next.
    """
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})
    started = _Started()
    sweeper = _sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30))

    BLOCK = 0.05  # a round trip, as it is on a real backend

    def _slow(fn):
        def go(*a, **kw):
            time.sleep(BLOCK)
            return fn(*a, **kw)

        return go

    sweeper._store.last_window = _slow(sweeper._store.last_window)  # type: ignore[method-assign]
    sweeper._store.try_claim = _slow(sweeper._store.try_claim)  # type: ignore[method-assign]
    sweeper._index.items = _slow(sweeper._index.items)  # type: ignore[method-assign]
    sweeper._index.paths = _slow(sweeper._index.paths)  # type: ignore[method-assign]
    sweeper._owner_of = _slow(sweeper._owner_of)

    async def _race() -> float:
        beats: list[float] = [time.monotonic()]
        running = True

        async def _heartbeat() -> None:
            while running:
                await asyncio.sleep(0.001)
                beats.append(time.monotonic())

        pulse = asyncio.create_task(_heartbeat())
        await sweeper.tick()
        running = False
        pulse.cancel()
        beats.append(time.monotonic())
        return max(b - a for a, b in zip(beats, beats[1:], strict=False))

    worst = asyncio.run(_race())

    assert started.runs, "the sweep did not fire — this is measuring nothing"
    assert worst < BLOCK / 2, (
        f"the loop was held for {worst * 1000:.0f}ms at once, and one blocking call "
        f"takes {BLOCK * 1000:.0f}ms — so at least one is still running on it"
    )


def test_a_blip_reading_the_file_is_not_a_deletion():
    """ "Gone" and "could not read it just now" are different answers, and only
    one of them may unregister a schedule.

    `files.read` raises for reasons that are not deletion: `SandboxBusy`, which
    the facade documents as deliberately propagating; any 502 or timeout from the
    sandbox host; a sandbox mid-restore. Treating those as "gone" calls `forget`,
    and `forget` is destructive — the last path takes the whole index row with
    it, and nothing re-creates it but a WRITE of `schedules.json`. So a five
    second blip stops a daily report forever, and the only trace is one log line
    that says the file is gone.
    """

    class _Busy(_Files):
        async def read(self, item_id: str, path: str) -> bytes:
            raise RuntimeError("sandbox busy")

    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)

    asyncio.run(_sweeper(spec, _Busy(), _Started(), datetime(2026, 9, 5, 9, 30)).tick())

    assert index.items() == [ITEM], "a transient read error unregistered the schedule"


def test_a_row_naming_a_workflow_this_app_does_not_offer_is_named_and_skipped():
    """`run` has a ceiling — the workflows this app offers — and the interactive
    entrance refuses an unknown one with a sentence saying which. The scheduled
    entrance checked nothing, so a typo reached `orchestrator.start`, failed an
    assertion deep inside, and arrived as a generic "could not start" in a log
    the page's author never sees.

    Per ROW, like every other lint here: one mistyped id must not stop the other
    schedules in the same file.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(
        **{f"{ITEM}{PATH}": _file({**DAILY, "run": "typo-report"}, {**NOON, "at": "09:00"})}
    )
    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
        workflows_for=lambda _item: ["build-report"],
    )

    asyncio.run(sweeper.tick())

    assert [r[1] for r in started.runs] == ["build-report"], (
        "the unknown workflow was started, or it took the good row down with it"
    )


def test_without_a_ceiling_every_row_still_runs():
    """The control. A deploy that wires no resolver must behave as it does now,
    not refuse everything — the same "unset means unrestricted" rule the tool
    ceiling keeps."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert [r[1] for r in started.runs] == ["build-report"]


def test_a_window_whose_run_never_started_is_tried_again():
    """The claim is taken BEFORE the run is asked for, because that is what makes
    two pods produce one run. So when the start then fails, the window has been
    consumed by a run that does not exist — and the ledger says it fired.

    Nothing retries it. The catch-up rule, which is the property this design is
    sold on, covers a sweeper that was DOWN at nine; it cannot see a window that
    was claimed and then dropped. So a daily report silently misses a day for
    any transient reason — an item briefly without an owner, one long run
    holding the item, a DB blip — and the next sweep is a minute later.
    """

    class _BoomOnce(_Started):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def __call__(self, **kw):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("the item had no owner just then")
            return await super().__call__(**kw)

    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _BoomOnce()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())
    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 31)).tick())

    assert [r[1] for r in started.runs] == ["build-report"], "the window was burned"


def test_a_schedule_that_never_starts_stops_being_retried():
    """The other half of handing the window back. A start that fails for a
    PERMANENT reason — an item with no owner, a workflow somebody deleted —
    would otherwise be retried once a minute for as long as the period lasts.

    So the release is capped, and the last log line says so once rather than a
    thousand times. Trying forever is how a channel becomes noise, and a channel
    that is noise is one where the message that mattered is not read either.
    """

    class _AlwaysBoom(_Started):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def __call__(self, **kw):
            self.attempts += 1
            raise RuntimeError("this item has no owner")

    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _AlwaysBoom()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})
    sweeper = _sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30))

    for _ in range(10):
        asyncio.run(sweeper.tick())

    assert started.attempts == MAX_START_ATTEMPTS


def test_yesterdays_failures_do_not_spend_todays_attempts():
    """The cap is "how many tries THIS window gets", not "how many times this
    schedule may ever fail".

    Counted per trigger and never cleared except by a success, a report that
    failed three times in January would be abandoned on its FIRST stumble in
    February — and every month after — with the log line saying it had failed
    three times running. The blip absorption the cap exists for would be gone
    for good, silently, on exactly the schedules that had already had a bad day.
    """

    class _BoomThenFine(_Started):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0
            self.fail = True

        async def __call__(self, **kw):
            self.attempts += 1
            if self.fail:
                raise RuntimeError("a bad day")
            return await super().__call__(**kw)

    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _BoomThenFine()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    # Day one: it fails until the cap and the window is given up on.
    day_one = _sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30))
    for _ in range(6):
        asyncio.run(day_one.tick())
    assert started.attempts == MAX_START_ATTEMPTS

    # Day two, same pod. It stumbles ONCE and then the cause clears — which is
    # the whole scenario the cap is written for.
    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 6, 9, 30),
    )
    sweeper._failures = day_one._failures  # the same pod remembers yesterday
    asyncio.run(sweeper.tick())  # one stumble
    started.fail = False
    asyncio.run(sweeper.tick())  # the cause is gone — this must still get a turn

    assert [r[1] for r in started.runs] == ["build-report"], (
        "yesterday's failures spent today's attempts, so one stumble burned the window"
    )


def test_a_window_that_did_start_is_not_fired_a_second_time():
    """The control. Releasing the claim unconditionally would pass the test
    above and re-run every schedule on every sweep — sixty reports an hour."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    for minute in (30, 31, 32):
        asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, minute)).tick())

    assert len(started.runs) == 1


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


# ── the runaway guard ────────────────────────────────────────────────────────


def test_a_page_past_the_cap_fires_nothing_and_says_so(caplog):
    """A guard against a page with a bug, not a policy limit on what people may
    schedule. The number is deliberately far above any real use, so hitting it
    means something is wrong — and the right answer to "something is wrong" is
    to be loud, not to quietly do the first N.

    Whole-file refusal on purpose here, unlike an invalid ROW: a file with a
    thousand entries was not typed by a person, so there is no good half to
    preserve, and half-processing would leave a ledger row for every one it got
    through.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    many = _file(*[{**DAILY, "with": {"n": i}} for i in range(5)])
    files = _Files(**{f"{ITEM}{PATH}": many})

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        start=started,
        owner_of=lambda _i: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
        max_rows=3,
    )
    with caplog.at_level("ERROR"):
        asyncio.run(sweeper.tick())

    assert started.runs == []
    assert "5" in caplog.text and "3" in caplog.text


def test_a_page_at_the_cap_still_works():
    """Off-by-one on a guard is how a legitimate page gets refused. The cap is
    the most it may have, not the first number that is too many."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(*[{**DAILY, "with": {"n": i}} for i in range(3)])})

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        start=started,
        owner_of=lambda _i: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
        max_rows=3,
    )
    asyncio.run(sweeper.tick())

    assert len(started.runs) == 3
