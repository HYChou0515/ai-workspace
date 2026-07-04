"""Declarative trigger definitions (#429 P6) — the profile-level ``triggers.json``
schema, parse, and static validation. Runtime (sweeper / lease / event dispatch) is
P7–P9; this is the declarative layer only."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from specstar import SpecStar

from workspace_app.workflow.triggers import (
    ITriggerStore,
    Schedule,
    ScheduleTrigger,
    SpecstarTriggerStore,
    TriggerError,
    TriggerSweeper,
    fire_window,
    is_due,
    parse_triggers,
    register_trigger_store,
    validate_triggers,
)


class FakeStore(ITriggerStore):
    """An in-memory trigger store: a (trigger_id, window) claim is CAS — the first caller
    wins, and the claimed window becomes ``last_window``. Also tracks the window's in-flight
    run + resume attempts for orphan pickup."""

    def __init__(self) -> None:
        self._claimed: set[tuple[str, str]] = set()
        self._last: dict[str, str] = {}
        self._run: dict[str, tuple[str, int]] = {}

    def last_window(self, trigger_id: str) -> str:
        return self._last.get(trigger_id, "")

    def try_claim(self, trigger_id: str, fire_window: str) -> bool:
        if (trigger_id, fire_window) in self._claimed:
            return False
        self._claimed.add((trigger_id, fire_window))
        self._last[trigger_id] = fire_window
        self._run.pop(trigger_id, None)  # a fresh window resets the run slot
        return True

    def record_run(self, trigger_id: str, run_id: str) -> None:
        self._run[trigger_id] = (run_id, 1)

    def note_resume(self, trigger_id: str) -> None:
        run_id, attempts = self._run.get(trigger_id, ("", 0))
        self._run[trigger_id] = (run_id, attempts + 1)

    def clear_run(self, trigger_id: str) -> None:
        _run_id, attempts = self._run.get(trigger_id, ("", 0))
        self._run[trigger_id] = ("", attempts)

    def get_run(self, trigger_id: str) -> tuple[str, int]:
        return self._run.get(trigger_id, ("", 0))


def _fixed_now(dt: datetime):
    from datetime import UTC

    aware = dt.replace(tzinfo=UTC)
    return lambda: aware


def _triggers(items: list[dict[str, Any]]) -> list[str]:
    return validate_triggers(parse_triggers(json.dumps({"triggers": items})))


def _sched(**over: Any) -> dict[str, Any]:
    base = {
        "type": "schedule",
        "id": "t",
        "workflow_id": "w",
        "acting_user": "bot",
        "item_id": "item-1",
        "schedule": {"every": "weekly", "dow": "mon", "at": "09:00"},
    }
    base.update(over)
    return base


def test_valid_weekly_schedule_trigger_validates_clean():
    """A well-formed weekly schedule trigger (every+dow+at+acting_user) is accepted."""
    assert _triggers([_sched()]) == []


def test_acting_user_is_required():
    """E-decl: acting_user is the run's authz scope — a headless run must not fall back to
    a system identity, so an empty acting_user is a static error (#429 E)."""
    errs = _triggers([_sched(acting_user="")])
    assert any("acting_user" in e and "required" in e for e in errs)


def test_every_dow_dom_combinations_are_static_errors():
    """#429 C2 edge 2: the every↔dow/dom combination is fixed and checked statically."""
    daily_with_dow = _triggers([_sched(schedule={"every": "daily", "at": "03:00", "dow": "mon"})])
    assert any("daily schedule takes only 'at'" in e for e in daily_with_dow)

    weekly_no_dow = _triggers([_sched(schedule={"every": "weekly", "at": "09:00"})])
    assert any("weekly schedule needs 'dow'" in e for e in weekly_no_dow)

    monthly_with_dow = _triggers(
        [_sched(schedule={"every": "monthly", "at": "00:00", "dow": "mon"})]
    )
    assert any("monthly schedule takes 'dom'" in e for e in monthly_with_dow)

    bad_every = _triggers([_sched(schedule={"every": "hourly", "at": "00:00"})])
    assert any("'every' must be one of" in e for e in bad_every)

    bad_at = _triggers([_sched(schedule={"every": "daily", "at": "25:00"})])
    assert any("'at' must be 'HH:MM'" in e for e in bad_at)


def test_monthly_dom_range_and_clamp_hint():
    """#429 C2 edge 1: a `dom` past the shortest month clamps to month-end (a hint); an
    out-of-range dom is an error."""
    hint = _triggers([_sched(schedule={"every": "monthly", "at": "00:00", "dom": 31})])
    assert any(e.startswith("hint:") and "month-end" in e for e in hint)

    bad = _triggers([_sched(schedule={"every": "monthly", "at": "00:00", "dom": 40})])
    assert any("'dom' must be 1..31" in e for e in bad)

    ok = _triggers([_sched(schedule={"every": "monthly", "at": "00:00", "dom": 15})])
    assert ok == []


def test_event_trigger_validation():
    """An event trigger needs an entity type + a valid `on` (#429 D)."""
    base = {"type": "event", "id": "t", "workflow_id": "w", "acting_user": "bot"}
    ok = _triggers([{**base, "entity": "issue", "on": "created"}])
    assert ok == []
    no_entity = _triggers([{**base, "on": "created"}])
    assert any("needs an 'entity' type" in e for e in no_entity)
    bad_on = _triggers([{**base, "entity": "issue", "on": "deleted"}])
    assert any("event 'on' must be one of" in e for e in bad_on)


def test_id_and_workflow_id_rules():
    errs = _triggers([_sched(id=""), _sched(id="dup"), _sched(id="dup", workflow_id="")])
    assert any("missing its 'id'" in e for e in errs)
    assert any("duplicate trigger id 'dup'" in e for e in errs)
    assert any("'workflow_id' is required" in e for e in errs)


def test_scheduled_trigger_needs_item_id():
    errs = _triggers([_sched(item_id="")])
    assert any("needs an 'item_id'" in e for e in errs)


def test_tz_must_be_a_known_zone():
    """A valid IANA tz passes; an unknown one is a static error. DST semantics remain a
    documented known-limitation (#429 C2), not enforced here."""
    good = _triggers([_sched(schedule={"every": "daily", "at": "03:00", "tz": "Asia/Taipei"})])
    assert good == []
    bad = _triggers([_sched(schedule={"every": "daily", "at": "03:00", "tz": "Mars/Phobos"})])
    assert any("not a known IANA time zone" in e for e in bad)


def test_parse_rejects_bad_json_and_unknown_type():
    with pytest.raises(TriggerError, match="not valid JSON"):
        parse_triggers("{not json")
    with pytest.raises(TriggerError):
        parse_triggers(json.dumps({"triggers": [{"type": "webhook", "id": "x"}]}))


# ── period evaluation (#429 P7 / C2) ─────────────────────────────────────────


def test_daily_fire_window_and_once_per_period_gate():
    """A daily schedule's fire_window is the local date; it becomes due only once its
    target time has passed AND it hasn't fired for that window yet (#429 C2, the
    code_sync once-per-period gate)."""
    s = Schedule(every="daily", at="03:00")
    assert fire_window(s, datetime(2026, 7, 4, 12, 0)) == "2026-07-04"

    before = datetime(2026, 7, 4, 2, 30)  # before today's 03:00 target
    assert not is_due(s, before, last_window="2026-07-03")
    after = datetime(2026, 7, 4, 3, 30)  # past today's target, not fired today
    assert is_due(s, after, last_window="2026-07-03")
    assert not is_due(s, after, last_window="2026-07-04")  # already fired this window


def test_weekly_fire_window_targets_the_named_weekday():
    """A weekly schedule keys by ISO week and targets its `dow` within that week."""
    s = Schedule(every="weekly", dow="wed", at="09:00")
    # 2026-07-08 is the Wednesday of ISO week 28
    assert fire_window(s, datetime(2026, 7, 8, 10, 0)) == "2026-W28"
    mon = datetime(2026, 7, 6, 10, 0)  # Monday — before Wednesday's target
    assert not is_due(s, mon, last_window="2026-W27")
    wed = datetime(2026, 7, 8, 9, 30)  # past Wednesday 09:00
    assert is_due(s, wed, last_window="2026-W27")


async def test_sweeper_fires_due_trigger_once_and_elects_a_single_leader():
    """The sweeper fires a due trigger, then not again for the same window (once-per-period),
    and a CAS claim makes exactly one of two racing pods start the run (#429 P7)."""
    trig = ScheduleTrigger(
        id="weekly",
        workflow_id="report",
        acting_user="bot",
        item_id="i1",
        schedule=Schedule(every="daily", at="03:00"),
    )
    fired: list[tuple[str, str]] = []

    async def start(t: ScheduleTrigger, win: str) -> None:
        fired.append((t.id, win))

    store = FakeStore()
    now = _fixed_now(datetime(2026, 7, 4, 3, 30))  # past today's 03:00 target
    sweep = TriggerSweeper(load=lambda: [trig], store=store, start=start, now_utc=now)

    await sweep.tick()
    assert fired == [("weekly", "2026-07-04")]  # fired for today's window
    await sweep.tick()
    assert fired == [("weekly", "2026-07-04")]  # already fired this window → no re-fire

    # a second pod sharing the store, same window → CAS claim already taken → no double-fire
    fired2: list[tuple[str, str]] = []

    async def start2(t, win):
        fired2.append((t.id, win))

    store2_shared = store  # same durable store across pods
    store2_shared._last.pop("weekly")  # simulate the peer not yet knowing last_window
    sweep2 = TriggerSweeper(load=lambda: [trig], store=store2_shared, start=start2, now_utc=now)
    await sweep2.tick()
    assert fired2 == []  # the (weekly, 2026-07-04) claim is already held → loser doesn't start


async def test_sweeper_keys_the_store_by_the_globally_qualified_trigger_id():
    """Two profiles can each declare a trigger id 'weekly', so the sweeper claims by the
    globally-qualified ``slug:profile:id`` — never the bare, collision-prone file-local id
    (#429 P7). ``start`` still receives the trigger carrying its slug/profile so it can target
    the right workflow."""
    trig = ScheduleTrigger(
        id="weekly",
        workflow_id="report",
        acting_user="bot",
        item_id="i1",
        slug="rca",
        profile="echo",
        schedule=Schedule(every="daily", at="03:00"),
    )
    claimed: list[str] = []

    class SpyStore(ITriggerStore):
        def last_window(self, trigger_id: str) -> str:
            return ""

        def try_claim(self, trigger_id: str, fire_window: str) -> bool:
            claimed.append(trigger_id)
            return True

        def record_run(self, trigger_id: str, run_id: str) -> None: ...
        def note_resume(self, trigger_id: str) -> None: ...
        def clear_run(self, trigger_id: str) -> None: ...

        def get_run(self, trigger_id: str) -> tuple[str, int]:
            return ("", 0)

    fired: list[tuple[str, str, str]] = []

    async def start(t: ScheduleTrigger, win: str) -> None:
        fired.append((t.slug, t.profile, t.id))

    now = _fixed_now(datetime(2026, 7, 4, 3, 30))
    sweep = TriggerSweeper(load=lambda: [trig], store=SpyStore(), start=start, now_utc=now)
    await sweep.tick()
    assert claimed == ["rca:echo:weekly"]
    assert fired == [("rca", "echo", "weekly")]


# ── #429 P8: sweeper orphan pickup + abandon ─────────────────────────────────


class FakeOrphan:
    """A scripted ``IOrphanOps``: every run classifies as ``disp``; resume/abandon are recorded."""

    def __init__(self, disp: str, *, resume_wins: bool = True) -> None:
        self._disp = disp
        self._resume_wins = resume_wins
        self.resumed: list[str] = []
        self.abandoned: list[tuple[str, str]] = []

    def disposition(self, run_id: str, grace_ms: int) -> str:
        return self._disp

    async def resume(self, run_id: str, *, slug: str, profile: str, grace_ms: int) -> bool:
        self.resumed.append(run_id)
        return self._resume_wins

    async def abandon(self, run_id: str, *, reason: str) -> None:
        self.abandoned.append((run_id, reason))


def _orphan_trigger() -> ScheduleTrigger:
    return ScheduleTrigger(
        id="w",
        workflow_id="r",
        acting_user="b",
        item_id="i",
        slug="s",
        profile="p",
        schedule=Schedule(every="daily", at="03:00"),
    )


def _orphan_sweeper(store, orphan, fired, **kw):
    async def start(t, win):
        fired.append(win)
        return "run-new"

    return TriggerSweeper(
        load=lambda: [_orphan_trigger()],
        store=store,
        start=start,
        now_utc=_fixed_now(datetime(2026, 7, 4, 3, 30)),  # a new daily window is due
        orphan=orphan,
        grace_ms=1_000,
        max_resume_attempts=2,
        **kw,
    )


async def test_sweeper_resumes_a_stuck_orphan_before_firing_a_new_window():
    """#429 F-1: a stuck orphan of the previous window is resumed FIRST, and the new window is
    deferred this tick — the old debt is paid before firing again."""
    store = FakeStore()
    store.try_claim("s:p:w", "2026-07-03")  # a prior window
    store.record_run("s:p:w", "run-old")  # whose run is now orphaned
    orphan, fired = FakeOrphan("stuck"), []
    await _orphan_sweeper(store, orphan, fired).tick()
    assert orphan.resumed == ["run-old"]  # paid the old debt
    assert store.get_run("s:p:w") == ("run-old", 2)  # resume attempt counted
    assert fired == []  # the new window did NOT fire this tick (F-1)


async def test_sweeper_abandons_an_orphan_past_budget_then_fires_the_new_window():
    """#429 F-2: once the resume budget is spent the orphan is abandoned (discoverable), the
    debt is written off, and the current window is then free to fire."""
    store = FakeStore()
    store.try_claim("s:p:w", "2026-07-03")
    store.record_run("s:p:w", "run-old")
    store.note_resume("s:p:w")  # attempts now 2 == max_resume_attempts
    orphan, fired = FakeOrphan("stuck"), []
    await _orphan_sweeper(store, orphan, fired).tick()
    assert [r for r, _ in orphan.abandoned] == ["run-old"]  # gave up, discoverably
    assert orphan.resumed == []
    assert fired == ["2026-07-04"]  # the new window fired after writing off the debt
    assert store.get_run("s:p:w") == ("run-new", 1)  # the new run is tracked


async def test_sweeper_defers_the_new_window_while_a_run_is_still_live():
    """A previous run still active (running-fresh / awaiting a human) holds the item — the
    sweeper does not fire a second run on it, and touches nothing."""
    store = FakeStore()
    store.try_claim("s:p:w", "2026-07-03")
    store.record_run("s:p:w", "run-live")
    orphan, fired = FakeOrphan("active"), []
    await _orphan_sweeper(store, orphan, fired).tick()
    assert fired == [] and orphan.resumed == [] and orphan.abandoned == []
    assert store.get_run("s:p:w") == ("run-live", 1)  # untouched


async def test_sweeper_clears_a_settled_run_then_fires_the_new_window():
    """Once the previous run settled (terminal), the slot is freed and the due window fires."""
    store = FakeStore()
    store.try_claim("s:p:w", "2026-07-03")
    store.record_run("s:p:w", "run-done")
    orphan, fired = FakeOrphan("settled"), []
    await _orphan_sweeper(store, orphan, fired).tick()
    assert fired == ["2026-07-04"]
    assert store.get_run("s:p:w") == ("run-new", 1)  # cleared, then the new run tracked


async def test_sweeper_records_the_started_run_for_orphan_tracking():
    """A fresh trigger with no prior run fires and records the run id, so a future sweep can
    chase it if this pod dies."""
    store = FakeStore()
    orphan, fired = FakeOrphan("gone"), []
    await _orphan_sweeper(store, orphan, fired).tick()
    assert fired == ["2026-07-04"]
    assert store.get_run("s:p:w") == ("run-new", 1)


async def test_sweeper_skips_disabled_and_not_due_triggers():
    async def start(t, win):
        raise AssertionError("should not fire")

    store = FakeStore()
    disabled = ScheduleTrigger(
        id="d",
        workflow_id="w",
        acting_user="b",
        item_id="i",
        enabled=False,
        schedule=Schedule(every="daily", at="03:00"),
    )
    not_due = ScheduleTrigger(
        id="n",
        workflow_id="w",
        acting_user="b",
        item_id="i",
        schedule=Schedule(every="daily", at="03:00"),
    )
    now = _fixed_now(datetime(2026, 7, 4, 2, 0))  # before 03:00 → not due
    sweep = TriggerSweeper(load=lambda: [disabled, not_due], store=store, start=start, now_utc=now)
    await sweep.tick()  # nothing fires (no AssertionError)


# ── specstar-backed store (#429 P7) ──────────────────────────────────────────


def test_specstar_store_claims_each_window_exactly_once(spec_instance: SpecStar):
    """The durable store elects a single winner per (trigger, window): the first claim of a
    window wins, a second claim of the SAME window loses (once-per-period across pods), and a
    NEW window claims fresh. This is the CAS single-row lease (#429 P7)."""
    register_trigger_store(spec_instance)
    store = SpecstarTriggerStore(spec_instance)

    assert store.last_window("t1") == ""  # never fired
    assert store.try_claim("t1", "2026-07-04") is True  # first claim of the window wins
    assert store.last_window("t1") == "2026-07-04"
    assert store.try_claim("t1", "2026-07-04") is False  # same window → loser, no double-fire
    assert store.try_claim("t1", "2026-07-05") is True  # the next window claims fresh
    assert store.last_window("t1") == "2026-07-05"


def test_specstar_store_keeps_triggers_independent(spec_instance: SpecStar):
    """Each trigger's window ledger is its own — claiming one trigger's window doesn't gate
    another's (the row is keyed by the composite trigger id)."""
    register_trigger_store(spec_instance)
    store = SpecstarTriggerStore(spec_instance)
    assert store.try_claim("rca:echo:weekly", "2026-W28") is True
    assert store.try_claim("hub:echo:weekly", "2026-W28") is True  # different trigger, same window
    assert store.last_window("rca:echo:weekly") == "2026-W28"


def test_specstar_store_tracks_the_windows_run_and_resume_attempts(spec_instance: SpecStar):
    """The ledger remembers the run a window started + how many times it was resumed (#429 P8),
    so a later sweep can chase an orphan; a NEW window resets the run slot."""
    register_trigger_store(spec_instance)
    store = SpecstarTriggerStore(spec_instance)

    assert store.get_run("t") == ("", 0)  # nothing recorded yet
    store.try_claim("t", "2026-07-04")
    store.record_run("t", "run-1")
    assert store.get_run("t") == ("run-1", 1)  # first start → attempt 1
    store.note_resume("t")
    assert store.get_run("t") == ("run-1", 2)  # a resume bumps the budget counter
    store.clear_run("t")
    assert store.get_run("t") == ("", 2)  # run forgotten (settled / abandoned)
    store.try_claim("t", "2026-07-05")  # a new window is a fresh run slot
    assert store.get_run("t") == ("", 0)
    assert store.last_window("t") == "2026-07-05"  # the window ledger still advanced


def test_register_trigger_store_is_idempotent(spec_instance: SpecStar):
    """Registering the model twice (every pod calls it at boot) is a no-op, not an error."""
    register_trigger_store(spec_instance)
    register_trigger_store(spec_instance)


# ── discovery / loading from profiles (#429 P7) ──────────────────────────────


def test_load_profile_triggers_fills_origin_and_filters_to_enabled_schedules(monkeypatch):
    """A profile's schedule triggers are loaded with their (slug, profile) origin filled in;
    disabled triggers and event triggers are excluded (the schedule sweeper only runs enabled
    schedules)."""
    from workspace_app.workflow import triggers as trg

    payload = json.dumps(
        {
            "triggers": [
                _sched(id="weekly"),
                _sched(id="off", enabled=False),
                {
                    "type": "event",
                    "id": "ev",
                    "workflow_id": "w",
                    "acting_user": "b",
                    "entity": "issue",
                },
            ]
        }
    )
    monkeypatch.setattr(trg, "load_profile_triggers_raw", lambda s, p: payload.encode())
    got = trg.load_profile_triggers("rca", "echo")
    assert [t.id for t in got] == ["weekly"]  # disabled + event excluded
    assert (got[0].slug, got[0].profile) == ("rca", "echo")


def test_load_profile_triggers_skips_an_invalid_file(monkeypatch):
    """A triggers.json with a static error (here: an empty acting_user) is skipped whole — one
    bad profile must not wedge the sweep — rather than firing a half-trusted trigger."""
    from workspace_app.workflow import triggers as trg

    bad = json.dumps({"triggers": [_sched(acting_user="")]})
    monkeypatch.setattr(trg, "load_profile_triggers_raw", lambda s, p: bad.encode())
    assert trg.load_profile_triggers("rca", "echo") == []


def test_load_profile_triggers_absent_file_is_empty(monkeypatch):
    from workspace_app.workflow import triggers as trg

    monkeypatch.setattr(trg, "load_profile_triggers_raw", lambda s, p: None)
    assert trg.load_profile_triggers("rca", "echo") == []


def test_discover_scans_every_app_and_profile(monkeypatch):
    from workspace_app.workflow import triggers as trg

    monkeypatch.setattr(trg, "discover_app_slugs", lambda: ["rca", "hub"])
    monkeypatch.setattr(trg, "list_profiles", lambda slug: ["echo"])
    monkeypatch.setattr(
        trg,
        "load_profile_triggers",
        lambda s, p: [
            ScheduleTrigger(
                id="w",
                workflow_id="x",
                acting_user="b",
                item_id="i",
                slug=s,
                profile=p,
                schedule=Schedule(every="daily", at="03:00"),
            )
        ],
    )
    got = trg.discover_schedule_triggers()
    assert [(t.slug, t.profile) for t in got] == [("rca", "echo"), ("hub", "echo")]


# ── start adapter → orchestrator (#429 P7 / E) ───────────────────────────────


async def test_build_trigger_start_launches_the_target_workflow_as_the_acting_user():
    """A due schedule trigger launches its ``workflow_id`` on its ``item_id`` in its
    ``profile``, under its ``acting_user`` as the captured authz scope (#429 E)."""
    from workspace_app.workflow.triggers import build_trigger_start

    calls: list[dict[str, str]] = []

    async def fake_start(**kw: str) -> str:
        calls.append(kw)
        return "run-1"

    t = ScheduleTrigger(
        id="w",
        workflow_id="report",
        acting_user="bot",
        item_id="i1",
        slug="rca",
        profile="echo",
        schedule=Schedule(every="daily", at="03:00"),
    )
    await build_trigger_start(fake_start)(t, "2026-07-04")
    assert calls == [
        {
            "slug": "rca",
            "item_id": "i1",
            "profile": "echo",
            "captured_user": "bot",
            "workflow_id": "report",
        }
    ]


async def test_build_trigger_start_skips_when_a_run_is_already_active():
    """A window colliding with an already-active run on that item is passed over (logged, not
    raised) — the once-per-window claim means the period is simply skipped; the next fires."""
    from workspace_app.workflow.orchestrator import ActiveRunExists
    from workspace_app.workflow.triggers import build_trigger_start

    async def busy(**kw: str) -> str:
        raise ActiveRunExists("i", "run-0")

    t = ScheduleTrigger(
        id="w",
        workflow_id="r",
        acting_user="b",
        item_id="i",
        slug="s",
        profile="p",
        schedule=Schedule(every="daily", at="03:00"),
    )
    await build_trigger_start(busy)(t, "win")  # must not raise


def test_monthly_dom_clamps_to_month_end():
    """dom=31 in February targets the last day of the month (#429 C2 edge 1), so the
    monthly trigger never silently skips a short month."""
    s = Schedule(every="monthly", dom=31, at="00:00")
    assert fire_window(s, datetime(2026, 2, 15)) == "2026-02"
    # Feb 2026 has 28 days → target is the 28th; on the 28th at/after 00:00 it is due
    assert is_due(s, datetime(2026, 2, 28, 0, 1), last_window="2026-01")
    assert not is_due(s, datetime(2026, 2, 27, 23, 0), last_window="2026-01")
