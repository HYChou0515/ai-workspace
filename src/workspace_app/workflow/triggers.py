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

import abc
import asyncio
import calendar
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import msgspec
from msgspec import Struct, field
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from ..apps.catalog import discover_app_slugs
from ..apps.profiles import list_profiles, load_profile_triggers_raw
from .run import RunStatus

_log = logging.getLogger(__name__)

# #429 P8: the default orphan-handling knobs. The grace must exceed a healthy step's worst
# case so a slow-but-live run is never falsely resumed (the #227 stuck-run tradeoff); the
# resume budget maps the "cron abandon after N windows (1~2)" decision onto resume attempts.
_DEFAULT_ORPHAN_GRACE_MS = 3_600_000  # 1h, matching the index-sweeper stuck grace
_DEFAULT_MAX_RESUME_ATTEMPTS = 2

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
    """Fire ``workflow_id`` on ``item_id`` (the target workspace) on a recurring period as
    ``acting_user`` (#429 P6/P7). ``slug``/``profile`` come from the trigger file's
    location; ``item_id`` is the run's workspace and is required for a scheduled run."""

    id: str
    workflow_id: str
    acting_user: str
    item_id: str
    schedule: Schedule
    enabled: bool = True
    # Runtime context, NOT declared in the file — the loader fills these from the trigger
    # file's location (``apps/<slug>/profiles/<profile>/triggers.json``), the "location is
    # authority" decision. They give the run its (slug, profile) target and, joined with
    # ``id``, the globally-unique claim key (a bare id is only unique within one file).
    slug: str = ""
    profile: str = ""


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
    # Runtime context, filled by the loader from the file's location (like ScheduleTrigger) —
    # slug scopes the trigger to its app's entity writes; the fired run's item is the entity's.
    slug: str = ""
    profile: str = ""


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
            if not t.item_id:
                errs.append(f"{where}: a scheduled trigger needs an 'item_id' (target workspace)")
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


def load_profile_triggers(slug: str, profile: str) -> list[ScheduleTrigger]:
    """A profile's ENABLED schedule triggers (#429 P7), each stamped with its (slug, profile)
    origin from the file's location. A malformed or statically-invalid ``triggers.json`` is
    skipped WHOLE with a loud warning — one bad profile must never wedge the sweep (resilient,
    #227-style), and a half-trusted trigger must never fire (the E-decl / 'no silent errors'
    contract). Event triggers are handled by P9's event dispatch, not the schedule sweep."""
    return [
        t for t in _valid_triggers(slug, profile) if isinstance(t, ScheduleTrigger) and t.enabled
    ]


def discover_schedule_triggers() -> list[ScheduleTrigger]:
    """Every enabled schedule trigger across all apps' profiles (#429 P7) — the sweeper's
    ``load``. Re-scanned each tick so an operator's edit to a ``triggers.json`` takes effect
    without a restart."""
    out: list[ScheduleTrigger] = []
    for slug in discover_app_slugs():
        for profile in list_profiles(slug):
            out.extend(load_profile_triggers(slug, profile))
    return out


def _valid_triggers(slug: str, profile: str) -> list[Trigger]:
    """A profile's parsed + statically-valid triggers (either kind), each stamped with its
    (slug, profile) origin. A malformed or invalid file is skipped WHOLE with a loud warning —
    the same lint-not-crash / no-half-trusted-trigger contract as the schedule loader."""
    raw = load_profile_triggers_raw(slug, profile)
    if raw is None:
        return []
    try:
        tf = parse_triggers(raw)
    except TriggerError as exc:
        _log.warning("skipping malformed triggers.json for %s/%s: %s", slug, profile, exc)
        return []
    errs = [e for e in validate_triggers(tf) if not e.startswith("hint:")]
    if errs:
        _log.warning("skipping invalid triggers for %s/%s: %s", slug, profile, "; ".join(errs))
        return []
    return [msgspec.structs.replace(t, slug=slug, profile=profile) for t in tf.triggers]


def load_profile_event_triggers(slug: str, profile: str) -> list[EventTrigger]:
    """A profile's ENABLED event triggers (#429 P9), origin-stamped. Event triggers fire on an
    entity write in-request (P9 dispatch), NOT via the schedule sweep."""
    return [t for t in _valid_triggers(slug, profile) if isinstance(t, EventTrigger) and t.enabled]


def discover_event_triggers() -> list[EventTrigger]:
    """Every enabled event trigger across all apps' profiles (#429 P9) — the entity-write
    dispatcher's trigger source, re-scanned so an operator's edit takes effect without a
    restart."""
    out: list[EventTrigger] = []
    for slug in discover_app_slugs():
        for profile in list_profiles(slug):
            out.extend(load_profile_event_triggers(slug, profile))
    return out


def where_matches(where: dict[str, str], fields: dict[str, object]) -> bool:
    """Does an entity's ``fields`` satisfy an event trigger's ``where`` (#429 P9/D)? Every
    declared ``field: value`` must equal the record's field (string-compared, so "5" matches
    5) — the narrowing that stops a trivial edit from firing. An empty ``where`` matches all."""
    return all(str(fields.get(key, "")) == want for key, want in where.items())


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


# ── period evaluation (#429 P7 / C2) ─────────────────────────────────────────
#
# The window IS the period (daily=date, weekly=ISO week, monthly=year-month), so a run's
# (trigger_id, fire_window) is a stable CAS-lease + orphan key (P8). A schedule becomes due
# with the code_sync gate: the CURRENT period's target time has passed AND it has not fired
# for that window yet — tolerant of poll cadence, and a missed window fires late (catch-up)
# rather than being dropped like a cron 'fire at this instant' would.


def window_key(every: str, now: datetime) -> str:
    """The period bucket key for ``now``: ``YYYY-MM-DD`` (daily), ``YYYY-Www`` ISO week
    (weekly), or ``YYYY-MM`` (monthly); any other ``every`` buckets by day. Shared by the
    schedule trigger's fire dedup (``fire_window``) and #435's ``send_notification``
    per-window fingerprint — one truncation rule, so a "daily" notify and a "daily" schedule
    bucket identically."""
    if every == "weekly":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if every == "monthly":
        return f"{now.year:04d}-{now.month:02d}"
    return now.strftime("%Y-%m-%d")


def fire_window(s: Schedule, now: datetime) -> str:
    """The period key for ``now`` (local to the schedule's tz) — see :func:`window_key`."""
    return window_key(s.every, now)


def period_target(s: Schedule, now: datetime) -> datetime:
    """The datetime within ``now``'s period the schedule targets — today at ``at`` (daily),
    the ``dow`` day of this ISO week at ``at`` (weekly), or ``dom`` (clamped to the month's
    length, C2 edge 1) at ``at`` (monthly)."""
    hh, mm = (int(p) for p in s.at.split(":"))
    if s.every == "weekly":
        target_wd = _DOW.index(s.dow) + 1  # ISO weekday 1..7
        day = now.date() + timedelta(days=target_wd - now.isoweekday())
        return datetime(day.year, day.month, day.day, hh, mm)
    if s.every == "monthly":
        last = calendar.monthrange(now.year, now.month)[1]
        dom = min(s.dom or 1, last)  # clamp to month-end
        return datetime(now.year, now.month, dom, hh, mm)
    return datetime(now.year, now.month, now.day, hh, mm)


def is_due(s: Schedule, now: datetime, last_window: str) -> bool:
    """Is the schedule due at ``now`` given the last window it fired for? Due when the
    current period's target has passed AND it hasn't already fired for this window."""
    return now >= period_target(s, now) and last_window != fire_window(s, now)


def _valid_tz(tz: str) -> bool:
    from zoneinfo import ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


# ── the sweep loop (#429 P7) ─────────────────────────────────────────────────


class ITriggerStore(abc.ABC):
    """The durable state a scheduled trigger needs across pods (#429 P7): the last window
    it fired for, and an atomic per-``(trigger_id, window)`` claim that elects a single pod
    to fire it. The specstar-backed impl makes ``try_claim`` a CAS create (only the first
    caller across all pods wins), the same shape as the blob-GC lease / sandbox address."""

    @abc.abstractmethod
    def last_window(self, trigger_id: str) -> str:
        """The most recent window this trigger fired for (``""`` if never)."""

    @abc.abstractmethod
    def try_claim(self, trigger_id: str, fire_window: str) -> bool:
        """Atomically claim ``(trigger_id, fire_window)``. Returns True for the single
        caller that wins the race (which then starts the run), False for everyone else —
        so a window fires exactly once across all pods. Winning a NEW window also resets the
        run slot (``run_id``/``attempts``) — a fresh window is a fresh run."""

    @abc.abstractmethod
    def record_run(self, trigger_id: str, run_id: str) -> None:
        """Record the run this trigger just started for the current window (#429 P8), so a
        later sweep can find it if the driving pod dies. Resets the resume counter to 1."""

    @abc.abstractmethod
    def note_resume(self, trigger_id: str) -> None:
        """Count one resume of the current window's orphan run (#429 F-2) — the resume budget
        that, once spent, tips the orphan into ``abandon``."""

    @abc.abstractmethod
    def clear_run(self, trigger_id: str) -> None:
        """Forget the current window's run (#429 P8) — it settled or was abandoned, so it is no
        longer an orphan to chase."""

    @abc.abstractmethod
    def get_run(self, trigger_id: str) -> tuple[str, int]:
        """The current window's ``(run_id, resume_attempts)`` — ``("", 0)`` when none."""


# Cross-pod contention on ONE trigger's window row is a handful of pods for a few
# microseconds at the period boundary, so a generous cap only bites under pathological
# churn (mirrors SpecstarAddressStore / the epoch CAS).
_MAX_CAS_RETRIES = 100


class _TriggerWindow(Struct):
    """The last fire-window a scheduled trigger claimed. ``resource_id == trigger_id`` so
    the claim is a SINGLE-row CAS: advancing ``last_window`` to a new window iff no peer
    already advanced it is both the leader election AND the once-per-window gate in one
    atomic step (no separate claim-set needed). ``run_id``/``attempts`` track the window's
    in-flight run for orphan pickup (#429 P8) — cleared when a new window is claimed."""

    trigger_id: str
    last_window: str
    run_id: str = ""
    attempts: int = 0


def register_trigger_store(spec: SpecStar) -> None:
    """Idempotently register the window-ledger model. Safe to call on every pod at boot."""
    with contextlib.suppress(ValueError):
        spec.add_model(_TriggerWindow)


class SpecstarTriggerStore(ITriggerStore):
    """``ITriggerStore`` over a shared specstar backend (#429 P7). A trigger's window ledger
    is one row keyed by its (globally-qualified) id; ``try_claim`` CAS-advances it, so across
    all pods exactly one claim of a given window wins. Blocking specstar I/O is called
    directly (sync) — the ``TriggerSweeper`` offloads the whole store call to a thread, the
    same way ``code_sync`` offloads its ``tick``."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _row(self, trigger_id: str) -> _TriggerWindow | None:
        rm = self._spec.get_resource_manager(_TriggerWindow)
        try:
            res = rm.get(trigger_id)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        data = res.data
        assert isinstance(data, _TriggerWindow)
        return data

    def _write_fields(self, trigger_id: str, **fields: object) -> None:
        """Read-modify-write the ledger row, preserving unspecified fields. The current window's
        run slot has a single writer at a time (the claim/resume winner), so no CAS is needed."""
        row = self._row(trigger_id)
        if row is None:
            return
        rm = self._spec.get_resource_manager(_TriggerWindow)
        rm.update(trigger_id, msgspec.structs.replace(row, **fields))

    def last_window(self, trigger_id: str) -> str:
        row = self._row(trigger_id)
        return row.last_window if row is not None else ""

    def record_run(self, trigger_id: str, run_id: str) -> None:
        self._write_fields(trigger_id, run_id=run_id, attempts=1)

    def note_resume(self, trigger_id: str) -> None:
        row = self._row(trigger_id)
        if row is not None:
            self._write_fields(trigger_id, attempts=row.attempts + 1)

    def clear_run(self, trigger_id: str) -> None:
        self._write_fields(trigger_id, run_id="")

    def get_run(self, trigger_id: str) -> tuple[str, int]:
        row = self._row(trigger_id)
        return (row.run_id, row.attempts) if row is not None else ("", 0)

    def try_claim(self, trigger_id: str, fire_window: str) -> bool:
        rm = self._spec.get_resource_manager(_TriggerWindow)
        row = _TriggerWindow(trigger_id=trigger_id, last_window=fire_window)
        try:
            # First claim ever for this trigger: first-writer-wins create.
            rm.create(row, resource_id=trigger_id, if_not_exists=True)  # ty: ignore[unknown-argument]
            _log.info("trigger %s: claimed first window %s", trigger_id, fire_window)
            return True
        except DuplicateResourceError:
            pass  # a ledger row already exists — CAS-advance it below
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = rm.get(trigger_id)
            except ResourceIsDeletedError:  # pragma: no cover - triggers are never deleted
                rm.restore(trigger_id)
                rm.modify(trigger_id, row, status=RevisionStatus.draft)
                return True
            data = res.data
            assert isinstance(data, _TriggerWindow)
            if data.last_window == fire_window:
                _log.debug(
                    "trigger %s: window %s already claimed — skipping", trigger_id, fire_window
                )
                return False  # a peer (or a prior tick) already claimed this window
            try:
                rm.modify(
                    trigger_id,
                    row,
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                _log.info("trigger %s: advanced to window %s (CAS won)", trigger_id, fire_window)
                return True  # we advanced the window → we start the run
            except PreconditionFailedError:  # pragma: no cover - cross-pod CAS race
                continue  # a peer advanced between our get and modify → re-read
        raise RuntimeError(  # pragma: no cover - only under pathological churn
            f"trigger claim CAS exhausted retries for {trigger_id!r}"
        )


StartTrigger = Callable[["ScheduleTrigger", str], Awaitable[str | None]]
OrchestratorStart = Callable[..., Awaitable[str]]

# ── orphan pickup (#429 P8 / F) ──────────────────────────────────────────────
#
# A triggered run whose driving pod dies is left non-terminal with no live driver. The
# sweeper (any pod) classifies the trigger's last run and, before firing a NEW window,
# pays the old debt: a stuck orphan is resumed (F-1) until its budget is spent, then
# abandoned (F-2). The four dispositions:
_GONE = "gone"  # the run row is gone → nothing to chase
_SETTLED = "settled"  # terminal → resolved, free the slot
_STUCK = "stuck"  # RUNNING but heartbeat stale → an orphan to resume/abandon
_ACTIVE = "active"  # pending / awaiting_human / RUNNING-fresh → a live run, leave it


class IOrphanOps(abc.ABC):
    """What the sweeper needs from the run engine to chase orphans (#429 P8), kept behind an
    interface so the sweeper stays testable without a real orchestrator."""

    @abc.abstractmethod
    def disposition(self, run_id: str, grace_ms: int) -> str:
        """Classify ``run_id`` as one of gone / settled / stuck / active."""

    @abc.abstractmethod
    async def resume(self, run_id: str, *, slug: str, profile: str, grace_ms: int) -> bool:
        """Re-drive a stuck orphan from its journal; True iff this caller took the resume."""

    @abc.abstractmethod
    async def abandon(self, run_id: str, *, reason: str) -> None:
        """Give up on an orphan past its resume budget — a one-way, discoverable transition."""


class OrchestratorOrphanOps(IOrphanOps):
    """``IOrphanOps`` over a ``WorkflowOrchestrator`` (duck-typed to avoid an import cycle —
    only ``run_status`` / ``is_stuck`` / ``resume`` / ``abandon`` are used)."""

    def __init__(self, orchestrator: object) -> None:
        self._orch = orchestrator

    def disposition(self, run_id: str, grace_ms: int) -> str:
        status = self._orch.run_status(run_id)  # ty: ignore[unresolved-attribute]
        if status is None:
            return _GONE
        if status in (RunStatus.DONE, RunStatus.ERROR, RunStatus.CANCELLED):
            return _SETTLED
        if status is RunStatus.RUNNING and self._orch.is_stuck(run_id, grace_ms):  # ty: ignore[unresolved-attribute]
            return _STUCK
        return _ACTIVE  # pending / awaiting_human / RUNNING-but-fresh

    async def resume(self, run_id: str, *, slug: str, profile: str, grace_ms: int) -> bool:
        return await self._orch.resume(  # ty: ignore[unresolved-attribute]
            run_id, slug=slug, profile=profile, grace_ms=grace_ms
        )

    async def abandon(self, run_id: str, *, reason: str) -> None:
        await self._orch.abandon(run_id, reason=reason)  # ty: ignore[unresolved-attribute]


def build_trigger_start(start_run: OrchestratorStart) -> StartTrigger:
    """Adapt the orchestrator's ``start`` into the sweeper's ``StartTrigger`` (#429 P7): a due
    schedule trigger launches its ``workflow_id`` on its ``item_id`` in its ``profile``, under
    its ``acting_user`` as the captured authz scope (the E-decl decision — the run's identity
    is written in the declaration, never a system fallback). A window colliding with an already-
    active run on that item is passed over with a log (not raised): the once-per-window claim is
    already spent, so this period is simply skipped and the next window fires normally."""
    from .orchestrator import ActiveRunExists

    async def start(t: ScheduleTrigger, window: str) -> str | None:
        _log.info(
            "schedule trigger %s: firing workflow %s on item %s for window %s",
            trigger_key(t),
            t.workflow_id,
            t.item_id,
            window,
        )
        try:
            return await start_run(
                slug=t.slug,
                item_id=t.item_id,
                profile=t.profile,
                captured_user=t.acting_user,
                workflow_id=t.workflow_id,
            )
        except ActiveRunExists:
            _log.info(
                "trigger %s: item %s already has an active run — skipping window %s",
                trigger_key(t),
                t.item_id,
                window,
            )
            return None  # collision → nothing started; the next window re-evaluates

    return start


def trigger_key(t: Trigger) -> str:
    """The globally-unique key for a trigger (either kind) — ``slug:profile:id`` once the
    loader has filled its origin, else the bare ``id`` (tests / a not-yet-located trigger).
    ``:`` (not ``/``) because a specstar resource_id is slash-free (same rule as a SourceDoc
    id). Used as the schedule claim key AND the event trigger's recursion-marker / watermark
    key."""
    return f"{t.slug}:{t.profile}:{t.id}" if t.slug else t.id


class TriggerSweeper:
    """The poll-loop half of a scheduled trigger (#429 P7), modelled on the #355 code_sync
    sweeper: each tick, for every enabled trigger that is due (its period target has passed
    and it hasn't fired for this window), CAS-claim ``(id, window)`` to elect one pod, and
    the winner starts the run. Missed windows fire late (catch-up), never dropped.

    With an ``orphan`` handler wired (#429 P8), each tick FIRST settles the trigger's previous
    run before firing a new window (F-1 — pay the old debt first): a stuck orphan is resumed
    from its journal until its resume budget is spent, then abandoned to a discoverable
    terminal state (F-2); a still-live run defers the new window. Without ``orphan`` (the P7
    path) the sweeper just fires due windows."""

    def __init__(
        self,
        *,
        load: Callable[[], list[ScheduleTrigger]],
        store: ITriggerStore,
        start: StartTrigger,
        now_utc: Callable[[], datetime],
        orphan: IOrphanOps | None = None,
        grace_ms: int = _DEFAULT_ORPHAN_GRACE_MS,
        max_resume_attempts: int = _DEFAULT_MAX_RESUME_ATTEMPTS,
    ) -> None:
        self._load = load
        self._store = store
        self._start = start
        self._now_utc = now_utc
        self._orphan = orphan
        self._grace_ms = grace_ms
        self._max_attempts = max_resume_attempts

    def _local_now(self, tz: str) -> datetime:
        """``now`` in the schedule's zone as a naive datetime (the period math is naive-
        local). ``tz=""`` ⇒ UTC."""
        return self._now_utc().astimezone(ZoneInfo(tz or "UTC")).replace(tzinfo=None)

    async def tick(self) -> None:
        for t in self._load():
            if t.enabled:
                await self._tick_one(t)

    async def _tick_one(self, t: ScheduleTrigger) -> None:
        key = trigger_key(t)  # globally-unique; a bare id would collide across files
        # F-1: settle any orphan of the previous window before considering a new one. If the
        # run is still live (or we resumed it), defer the new window — one run per item at a
        # time, and the old debt is paid first.
        if not await self._settle_orphan(t, key):
            return
        now = self._local_now(t.schedule.tz)
        # Store calls are blocking specstar I/O — offload so a sweep never sits on the event
        # loop (the code_sync sweeper offloads its whole tick for the same reason).
        last = await asyncio.to_thread(self._store.last_window, key)
        if not is_due(t.schedule, now, last):
            return
        window = fire_window(t.schedule, now)
        if not await asyncio.to_thread(self._store.try_claim, key, window):
            return  # a peer claimed this window (leader election + once-per-window)
        run_id = await self._start(t, window)
        if run_id and self._orphan is not None:
            await asyncio.to_thread(self._store.record_run, key, run_id)  # track for orphan pickup

    async def _settle_orphan(self, t: ScheduleTrigger, key: str) -> bool:
        """Resolve the trigger's previous run. Returns True to PROCEED to a new window (no run,
        or one that settled / was abandoned), False to DEFER this tick (a live run, or an orphan
        we just resumed — F-1: pay the old debt before firing again)."""
        if self._orphan is None:
            return True
        run_id, attempts = await asyncio.to_thread(self._store.get_run, key)
        if not run_id:
            return True
        disp = await asyncio.to_thread(self._orphan.disposition, run_id, self._grace_ms)
        if disp == _ACTIVE:
            _log.debug("trigger %s: run %s still active — deferring new window", key, run_id)
            return False  # a live run holds the item — don't fire a second
        if disp == _STUCK:
            if attempts < self._max_attempts:  # F-1: resume the orphan first
                _log.info(
                    "trigger %s: resuming stuck orphan run %s (attempt %d/%d)",
                    key,
                    run_id,
                    attempts,
                    self._max_attempts,
                )
                if await self._orphan.resume(
                    run_id, slug=t.slug, profile=t.profile, grace_ms=self._grace_ms
                ):
                    await asyncio.to_thread(self._store.note_resume, key)
                return False  # resumed (or a peer did) — skip the new window this tick
            # F-2: resume budget spent → abandon to a discoverable terminal, then free the slot.
            _log.warning(
                "trigger %s: abandoning stuck orphan run %s after %d resume attempts",
                key,
                run_id,
                self._max_attempts,
            )
            await self._orphan.abandon(
                run_id, reason=f"stuck orphan abandoned after {self._max_attempts} resume attempts"
            )
        await asyncio.to_thread(self._store.clear_run, key)  # settled / gone / abandoned
        return True
