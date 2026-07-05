"""Event-trigger backfill (#429 P11) — the D2d on-demand reconciliation.

P9's ``EventTriggerDispatcher`` fires in-request and one-shot (no queue, no sweeper).
A pod that dies between an entity's commit and the dispatch leaves the trigger's
processing watermark BEHIND that entity's version — a run that should have fired but
didn't, silently. P9 chose "can back-fill, doesn't proactively": the watermark ledger
records the lag, and an operator reconciles on demand.

This module is that on-demand surface, built on the pieces P9 already exposes:

* ``find_trigger_lag`` — the QUERY. For each event trigger of an item's app, walk the
  item's current entities and report those whose version the trigger's watermark has
  not caught up to (respecting ``where`` — an entity the trigger would never fire for
  is not "behind"). Pure + read-only, so it doubles as a dry-run.
* ``backfill_trigger_lag`` — the CATCH-UP. Re-synthesise each behind entity's CURRENT
  state as an ``EntityWriteEvent`` and feed it back through the dispatcher. The
  dispatcher's own once-per-version watermark makes this idempotent (a concurrent live
  dispatch that already advanced a version turns the re-dispatch into a no-op), and the
  event carries ``origin=None`` — a first-level reconciliation that the depth cap counts
  from zero, exactly like the original missed write.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from msgspec import Struct

from ..entity.events import EntityWriteEvent
from ..entity.parser import ParsedEntity
from .event_dispatch import IEventWatermark, entity_key
from .triggers import EventTrigger, trigger_key, where_matches

__all__ = [
    "BackfillReport",
    "EntityLag",
    "TriggerBackfill",
    "TriggerLag",
    "backfill_trigger_lag",
    "find_trigger_lag",
]

# Resolve an entity type to the item's current parsed records of that type (the read
# side of an ``EntityStore.query``). Injected so the pure lag logic is testable without
# a filestore; the route wires it to a real store.
EntitiesOf = Callable[[str], Awaitable[list[ParsedEntity]]]

# The sink a re-synthesised event is fed back through (``EventTriggerDispatcher.dispatch``).
Dispatch = Callable[[EntityWriteEvent], Awaitable[None]]


class EntityLag(Struct):
    """One record a trigger's watermark is behind on — its number + the current version
    the trigger has not fired for."""

    number: int
    version: str


class TriggerLag(Struct):
    """One event trigger's lag: the records whose current version its watermark trails."""

    trigger_id: str
    entity: str
    on: str
    behind: list[EntityLag]


class TriggerBackfill(Struct):
    """How many missed runs a trigger's catch-up re-dispatched."""

    trigger_id: str
    fired: int


class BackfillReport(Struct):
    item_id: str
    triggers: list[TriggerBackfill]


def _behind(item_id: str, t: EventTrigger, e: ParsedEntity, watermark: IEventWatermark) -> bool:
    """Is trigger ``t``'s watermark behind entity ``e``'s current version? Not behind if
    ``where`` narrows the entity out (the trigger would never have fired for it) or the
    watermark already holds this exact version (once-per-version)."""
    if not where_matches(t.where, e.fields):
        return False
    processed = watermark.processed_version(trigger_key(t), entity_key(item_id, t.entity, e.number))
    return processed != e.version


async def find_trigger_lag(
    item_id: str,
    *,
    triggers: list[EventTrigger],
    entities_of: EntitiesOf,
    watermark: IEventWatermark,
) -> list[TriggerLag]:
    """The lag query (#429 P11). ``triggers`` is the item's app's event triggers; for each,
    report the records whose current version its watermark trails. A trigger with nothing
    behind is omitted, so an empty result means "fully caught up"."""
    out: list[TriggerLag] = []
    for t in triggers:
        behind = [
            EntityLag(number=e.number, version=e.version)
            for e in await entities_of(t.entity)
            if _behind(item_id, t, e, watermark)
        ]
        if behind:
            out.append(
                TriggerLag(trigger_id=trigger_key(t), entity=t.entity, on=t.on, behind=behind)
            )
    return out


async def backfill_trigger_lag(
    item_id: str,
    *,
    triggers: list[EventTrigger],
    entities_of: EntitiesOf,
    watermark: IEventWatermark,
    dispatch: Dispatch,
) -> BackfillReport:
    """Catch each behind trigger up (#429 P11). For every record ``find_trigger_lag`` reports
    as behind, re-synthesise its CURRENT state as an ``EntityWriteEvent`` (``action`` = the
    trigger's ``on`` so the dispatcher matches it; ``origin=None`` so the depth cap counts this
    reconciliation from zero) and feed it back through ``dispatch``. The dispatcher's
    once-per-version watermark keeps it idempotent — a version a live dispatch already advanced
    is a no-op. Returns a per-trigger count of the records re-dispatched."""
    fired: list[TriggerBackfill] = []
    for t in triggers:
        count = 0
        for e in await entities_of(t.entity):
            if not _behind(item_id, t, e, watermark):
                continue
            await dispatch(
                EntityWriteEvent(
                    item_id=item_id,
                    type_name=t.entity,
                    number=e.number,
                    action=t.on,
                    actor=t.acting_user,
                    version=e.version,
                    fields=dict(e.fields),
                    origin=None,
                )
            )
            count += 1
        if count:
            fired.append(TriggerBackfill(trigger_id=trigger_key(t), fired=count))
    return BackfillReport(item_id=item_id, triggers=fired)
