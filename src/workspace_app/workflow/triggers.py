"""Declarative workflow triggers (#429 P6) — the profile-level ``triggers.json``.

A trigger binds a **schedule** (time) or an **event** (an entity change) to a workflow,
under a fixed ``acting_user`` (the authz scope every triggered run executes in — the E-decl
decision: *actor is data, acting_user is authz*). This module is the DECLARATIVE layer:
the schema, ``parse_triggers`` (decode), and ``validate_triggers`` (static problems as
strings — the same lint-not-crash contract as the DSL's ``validate_def``). The runtime
(the sweep loop, the CAS lease + orphan pickup, the event dispatch) lands in P7–P9.

Grill decisions (docs/plan-issue-429.md, Forks A–F):

* **C2** period schedule ``{every: daily|weekly|monthly, at, dow?, dom?, tz?}`` — NOT full
  cron: its ``fire_window`` = the period, which gives the CAS lease + orphan pickup a clean
  key. ``dom`` past the month's length clamps to month-end (a hint, not an error); the
  ``every``↔``dow``/``dom`` combinations are static errors.
* **E-decl** ``acting_user`` is REQUIRED (a headless run with no captured user must fail at
  declaration, never fall back to a system identity).
* **D** an event trigger fires on an entity ``created``/``updated`` with an optional
  ``where`` filter (P9 wires it onto the specstar ``event_handlers``).
"""

from __future__ import annotations

import msgspec
from msgspec import Struct, field

_DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_EVERY = ("daily", "weekly", "monthly")
_EVENT_ON = ("created", "updated")


class TriggerError(Exception):
    """A ``triggers.json`` is malformed (bad JSON / unknown field / bad ``type`` tag) —
    raised by ``parse_triggers``. ``validate_triggers`` returns the *static* problems as
    strings instead, so an operator sees them before the trigger ever fires."""


class Schedule(Struct, forbid_unknown_fields=True):
    """A period schedule (#429 C2). ``every`` picks the period; ``at`` is the local
    ``HH:MM`` target; ``dow`` names the weekday (weekly); ``dom`` the day-of-month
    (monthly, default 1). ``tz`` is an IANA zone (``""`` ⇒ server-local)."""

    every: str
    at: str = "00:00"
    dow: str = ""
    dom: int = 0
    tz: str = ""


class ScheduleTrigger(Struct, tag="schedule", forbid_unknown_fields=True):
    """Fire ``workflow_id`` on a recurring period as ``acting_user`` (#429 P6/P7)."""

    id: str
    workflow_id: str
    acting_user: str
    schedule: Schedule
    enabled: bool = True


class EventTrigger(Struct, tag="event", forbid_unknown_fields=True):
    """Fire ``workflow_id`` when an entity of type ``entity`` is ``created``/``updated``
    (``on``), optionally narrowed by ``where`` (field → required value), as ``acting_user``
    (#429 P6/P9)."""

    id: str
    workflow_id: str
    acting_user: str
    entity: str = ""
    on: str = "created"
    where: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


Trigger = ScheduleTrigger | EventTrigger


class TriggerFile(Struct, forbid_unknown_fields=True):
    """A parsed ``triggers.json`` — the declarative whole."""

    triggers: list[Trigger] = field(default_factory=list)


def parse_triggers(raw: bytes | str) -> TriggerFile:
    """Decode ``triggers.json`` bytes into a ``TriggerFile``. Raises ``TriggerError`` on
    malformed JSON, an unknown field, or a bad trigger ``type`` (msgspec pinpoints it)."""
    data = raw.encode() if isinstance(raw, str) else raw
    try:
        return msgspec.json.decode(data, type=TriggerFile)
    except msgspec.ValidationError as exc:  # bad field / tag first
        raise TriggerError(str(exc)) from exc
    except msgspec.DecodeError as exc:  # genuine JSON syntax error
        raise TriggerError(f"not valid JSON: {exc}") from exc


def validate_triggers(tf: TriggerFile) -> list[str]:
    """Static problems with a parsed ``triggers.json``, as human-readable strings (empty
    ⇒ valid). Errors are prefixed plainly; advisory hints start with ``hint:``. This is
    the lint-not-crash contract — an operator fixes these before the trigger runs."""
    errs: list[str] = []
    seen: set[str] = set()
    for t in tf.triggers:
        where = f"trigger {t.id!r}" if t.id else "trigger"
        if not t.id:
            errs.append("a trigger is missing its 'id'")
        elif t.id in seen:
            errs.append(f"duplicate trigger id {t.id!r}")
        else:
            seen.add(t.id)
        if not t.workflow_id:
            errs.append(f"{where}: 'workflow_id' is required")
        # E-decl: acting_user is the run's authz scope — a headless run cannot fall back to
        # a system identity, so an empty acting_user is a static error (never a run-time one).
        if not t.acting_user:
            errs.append(f"{where}: 'acting_user' is required (the run's authz scope)")
        if isinstance(t, ScheduleTrigger):
            _validate_schedule(t.schedule, where, errs)
        else:
            _validate_event(t, where, errs)
    return errs


def _validate_schedule(s: Schedule, where: str, errs: list[str]) -> None:
    if s.every not in _EVERY:
        errs.append(f"{where}: schedule 'every' must be one of {list(_EVERY)}")
    if not _valid_hhmm(s.at):
        errs.append(f"{where}: schedule 'at' must be 'HH:MM' (24h), got {s.at!r}")
    if s.tz and not _valid_tz(s.tz):
        # KNOWN LIMITATION (#429 C2): a DST zone's 'nonexistent/duplicate local time' at the
        # spring/autumn switch is undefined here — prefer a non-DST zone. We only static-check
        # that the zone EXISTS; the DST semantics are documented, not enforced.
        errs.append(f"{where}: schedule 'tz' {s.tz!r} is not a known IANA time zone")
    # #429 C2 edge 2: the every↔dow/dom combination is fixed and statically checked.
    if s.every == "daily":
        if s.dow or s.dom:
            errs.append(f"{where}: a daily schedule takes only 'at' (no 'dow'/'dom')")
    elif s.every == "weekly":
        if s.dow not in _DOW:
            errs.append(f"{where}: a weekly schedule needs 'dow' in {list(_DOW)}")
        if s.dom:
            errs.append(f"{where}: a weekly schedule takes 'dow', not 'dom'")
    elif s.every == "monthly":
        if s.dow:
            errs.append(f"{where}: a monthly schedule takes 'dom', not 'dow'")
        if s.dom and not 1 <= s.dom <= 31:
            errs.append(f"{where}: schedule 'dom' must be 1..31, got {s.dom}")
        # #429 C2 edge 1: a dom past the shortest month clamps to month-end at fire time.
        elif s.dom > 28:
            errs.append(f"hint: {where}: 'dom' {s.dom} clamps to month-end in shorter months")


def _validate_event(t: EventTrigger, where: str, errs: list[str]) -> None:
    if not t.entity:
        errs.append(f"{where}: an event trigger needs an 'entity' type")
    if t.on not in _EVENT_ON:
        errs.append(f"{where}: event 'on' must be one of {list(_EVENT_ON)}")


def _valid_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    hh, mm = int(parts[0]), int(parts[1])
    return 0 <= hh <= 23 and 0 <= mm <= 59


def _valid_tz(tz: str) -> bool:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
