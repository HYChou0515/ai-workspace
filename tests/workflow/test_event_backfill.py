"""#429 P11 — the D2d on-demand backfill: turn the event-dispatch watermark's
"discoverable lag" into an operator query + catch-up.

P9's dispatch is in-request, one-shot (no queue). A pod dying between an entity
commit and the dispatch leaves the trigger's processing watermark BEHIND the
entity's current version — a missed run, silently. The watermark ledger already
records that lag; P11 exposes it: ``find_trigger_lag`` reports which
(trigger, entity) pairs are behind, and ``backfill_trigger_lag`` re-dispatches
their current state to catch them up (idempotent — the watermark's once-per-version
gate means an already-processed version is a no-op). "Can back-fill, doesn't
proactively" — an operator decides when.
"""

from __future__ import annotations

import msgspec

from workspace_app.entity.events import EntityWriteEvent
from workspace_app.entity.parser import ParsedEntity
from workspace_app.workflow.event_backfill import (
    TriggerBackfill,
    backfill_trigger_lag,
    find_trigger_lag,
)
from workspace_app.workflow.event_dispatch import IEventWatermark, entity_key
from workspace_app.workflow.triggers import EventTrigger, trigger_key


class FakeWatermark(IEventWatermark):
    def __init__(self) -> None:
        self._v: dict[tuple[str, str], str] = {}

    def processed_version(self, trigger_id: str, entity_key: str) -> str:
        return self._v.get((trigger_id, entity_key), "")

    def try_advance(self, trigger_id: str, entity_key: str, version: str) -> bool:
        if self._v.get((trigger_id, entity_key)) == version:
            return False
        self._v[(trigger_id, entity_key)] = version
        return True


def _entity(number: int, version: str, **fields: object) -> ParsedEntity:
    return ParsedEntity(
        number=number, type_name="issue", fields=fields, body="", diagnostics=[], version=version
    )


def _trigger(**over: object) -> EventTrigger:
    base = EventTrigger(
        id="on_issue",
        workflow_id="triage",
        acting_user="bot",
        entity="issue",
        on="created",
        slug="pm",
        profile="echo",
    )
    return msgspec.structs.replace(base, **over)


def _entities_of(entities: list[ParsedEntity]):
    async def go(type_name: str) -> list[ParsedEntity]:
        return [e for e in entities if e.type_name == type_name]

    return go


async def test_find_lag_reports_entities_whose_version_is_ahead_of_the_watermark() -> None:
    t = _trigger()
    entities = [_entity(1, "v1", status="open"), _entity(2, "v2", status="open")]
    wm = FakeWatermark()
    wm.try_advance(trigger_key(t), entity_key("pm/i1", "issue", 1), "v1")  # #1 already handled

    lag = await find_trigger_lag(
        "pm/i1", triggers=[t], entities_of=_entities_of(entities), watermark=wm
    )

    assert len(lag) == 1
    assert lag[0].trigger_id == trigger_key(t)
    assert [e.number for e in lag[0].behind] == [2]  # only the un-processed one


async def test_find_lag_respects_where_so_a_narrowed_out_entity_is_not_behind() -> None:
    """An entity the trigger's ``where`` would never fire for is not "behind" — else an
    operator would chase phantom lag the live path was right to skip."""
    t = _trigger(where={"status": "closed"})
    entities = [_entity(1, "v1", status="open")]  # unprocessed, but where wants closed

    lag = await find_trigger_lag(
        "pm/i1", triggers=[t], entities_of=_entities_of(entities), watermark=FakeWatermark()
    )

    assert lag == []


async def test_find_lag_is_empty_when_the_watermark_is_caught_up() -> None:
    t = _trigger()
    entities = [_entity(1, "v1", status="open")]
    wm = FakeWatermark()
    wm.try_advance(trigger_key(t), entity_key("pm/i1", "issue", 1), "v1")

    lag = await find_trigger_lag(
        "pm/i1", triggers=[t], entities_of=_entities_of(entities), watermark=wm
    )

    assert lag == []


async def test_backfill_redispatches_the_behind_entitys_current_state() -> None:
    """Catch-up: a behind record is re-synthesised as a CURRENT-state event and fed back
    through the dispatch sink — action == the trigger's ``on`` (so it matches), the live
    version + fields, and ``origin=None`` (a first-level reconciliation)."""
    t = _trigger()
    entities = [_entity(1, "v1", status="open"), _entity(2, "v2", status="done")]
    wm = FakeWatermark()
    wm.try_advance(trigger_key(t), entity_key("pm/i1", "issue", 1), "v1")  # #1 done, #2 behind

    dispatched: list[EntityWriteEvent] = []

    async def dispatch(event: EntityWriteEvent) -> None:
        dispatched.append(event)

    report = await backfill_trigger_lag(
        "pm/i1", triggers=[t], entities_of=_entities_of(entities), watermark=wm, dispatch=dispatch
    )

    assert len(dispatched) == 1
    (ev,) = dispatched
    assert (ev.item_id, ev.type_name, ev.number, ev.version) == ("pm/i1", "issue", 2, "v2")
    assert ev.action == "created"  # == t.on, so the dispatcher matches this trigger
    assert ev.origin is None  # first-level reconciliation — depth counts from zero
    assert ev.fields == {"status": "done"}  # the entity's CURRENT state
    assert report.item_id == "pm/i1"
    assert report.triggers == [TriggerBackfill(trigger_id=trigger_key(t), fired=1)]


async def test_backfill_through_the_real_dispatcher_fires_once_then_is_a_noop() -> None:
    """The idempotency guarantee, end-to-end: feeding the re-synthesised event through the
    REAL dispatcher fires the missed run AND advances its watermark, so a second backfill
    finds nothing behind — the operator can re-run it safely without duplicate runs."""
    from workspace_app.workflow.event_dispatch import EventTriggerDispatcher

    t = _trigger()
    entities = [_entity(5, "v1", status="open")]  # never processed → behind
    wm = FakeWatermark()
    fired: list = []

    async def start(trig: EventTrigger, event: EntityWriteEvent, depth: int) -> str | None:
        fired.append((trig.id, event.number, depth))
        return "run-x"

    dispatcher = EventTriggerDispatcher(
        triggers=lambda: [t], app_of_item=lambda _i: "pm", start=start, watermark=wm
    )
    entities_of = _entities_of(entities)

    r1 = await backfill_trigger_lag(
        "pm/i1",
        triggers=[t],
        entities_of=entities_of,
        watermark=wm,
        dispatch=dispatcher.dispatch,
    )
    assert fired == [("on_issue", 5, 0)]  # the missed run fired at depth 0
    assert r1.triggers == [TriggerBackfill(trigger_id=trigger_key(t), fired=1)]

    r2 = await backfill_trigger_lag(
        "pm/i1",
        triggers=[t],
        entities_of=entities_of,
        watermark=wm,
        dispatch=dispatcher.dispatch,
    )
    assert fired == [("on_issue", 5, 0)]  # watermark caught up → no duplicate fire
    assert r2.triggers == []
