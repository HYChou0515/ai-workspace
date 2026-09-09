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
import inspect
import json
import logging
import time
from datetime import datetime, timedelta

import pytest
from specstar import SpecStar

from workspace_app.api.schedule_index import (
    SCHEDULES_FILE,
    ScheduleIndex,
    register_schedule_index,
)
from workspace_app.resources import make_spec
from workspace_app.workflow.orchestrator import ActiveRunExists
from workspace_app.workflow.triggers import register_trigger_store, window_key
from workspace_app.workflow.user_schedule_sweep import (
    MAX_START_ATTEMPTS,
    UserScheduleSweeper,
    _in_zone,
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
        #: The stable per-schedule key each fire was given. Recorded because the
        #: chat it selects is what `active_run_for_chat` collides on — a key that
        #: changes per fire silently switches the one-run rule off.
        self.keys: list[str] = []

    async def __call__(
        self, *, item_id: str, workflow_id: str, acting_user: str, payload: dict, key: str
    ):
        self.runs.append((item_id, workflow_id, acting_user, payload))
        self.keys.append(key)
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
        # WIRED, like production. Leaving it out meant nearly every test in this
        # file exercised a sweeper shaped differently from the one that ships —
        # and the confirmation path, where the interesting failures live, was
        # reached by exactly one test.
        read_live=files.read,
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


def test_every_fire_of_one_schedule_carries_the_same_key():
    """The key picks the conversation the run drives, and that conversation is
    what `active_run_for_chat` collides on. A key that changes per fire means a
    schedule can never collide with its own still-running previous fire, so
    `every: minutes` against a slow workflow piles runs up on one item with
    nothing to stop it — and leaves a permanent chat behind for each.

    It is the same key the window ledger uses, so "the same schedule" means the
    same thing to both.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())
    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 6, 9, 30)).tick())

    assert len(started.keys) == 2
    assert started.keys[0] == started.keys[1]
    assert started.keys[0].startswith("wui:")


def test_two_schedules_in_one_file_get_different_keys():
    """The control. One key per ITEM would pass the test above and make two
    unrelated reports share a thread — and collide with each other."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    rows = _file(DAILY, {"every": "daily", "at": "09:00", "run": "close-month"})
    files = _Files(**{f"{ITEM}{PATH}": rows})

    asyncio.run(_sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30)).tick())

    assert len(started.keys) == 2
    assert started.keys[0] != started.keys[1]


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


def test_a_slow_confirmation_does_not_hold_up_every_other_item():
    """The confirming read reaches the LIVE workspace, and on the hosted backend
    that can mean a full sandbox restore. `tick` is sequential over items, so one
    deleted schedule behind a cold workspace delays every other item's schedules
    on that pod — against a sweeper whose whole promise is "the interval is the
    latest a run will be".

    Bounded, so a slow answer costs one tick's worth of one path rather than the
    sweep. Timing out is not "confirmed gone": it is the third answer, and the
    path stays indexed.
    """

    async def _never_answers(item_id: str, path: str) -> bytes:
        await asyncio.sleep(30)
        raise AssertionError("should have been cut off long before this")

    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=index,
        read=_Files().read,
        read_live=_never_answers,
        start=_Started(),
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
        confirm_timeout_s=0.05,
    )

    started_at = time.monotonic()
    asyncio.run(sweeper.tick())
    took = time.monotonic() - started_at

    assert took < 5, f"the tick waited {took:.1f}s on one confirmation"
    assert index.items() == [ITEM], "a timeout was read as a deletion"


def test_the_cap_counts_schedules_not_complaints():
    """A file of N bad rows declares N schedules, not N times its complaints.

    `validate_user_schedules` emits several strings per bad row, so counting
    rows-plus-problems made the cap fire on files that never reached it — and the
    operator-facing message named the inflated number, so the one place they
    could check the claim disagreed with the file in front of them.

    THE NUMBERS ARE SIZED TO CROSS THE LINE, which they were not before: the
    fixture was 1 good plus 40 bad against a cap of 100, and the old expression
    computes 1 + 80 = 81 — under the cap, so restoring the bug left this test
    green. It was failing to catch the regression it is named after. (The old
    comment said "three problems each"; the measured figure is two. A derived
    number written from memory, in the test whose whole subject is a
    miscounted total.)

    91 declared rows against a cap of 100: honest count 91, passes; complaint
    count 1 + 180 = 181, refused.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    # Two problems each — measured, not recalled.
    bad = {"every": "monthly", "dom": 99, "at": "9am", "run": "x"}
    rows = [DAILY, *[bad] * 90]
    files = _Files(**{f"{ITEM}{PATH}": _file(*rows)})

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        read_live=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
        max_rows=100,
    )
    asyncio.run(sweeper.tick())

    assert [r[1] for r in started.runs] == ["build-report"], (
        "the whole file was refused against a cap it never reached"
    )


def test_a_file_over_the_cap_is_refused_before_it_is_parsed():
    """The guard bounds the work, so it has to come first.

    Parsing every row and THEN refusing means a runaway file costs its full
    parse — one `json.dumps` plus one `json.loads` plus validation per row —
    every tick, on every pod, for as long as it stays indexed. Measured in the
    hundreds of milliseconds of event-loop time for 20 000 rows, firing nothing
    — 150ms to 330ms across the machines this has been run on, with
    `uv run python -c` over `usable_rows` on a file of that size. The figure
    moves with the machine; the shape does not, and a single draw quoted as a
    constant is what the claim ledger keeps catching.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(*[DAILY] * 50)})

    parsed: list[int] = []
    import workspace_app.workflow.user_schedule_sweep as mod

    real = mod.usable_rows

    def _counting(raw: str):
        parsed.append(1)
        return real(raw)

    mod.usable_rows = _counting  # ty: ignore[invalid-assignment]
    try:
        sweeper = UserScheduleSweeper(
            spec=spec,
            index=ScheduleIndex(spec),
            read=files.read,
            read_live=files.read,
            start=started,
            owner_of=lambda _item: "alice",
            now=lambda: datetime(2026, 9, 5, 9, 30),
            max_rows=10,
        )
        asyncio.run(sweeper.tick())
    finally:
        mod.usable_rows = real

    assert started.runs == []
    assert parsed == [], "the file was fully parsed before the cap refused it"


def test_an_unset_workflow_ceiling_means_unrestricted():
    """`WorkflowsFor` documents "or None for 'unrestricted' … never 'refuse
    everything'". A resolver that ANSWERS `None` was being mapped to the empty
    set, which rejects every row — the opposite, silently, one warning per row
    per tick.

    The outer "is the resolver wired" check only covered an unwired resolver,
    not a wired one that answers None.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        read_live=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        workflows_for=lambda _item: None,
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )
    asyncio.run(sweeper.tick())

    assert [r[1] for r in started.runs] == ["build-report"]


def test_an_empty_workflow_ceiling_still_refuses_everything():
    """The control. `None` and `[]` are different answers: "this deploy does not
    restrict" and "this app offers nothing". Collapsing them either way loses a
    real distinction."""
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        read_live=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        workflows_for=lambda _item: [],
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )
    asyncio.run(sweeper.tick())

    assert started.runs == []


def test_the_sweep_never_holds_the_event_loop():
    """Every blocking call the sweep MAKES ITSELF must be off the loop — all of
    them, not most of them.

    Scope, stated because the first version of this docstring overstated it: the
    `start` callback is somebody else's code, injected here as an in-memory
    double, so this measures the sweep and not the fire path. What the fire path
    does with the loop is pinned separately, by
    `test_the_fire_path_does_not_hold_the_event_loop` — and it needed pinning:
    `_start_page_schedule` was making ten blocking specstar calls while this test
    read green and claimed "all of them".

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

    The slowed set is DERIVED from the module's own `asyncio.to_thread` call
    sites, not hand-written. It used to be a list naming `_index.items` and
    `_index.paths` — and when P39 replaced those with `items_with_paths`, the
    list kept slowing two functions the tick no longer calls. Un-offloading the
    new one left this test green: the guard was measuring a code path that no
    longer existed, while claiming "every store call is slowed". A list written
    against something defined in another file goes stale silently, and the only
    fix that stays fixed is to stop writing the list.
    """
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})
    started = _Started()
    sweeper = _sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30))

    # 200ms, not 50. The measurement floor is scheduler jitter, and under the
    # oversubscription CI actually runs at (`-n auto` on a busy runner) that
    # jitter reaches ~26ms — which cleared a 25ms threshold and made this test
    # flake, exactly as a load measurement predicted before it did. Widening the
    # block widens the signal, not the tolerance: a held loop still shows as a
    # gap of a full block, and the threshold stays half of it.
    BLOCK = 0.2  # a round trip, as it is on a real backend

    def _slow(fn):
        def go(*a, **kw):
            time.sleep(BLOCK)
            return fn(*a, **kw)

        return go

    # DERIVED FROM THE COLLABORATORS, not from the sweep's call sites.
    #
    # The list used to be hand-written — `_index.items`, `_index.paths`,
    # `_store.last_window`, `_store.try_claim`, `_owner_of` — and when P39
    # replaced the first two with `items_with_paths`, the guard went on slowing
    # two functions the tick no longer calls. Un-offloading the new one left it
    # green: a list written against something defined in another file goes stale
    # in silence, which is this file's recurring failure.
    #
    # Deriving it from the sweep's own `asyncio.to_thread(...)` call sites is
    # WORSE, not better, and it took a probe to see why: removing an offload
    # removes the call site, so the derivation stops slowing exactly the call the
    # mutation just broke, and the guard adapts to the regression instead of
    # catching it. A derived guard must not derive from the thing it guards
    # against.
    #
    # So: slow every public method of every store-like collaborator the sweeper
    # holds. That surface is decided by the collaborator, not by this sweep, so
    # neither renaming a call here nor deleting an offload can shrink it.
    slowed: list[str] = []
    for holder in (sweeper._store, sweeper._index):
        for attr in dir(holder):
            if attr.startswith("_"):
                continue
            current = getattr(holder, attr)
            if not callable(current) or inspect.iscoroutinefunction(current):
                continue
            setattr(holder, attr, _slow(current))
            slowed.append(f"{type(holder).__name__}.{attr}")
    for attr in ("_owner_of", "_workflows_for"):
        current = getattr(sweeper, attr, None)
        # `workflows_for` is optional and this fixture leaves it unwired; wrapping
        # `None` would turn "not configured" into "configured", which is a
        # different behaviour from the one under test.
        if callable(current):
            setattr(sweeper, attr, _slow(current))
            slowed.append(attr)

    assert len(slowed) >= 5, f"only {slowed} were slowed — the derivation stopped finding them"

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


def test_a_file_the_snapshot_has_not_caught_up_with_is_not_gone():
    """The durable snapshot is BEHIND the workspace, and "behind" looks exactly
    like "deleted" from where the sweep stands.

    A page's save lands in the warm sandbox; the snapshot catches up on the next
    mirror. Reading the snapshot is what keeps the sweep from waking reaped
    sandboxes — but it means a tick inside that window sees `FileNotFound` for a
    file that is right there, and the previous answer to `FileNotFound` was to
    UNREGISTER the schedule. Which nothing undoes but another save.

    Two fixes, each correct alone, that broke each other: narrowing the catch to
    `FileNotFound` (so a blip stops being read as a deletion) and switching to the
    snapshot (so the sweep stops resurrecting sandboxes) made `FileNotFound` a
    NORMAL transient state for the first time.

    So a deletion is now CONFIRMED against the live workspace before it counts.
    Only on the missing path, so the ordinary tick still wakes nothing.
    """
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)
    started = _Started()

    snapshot = _Files()  # the mirror has not run yet
    live = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})  # the page saved a moment ago

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=index,
        read=snapshot.read,
        read_live=live.read,
        start=started,
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )
    asyncio.run(sweeper.tick())

    assert index.items() == [ITEM], "a schedule was unregistered for lagging the mirror"
    assert [r[1] for r in started.runs] == ["build-report"], "and it did not fire either"


def test_the_same_complaint_is_not_repeated_every_tick(caplog):
    """A file with a typo is read every tick, so a complaint logged per tick is
    the same three lines a minute, for ever, per pod.

    This module already argues the point about its own retry cap — "the log says
    so once instead of a thousand times" — because a channel trained to be noise
    is one where the line that mattered is not read either. The same reasoning
    applies to the thing being complained about.

    It repeats when the complaint CHANGES, because that is new information.
    """
    import logging

    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    started = _Started()
    bad = {"every": "fortnightly", "run": "x"}
    files = _Files(**{f"{ITEM}{PATH}": _file(bad, DAILY)})
    sweeper = _sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30))

    with caplog.at_level(logging.WARNING, logger="workspace_app.workflow.user_schedule_sweep"):
        for _ in range(4):
            asyncio.run(sweeper.tick())
        first = [r for r in caplog.records if "fortnightly" in r.getMessage()]

        # The file is edited and is still wrong in a NEW way.
        files.files[f"{ITEM}{PATH}"] = _file({"every": "minutes", "n": 7, "run": "x"}, DAILY)
        asyncio.run(sweeper.tick())
        second = [r for r in caplog.records if "divide 60" in r.getMessage()]

    assert len(first) == 1, f"the same complaint was logged {len(first)} times"
    assert len(second) == 1, "a NEW complaint was suppressed along with the old one"


def test_a_tick_reads_the_index_once_not_once_per_item():
    """The listing already fetches each row's data — it has to, to tell an
    emptied row from a live one — so asking each item for its paths afterwards
    re-reads what is in hand. One round trip per item, per tick, per pod, for a
    number that only grows.

    It also removed a failure mode rather than guarding it: reading paths per
    item happened OUTSIDE the per-item try, so one specstar error skipped every
    item after it in the same tick, alphabetically, with the lifespan loop
    swallowing the error so nothing looked wrong.
    """
    spec = _spec()
    index = ScheduleIndex(spec)
    for n in range(5):
        index.record(f"item-{n}", PATH)
    started = _Started()
    files = _Files(**{f"item-{n}{PATH}": _file(DAILY) for n in range(5)})

    sweeper = _sweeper(spec, files, started, datetime(2026, 9, 5, 9, 30))
    per_item: list[str] = []
    sweeper._index.paths = lambda item_id: per_item.append(item_id) or []

    asyncio.run(sweeper.tick())

    assert len(started.runs) == 5, "the sweep did not do its work — this measures nothing"
    assert per_item == [], f"the index was read again per item: {per_item}"


def test_a_confirmation_that_could_not_be_made_is_not_a_deletion():
    """ "I could not ask" is a third answer, and it must not be filed as "gone".

    The snapshot says missing (mirror lag — the state this confirmation exists
    for) and the LIVE read then fails for a reason that is not absence:
    `SandboxBusy`, which the facade propagates on purpose, or a 502 from the
    sandbox host. Collapsing that onto "gone" unregisters the schedule
    permanently, and only a WRITE of `schedules.json` puts it back.

    That is the same failure the sibling branch below already refuses to make —
    reintroduced through the confirmation path added to prevent it. The
    justification given at the time ("the caller was already about to drop this
    path") is the trap: the caller was about to drop it ONLY because the
    snapshot said missing, which is exactly the claim being checked.
    """

    class _Busy(_Files):
        async def read(self, item_id: str, path: str) -> bytes:
            raise RuntimeError("sandbox busy")

    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=index,
        read=_Files().read,  # snapshot: not there yet
        read_live=_Busy().read,  # live: cannot answer
        start=_Started(),
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )
    asyncio.run(sweeper.tick())

    assert index.items() == [ITEM], "a schedule was unregistered because a read failed"


def test_a_file_gone_from_both_is_really_gone():
    """The control. Never forgetting would leave every deleted page in the sweep's
    input for the life of the deployment — the one cost this index exists to
    avoid — so the confirmation has to be able to say yes."""
    spec = _spec()
    index = ScheduleIndex(spec)
    index.record(ITEM, PATH)

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=index,
        read=_Files().read,
        read_live=_Files().read,
        start=_Started(),
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )
    asyncio.run(sweeper.tick())

    assert index.items() == []


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


# --- the DST limitation, recorded so it cannot be discovered by accident ------


@pytest.mark.parametrize(
    ("every", "per_hour"),
    [("hourly", 1), ("minutes:30", 2), ("minutes:15", 4), ("minutes:5", 12)],
)
def test_a_sub_daily_schedule_loses_one_hour_of_runs_at_the_autumn_switch(
    every: str, per_hour: int
) -> None:
    """KNOWN LIMITATION, measured — not a bug report, a recorded shape.

    Windows are keyed on the LOCAL wall clock, and at a fall-back the local
    clock repeats an hour. `Europe/Berlin` 2025-10-26 is local 02:00 twice, so
    the two real hours from UTC 00:00 to 01:59 produce ONE bucket. A sub-daily
    schedule therefore fires half as often across that stretch — exactly one
    hour of runs, once a year, in a DST-observing zone.

    Not fixed, deliberately. Distinguishing the two 02:00s needs the offset in
    the key, which needs `_in_zone` to hand back an AWARE datetime, which
    `period_target` then cannot compare against the naive ones it builds — a
    change to the window mechanism shared with the engineer-authored triggers
    and #435's notification fingerprint, to buy back one run a year. The zone
    that avoids it costs nothing: UTC, which is the default.

    Spring forward needs no note: local 02:00 simply never happens, so nothing
    is skipped that the local clock ever showed. The asymmetry is the point —
    only the repeat collapses.
    """
    base = datetime(2025, 10, 26, 0, 0)  # UTC, the two hours local 02 covers
    keys = {
        window_key(every, _in_zone(base + timedelta(minutes=m), "Europe/Berlin"))
        for m in range(120)
    }
    assert len(keys) == per_hour, f"{every} produced {len(keys)} buckets over two real hours"

    # The control: in a zone that does not switch, the same stretch buckets fully.
    steady = {
        window_key(every, _in_zone(base + timedelta(minutes=m), "Asia/Taipei")) for m in range(120)
    }
    assert len(steady) == per_hour * 2, "the loss must be the SWITCH, not the arithmetic"


def test_a_row_naming_a_workflow_the_app_does_not_offer_complains_once(caplog):
    """The third repeating complaint — and the only one that multiplies by ROWS.

    P39 memoised two of the three lines this module repeats and left this one a
    bare `logger.warning` inside the per-row loop. It is the worst of the three
    to leave behind: the other two are one line per file per tick, this one is a
    line per BAD ROW per tick, on every pod, forever — until somebody edits a
    page they have no reason to think is broken, because nothing tells them.

    A lesson applied to two of three places is worse than one not applied at
    all: the memo makes the log look tamed while the line that actually floods
    it keeps firing.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    files = _Files(
        **{
            f"{ITEM}{PATH}": _file(
                {"every": "daily", "at": "09:00", "run": "nope"},
                {"every": "daily", "at": "10:00", "run": "also-nope"},
            )
        }
    )
    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        read_live=files.read,
        start=_Started(),
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 11, 0),
        workflows_for=lambda _item: ("build-report",),
    )

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            asyncio.run(sweeper.tick())

    said = [r for r in caplog.records if "does not offer" in r.getMessage()]
    assert len(said) == 2, (
        f"two bad rows over three ticks produced {len(said)} lines — the complaint "
        "repeats every tick, per row, for as long as the page stays as it is"
    )


def test_one_page_of_an_item_failing_does_not_cost_the_other_pages(caplog):
    """A page is a FOLDER, so per-page resilience is per-PATH.

    P39 moved the `try` outward so it covered reading the paths, and widened it
    from one path to the whole item in the same edit. `paths` is sorted, so the
    alphabetically-first page loses every time — which is exactly the argument
    P39's own comment makes about items, reintroduced one level down. This
    module's header promises "one page's mistake costs that page only".

    Broken at the WINDOW LEDGER, not at the read: a read error is handled inside
    `_one_file` (that is what the confirmation path is for), so a double that
    breaks the read never reaches the handler under test and the test passes
    while the defect is intact. `last_window` is the first call that genuinely
    escapes.
    """
    spec = _spec()
    index = ScheduleIndex(spec)
    for page in ("/a", "/b"):
        index.record(ITEM, f"{page}/{SCHEDULES_FILE}")
    files = _Files(
        **{f"{ITEM}/a/{SCHEDULES_FILE}": _file(DAILY), f"{ITEM}/b/{SCHEDULES_FILE}": _file(DAILY)}
    )

    started = _Started()
    sweeper = UserScheduleSweeper(
        spec=spec,
        index=index,
        read=files.read,
        read_live=files.read,
        start=started,
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )

    real_last_window = sweeper._store.last_window
    calls = {"n": 0}

    def _breaks_once(trigger_id: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:  # the first page, in sorted order
            raise RuntimeError("a transient store error on the first page")
        return real_last_window(trigger_id)

    # Patched on the instance: the property under test is what `tick` does when a
    # store call escapes `_one_file`, and `last_window` is the first one that can.
    sweeper._store.last_window = _breaks_once  # ty: ignore[invalid-assignment]

    with caplog.at_level(logging.ERROR):
        fired = asyncio.run(sweeper.tick())

    assert fired == 1, (
        f"{fired} schedules fired — page /b was skipped because page /a failed, so "
        "the alphabetically-first page costs every page after it, every tick"
    )


def test_a_page_fixed_and_broken_again_is_reported_again(caplog):
    """The memo has to be FORGOTTEN when the file becomes clean.

    Otherwise "we already said that" outlives the thing it was said about: the
    author fixes the page, breaks it again a week later in the same way, and the
    sweep stays silent because a dict in a pod's memory still holds the sentence
    from last week. That is worse than never having de-noised, because the log
    now looks healthy.

    Nothing held this: deleting the `pop` left 43 tests green. Its two siblings —
    always-log, and remembering the key instead of the text — each bite alone;
    this is the half that decides whether a problem RECURRING is ever reported.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    files = _Files(**{f"{ITEM}{PATH}": _file({"every": "daily", "at": "9am", "run": "r"})})
    sweeper = _sweeper(spec, files, _Started(), datetime(2026, 9, 5, 11, 0))

    def _lines() -> int:
        return len([r for r in caplog.records if "must look like HH:MM" in r.getMessage()])

    with caplog.at_level(logging.WARNING):
        asyncio.run(sweeper.tick())
        asyncio.run(sweeper.tick())
        said_once = _lines()

        # fixed
        files.files[f"{ITEM}{PATH}"] = _file(DAILY)
        asyncio.run(sweeper.tick())

        # and broken again, the same way
        files.files[f"{ITEM}{PATH}"] = _file({"every": "daily", "at": "9am", "run": "r"})
        asyncio.run(sweeper.tick())

    assert said_once == 1, f"the first complaint was logged {said_once} times"
    assert _lines() == 2, (
        "the page broke again and the sweep stayed silent — the memo outlived the "
        "problem it was about"
    )


def test_a_schedule_whose_previous_run_is_still_going_skips_the_window_quietly(caplog):
    """An overrun is NORMAL, not a failure.

    Since a schedule's chat became stable, `active_run_for_chat` collides with
    the schedule's own still-running previous fire — which is the point: the
    one-run rule finally applies to the entrance that repeats. But the sweep
    treated the collision as a failed start: `logger.exception` with a
    traceback, the window handed back, and after three tries the window burned
    with "Nothing will run for it."

    `every: minutes, n: 1` against a two-minute workflow makes that a normal
    Tuesday — thousands of ERROR lines a day for a condition the design intends,
    in a module that spent this round arguing that a log trained to be noise is
    one where the line that mattered is not read either. Burning the window is
    wrong twice over: nothing failed, and the next window is the right place to
    try again.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    async def _busy(**kw):
        raise ActiveRunExists(ITEM, "run-still-going")

    def _tick(day: int) -> None:
        asyncio.run(
            UserScheduleSweeper(
                spec=spec,
                index=ScheduleIndex(spec),
                read=files.read,
                read_live=files.read,
                start=_busy,
                owner_of=lambda _item: "alice",
                now=lambda: datetime(2026, 9, day, 9, 30),
            ).tick()
        )

    with caplog.at_level(logging.INFO):
        for day in range(5, 10):
            _tick(day)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, (
        f"{len(errors)} ERROR lines for a schedule whose previous run is still "
        "going — a condition the design intends"
    )
    burned = [r for r in caplog.records if "Nothing will run for it" in r.getMessage()]
    assert not burned, "the window was burned because the last run had not finished"


def test_an_overrunning_schedule_says_it_once_and_remembers_boundedly(caplog):
    """ "Said once per (schedule, window)" bounds nothing and de-noises nothing.

    A minutely schedule whose run is slow produces a NEW window every minute, so
    keying the memo on the window means one line per window — 1440 a day, which
    is the flood the memo was added to stop — and one dict entry per window,
    never popped, for the life of the process.

    `_failures`, ten lines below, prunes the trigger's older windows with the
    comment "so this cannot grow with time". The window-keyed memo added beside
    it got no such treatment.

    The right key is the SCHEDULE: a run that spans forty windows is one fact,
    said once, and said again only when it changes.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    files = _Files(**{f"{ITEM}{PATH}": _file(DAILY)})

    async def _busy(**kw):
        raise ActiveRunExists(ITEM, "run-still-going")

    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        read_live=files.read,
        start=_busy,
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 9, 30),
    )

    with caplog.at_level(logging.INFO):
        for day in range(5, 45):
            sweeper._now = lambda d=day: datetime(2026, 9, d, 9, 30)
            asyncio.run(sweeper.tick())

    said = [r for r in caplog.records if "still running its previous fire" in r.getMessage()]
    assert len(said) == 1, (
        f"forty windows of one slow run narrated themselves {len(said)} times — "
        "the memo is keyed on the window, so it never suppresses anything"
    )
    assert len(sweeper._said) <= 2, (
        f"the memo holds {len(sweeper._said)} entries after forty windows and "
        "nothing ever pops them"
    )


@pytest.mark.parametrize(
    ("zone", "raises"),
    [
        ("Nowhere/Atlantis", "ZoneInfoNotFoundError"),
        ("/absolute", "ValueError"),
        ("../traversal", "ValueError"),
        ("x" * 5000, "OSError"),
    ],
)
def test_an_unusable_zone_falls_back_to_utc_instead_of_raising(zone: str, raises: str, caplog):
    """The FALLBACK, reached directly, because nothing reaches it through `tick`.

    This module says of the pair: "Both, deliberately: the lint is what TELLS
    the author, and this is what keeps a miss from being fatal. Neither alone is
    enough." Only the lint was held. `validate_user_schedules` now rejects a bad
    zone with the same `_valid_tz` predicate, so `usable_rows` drops the row
    before `_in_zone` can see it — which means the test named after this catch
    was exercising the LINT, and removing the widened `except` left 125 tests
    green while the branch also stopped being covered at all.

    THE FULL SET, not just "not found": `ZoneInfo` raises `ValueError` for an
    absolute path or a traversal and `OSError` for a key long enough to reach
    the filesystem. Catching only `ZoneInfoNotFoundError` is what made one bad
    row take a whole file down.

    An hour out is the intended trade against a page stopping, so the fallback
    returns UTC and says so once.
    """
    del raises  # named in the ids, so a failure says which family broke
    now = datetime(2026, 9, 5, 9, 30)

    with caplog.at_level(logging.WARNING):
        assert _in_zone(now, zone) == now, "the fallback did not return the UTC clock"

    assert any("unusable time zone" in r.getMessage() for r in caplog.records), (
        "the zone was silently ignored — the operator has nothing to look at"
    )


def test_a_row_fixed_and_broken_again_complains_again(caplog):
    """The forget rule has to cover EVERY memo about the file, not just the one
    it was written for.

    P47 pinned "a page fixed and broken again is reported again", and the pop it
    added clears only the bare `(item_id, path)` key. P46 had added a second
    memo about the same file, keyed `(item_id, f"{path}#{row.run}")`, and
    nothing pops that one — so an author who fixes a bad `run:` and breaks it
    the same way a week later gets silence, from a dict in a pod's memory, while
    the log looks healthy.

    That is exactly the failure P47's own docstring describes, sitting in a memo
    the same round added. A rule applied to one key and not the others is the
    shape this branch keeps producing.
    """
    spec = _spec()
    ScheduleIndex(spec).record(ITEM, PATH)
    files = _Files(**{f"{ITEM}{PATH}": _file({"every": "daily", "at": "09:00", "run": "nope"})})
    sweeper = UserScheduleSweeper(
        spec=spec,
        index=ScheduleIndex(spec),
        read=files.read,
        read_live=files.read,
        start=_Started(),
        owner_of=lambda _item: "alice",
        now=lambda: datetime(2026, 9, 5, 11, 0),
        workflows_for=lambda _item: ("build-report",),
    )

    def _lines() -> int:
        return len([r for r in caplog.records if "does not offer" in r.getMessage()])

    with caplog.at_level(logging.WARNING):
        asyncio.run(sweeper.tick())
        first = _lines()

        files.files[f"{ITEM}{PATH}"] = _file(DAILY)  # fixed
        asyncio.run(sweeper.tick())

        files.files[f"{ITEM}{PATH}"] = _file(  # broken again, the same way
            {"every": "daily", "at": "09:00", "run": "nope"}
        )
        asyncio.run(sweeper.tick())

    assert first == 1, f"the first complaint was logged {first} times"
    assert _lines() == 2, (
        "the row broke again the same way and the sweep stayed silent — the memo "
        "outlived the problem it was about"
    )
