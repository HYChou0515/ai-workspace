"""Declarative trigger definitions (#429 P6) — the profile-level ``triggers.json``
schema, parse, and static validation. Runtime (sweeper / lease / event dispatch) is
P7–P9; this is the declarative layer only."""

from __future__ import annotations

import json
from typing import Any

import pytest

from workspace_app.workflow.triggers import (
    TriggerError,
    parse_triggers,
    validate_triggers,
)


def _triggers(items: list[dict[str, Any]]) -> list[str]:
    return validate_triggers(parse_triggers(json.dumps({"triggers": items})))


def _sched(**over: Any) -> dict[str, Any]:
    base = {
        "type": "schedule",
        "id": "t",
        "workflow_id": "w",
        "acting_user": "bot",
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
