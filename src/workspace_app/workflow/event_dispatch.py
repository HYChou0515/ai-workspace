"""Event-trigger dispatch (#429 P9) — fire a workflow run when an entity changes.

The plan modelled this on specstar ``event_handlers`` on the entity *resource*, but #419
entities are **file-first** (written through ``EntityStore`` → ``WorkspaceFile``, and the
warm-workspace facade routes to a sandbox with no specstar event at all). So the hook is the
**single write path itself**: ``EntityStore.create``/``update`` emit an ``EntityWriteEvent``
post-commit, in-request, on the writing pod — the same "fires once where the write happened"
property the plan wanted, adapted to the file-first reality (the 探勘校正 correction).

The dispatcher (this module) matches that event against declared ``EventTrigger``s and starts
the run under the trigger's ``acting_user`` (E-decl — the actor is data, the authz scope is the
declaration). It carries three grill-locked guarantees:

* **Recursion guard (D)** — a triggered run's writes carry an ``EntityOrigin`` (the trigger that
  spawned the run + the run's depth). Guard 1: a run never re-fires its OWN trigger (breaks the
  direct A→A loop). Guard 2: a global depth cap breaks INDIRECT cycles A→B→A (an actor marker
  can't — every step's actor is "some run"); once the chain would exceed the cap, nothing fires.
* **Delivery (D2d)** — in-request, one-shot (no queue, no sweeper), but each ``(trigger, entity)``
  keeps a processing high-water mark (the last version it fired for). A missed event (a pod dying
  between commit and dispatch) leaves the watermark behind the entity's version — discoverable by
  a query, so it's "can back-fill, doesn't proactively" rather than a silent loss. The watermark
  also makes a re-dispatch idempotent (fire once per version).
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable

from msgspec import Struct
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..entity.events import EntityOrigin, EntityWriteEvent
from .triggers import EventTrigger, trigger_key, where_matches

__all__ = [
    "EntityOrigin",
    "EntityWriteEvent",
    "EventTriggerDispatcher",
    "EventTriggerStart",
    "IEventWatermark",
    "SpecstarEventWatermark",
    "build_event_trigger_start",
    "entity_key",
    "register_event_watermark",
]

_log = logging.getLogger(__name__)

# The global trigger-chain depth cap (#429 P9 guard 2). A first-level (human-caused) run is
# depth 0; each further hop adds one. Small on purpose — real chains are 1–2 deep, and the cap
# is a cycle backstop, not a feature knob.
_DEFAULT_MAX_TRIGGER_DEPTH = 3


# Fire a run for a matched trigger + event at a given chain depth; returns the run id (or None
# if it couldn't start, e.g. an active run already holds the item).
EventTriggerStart = Callable[[EventTrigger, EntityWriteEvent, int], Awaitable[str | None]]

OrchestratorStart = Callable[..., Awaitable[str]]


def build_event_trigger_start(start_run: OrchestratorStart) -> EventTriggerStart:
    """Adapt the orchestrator's ``start`` into an ``EventTriggerStart`` (#429 P9): a matched
    event fires the trigger's ``workflow_id`` on the ENTITY's item (the change is local to that
    item), under the trigger's ``acting_user`` (E-decl), carrying the recursion marker
    (``origin_trigger`` = this trigger's key, ``trigger_depth`` = the chain depth) so the run's
    own entity writes can't re-fire it and an indirect cycle hits the cap. A collision with an
    already-active run on that item is skipped-with-a-log (like the schedule adapter)."""
    from .orchestrator import ActiveRunExists

    async def start(t: EventTrigger, event: EntityWriteEvent, depth: int) -> str | None:
        try:
            return await start_run(
                slug=t.slug,
                item_id=event.item_id,
                profile=t.profile,
                captured_user=t.acting_user,
                workflow_id=t.workflow_id,
                origin_trigger=trigger_key(t),
                trigger_depth=depth,
            )
        except ActiveRunExists:
            _log.info(
                "event trigger %s: item %s already has an active run — skipping %s #%d",
                trigger_key(t),
                event.item_id,
                event.type_name,
                event.number,
            )
            return None

    return start


class IEventWatermark(abc.ABC):
    """The per-``(trigger, entity)`` processing high-water mark (#429 P9 / D2d). Durable +
    shared so the "fire once per version" gate and the discoverable-lag query hold across pods."""

    @abc.abstractmethod
    def processed_version(self, trigger_id: str, entity_key: str) -> str:
        """The entity version this trigger last fired for (``""`` if never)."""

    @abc.abstractmethod
    def try_advance(self, trigger_id: str, entity_key: str, version: str) -> bool:
        """Record ``version`` as processed iff it differs from the last — returns True when it
        advanced (this caller should fire), False when this version was already handled (an
        idempotent re-dispatch / a peer beat us). This is the once-per-version gate."""


def entity_key(item_id: str, type_name: str, number: int) -> str:
    """The watermark key for one entity record — its (item, type, number) natural key."""
    return f"{item_id}:{type_name}:{number}"


class EventTriggerDispatcher:
    """Match an ``EntityWriteEvent`` to declared event triggers and start their runs (#429 P9).

    ``triggers`` is re-scanned per event (an edited ``triggers.json`` takes effect without a
    restart); ``app_of_item`` resolves an item to its app slug (a trigger only fires for its own
    app's entities); ``start`` launches the run; ``watermark`` gives once-per-version delivery."""

    def __init__(
        self,
        *,
        triggers: Callable[[], list[EventTrigger]],
        app_of_item: Callable[[str], str | None],
        start: EventTriggerStart,
        watermark: IEventWatermark,
        max_depth: int = _DEFAULT_MAX_TRIGGER_DEPTH,
    ) -> None:
        self._triggers = triggers
        self._app_of_item = app_of_item
        self._start = start
        self._watermark = watermark
        self._max_depth = max_depth

    async def dispatch(self, event: EntityWriteEvent) -> None:
        """Fire every matching event trigger for ``event``. Errors from a single trigger are
        swallowed (logged) so one bad trigger can't turn a committed entity write into a 500 —
        the reindex-on-edit contract."""
        origin = event.origin
        next_depth = (origin.depth + 1) if origin is not None else 0
        # Guard 2 (depth cap): a write already at/over the chain cap spawns no further run, so an
        # indirect cycle A→B→A can climb only `max_depth` hops before it stops.
        if next_depth > self._max_depth:
            _log.info(
                "event dispatch: trigger-chain depth cap %d reached (%s #%d) — not firing",
                self._max_depth,
                event.type_name,
                event.number,
            )
            return
        slug = await asyncio.to_thread(self._app_of_item, event.item_id)
        if slug is None:
            return  # the item's app can't be resolved → nothing to match against
        for t in await asyncio.to_thread(self._triggers):
            with contextlib.suppress(Exception):
                await self._maybe_fire(t, event, slug, origin, next_depth)

    async def _maybe_fire(
        self,
        t: EventTrigger,
        event: EntityWriteEvent,
        slug: str,
        origin: EntityOrigin | None,
        next_depth: int,
    ) -> None:
        key = trigger_key(t)
        if t.slug != slug or t.entity != event.type_name or t.on != event.action:
            return  # not this trigger's app / entity / create-vs-update
        # Guard 1 (self-trigger): a run does not re-fire the very trigger that spawned it — the
        # direct A→A loop. (Indirect cycles are caught by the depth cap above.)
        if origin is not None and origin.trigger == key:
            return
        if not where_matches(t.where, event.fields):
            return  # narrowed out (a trivial edit that doesn't meet the condition)
        # D2d once-per-version: fire only when this entity version is new to this trigger. A
        # re-dispatch (back-fill) of an already-handled version is a no-op.
        ekey = entity_key(event.item_id, event.type_name, event.number)
        if not await asyncio.to_thread(self._watermark.try_advance, key, ekey, event.version):
            return
        await self._start(t, event, next_depth)


class _EventWatermark(Struct):
    """One ``(trigger, entity)`` processing high-water mark. ``resource_id`` is a hash of the
    composite (the natural key holds ``/`` in item ids, which a specstar id forbids)."""

    trigger_id: str
    entity_key: str
    version: str


def _watermark_id(trigger_id: str, entity_key: str) -> str:
    return hashlib.sha256(f"{trigger_id}\x00{entity_key}".encode()).hexdigest()


def register_event_watermark(spec: SpecStar) -> None:
    """Idempotently register the watermark model (every pod calls it at boot)."""
    with contextlib.suppress(ValueError):
        spec.add_model(_EventWatermark)


class SpecstarEventWatermark(IEventWatermark):
    """``IEventWatermark`` over a shared specstar backend — one row per ``(trigger, entity)``,
    keyed by a hash of the composite so an item id's ``/`` never breaks the resource id."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _row(self, trigger_id: str, entity_key: str) -> _EventWatermark | None:
        rm = self._spec.get_resource_manager(_EventWatermark)
        try:
            res = rm.get(_watermark_id(trigger_id, entity_key))
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        data = res.data
        assert isinstance(data, _EventWatermark)
        return data

    def processed_version(self, trigger_id: str, entity_key: str) -> str:
        row = self._row(trigger_id, entity_key)
        return row.version if row is not None else ""

    def try_advance(self, trigger_id: str, entity_key: str, version: str) -> bool:
        rm = self._spec.get_resource_manager(_EventWatermark)
        rid = _watermark_id(trigger_id, entity_key)
        row = self._row(trigger_id, entity_key)
        if row is None:
            rm.create(_EventWatermark(trigger_id, entity_key, version), resource_id=rid)
            return True  # first time we see this entity for this trigger → fire
        if row.version == version:
            return False  # already fired for this version (idempotent re-dispatch)
        rm.update(rid, _EventWatermark(trigger_id, entity_key, version))
        return True
