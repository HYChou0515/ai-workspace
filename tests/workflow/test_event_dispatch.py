"""#429 P9: entity-write event dispatch — matching, where-filter, the recursion guard
(self-trigger skip + global depth cap), and once-per-version delivery (D2d watermark)."""

from __future__ import annotations

from typing import Any

import msgspec
from specstar import SpecStar

from workspace_app.workflow.event_dispatch import (
    EntityOrigin,
    EntityWriteEvent,
    EventTriggerDispatcher,
    IEventWatermark,
    SpecstarEventWatermark,
    register_event_watermark,
)
from workspace_app.workflow.triggers import EventTrigger


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


_TRIGGER_BASE = EventTrigger(
    id="on_issue", workflow_id="triage", acting_user="bot", entity="issue", on="created",
    slug="rca", profile="echo",
)
_EVENT_BASE = EntityWriteEvent(
    item_id="rca/i1", type_name="issue", number=5, action="created", actor="alice",
    version="v1", fields={"status": "open"},
)


def _event_trigger(**over: Any) -> EventTrigger:
    return msgspec.structs.replace(_TRIGGER_BASE, **over)


def _event(**over: Any) -> EntityWriteEvent:
    return msgspec.structs.replace(_EVENT_BASE, **over)


async def _noop_start(t: EventTrigger, event: EntityWriteEvent, depth: int) -> str | None:
    return None


def _dispatcher(triggers, fired, *, watermark=None, app="rca", max_depth=3):
    async def start(t, event, depth):
        fired.append((t.id, event.number, depth))
        return "run-x"

    return EventTriggerDispatcher(
        triggers=lambda: triggers,
        app_of_item=lambda item_id: app,
        start=start,
        watermark=watermark or FakeWatermark(),
        max_depth=max_depth,
    )


async def test_a_matching_create_fires_the_trigger_at_depth_zero():
    """A human-caused create matching entity + on + where + app fires the trigger at depth 0
    (the first level of the chain)."""
    fired: list = []
    await _dispatcher([_event_trigger()], fired).dispatch(_event())
    assert fired == [("on_issue", 5, 0)]


async def test_non_matching_app_entity_or_action_do_not_fire():
    fired: list = []
    d_wrong_app = _dispatcher([_event_trigger()], fired, app="other")
    await d_wrong_app.dispatch(_event())
    await _dispatcher([_event_trigger(entity="task")], fired).dispatch(_event())
    await _dispatcher([_event_trigger(on="updated")], fired).dispatch(_event())
    assert fired == []


async def test_where_filter_narrows_out_a_trivial_change():
    """A where clause that the record doesn't satisfy stops the trigger from firing."""
    fired: list = []
    trig = _event_trigger(where={"status": "closed"})
    await _dispatcher([trig], fired).dispatch(_event(fields={"status": "open"}))
    assert fired == []


async def test_guard_1_a_run_does_not_refire_its_own_trigger():
    """Recursion guard 1: a write whose origin is THIS trigger's own run is skipped — the direct
    A→A self-loop is broken."""
    fired: list = []
    trig = _event_trigger()
    origin = EntityOrigin(trigger="rca:echo:on_issue", depth=0)  # == trigger_key(trig)
    await _dispatcher([trig], fired).dispatch(_event(origin=origin))
    assert fired == []


async def test_a_different_trigger_still_fires_within_the_depth_cap():
    """A run spawned by trigger A whose write matches a DIFFERENT trigger B still fires B (a
    legitimate chain) — at the incremented depth."""
    fired: list = []
    trig_b = _event_trigger(id="on_issue_b", workflow_id="notify")
    origin = EntityOrigin(trigger="rca:echo:on_issue_a", depth=0)  # a different trigger
    await _dispatcher([trig_b], fired).dispatch(_event(origin=origin))
    assert fired == [("on_issue_b", 5, 1)]  # depth incremented to 1


async def test_guard_2_the_depth_cap_stops_an_indirect_cycle():
    """Recursion guard 2: once the chain would exceed the depth cap, NOTHING fires — the
    backstop for indirect cycles A→B→A that the self-trigger marker can't catch."""
    fired: list = []
    origin = EntityOrigin(trigger="rca:echo:someone_else", depth=3)  # next would be 4 > cap 3
    await _dispatcher([_event_trigger()], fired, max_depth=3).dispatch(_event(origin=origin))
    assert fired == []


async def test_once_per_version_delivery_is_idempotent():
    """D2d: dispatching the same entity version twice fires once; a NEW version fires again."""
    fired: list = []
    wm = FakeWatermark()
    d = _dispatcher([_event_trigger()], fired, watermark=wm)
    await d.dispatch(_event(version="v1"))
    await d.dispatch(_event(version="v1"))  # same version → idempotent no-op
    await d.dispatch(_event(version="v2", action="created"))  # a new change → fires again
    assert fired == [("on_issue", 5, 0), ("on_issue", 5, 0)]


async def test_one_bad_trigger_does_not_sink_the_others():
    """A trigger whose start raises is swallowed (a committed write never becomes a 500) and the
    remaining triggers still fire."""
    fired: list = []

    async def start(t, event, depth):
        if t.id == "boom":
            raise RuntimeError("kaboom")
        fired.append(t.id)
        return "run"

    d = EventTriggerDispatcher(
        triggers=lambda: [_event_trigger(id="boom"), _event_trigger(id="ok")],
        app_of_item=lambda _i: "rca",
        start=start,
        watermark=FakeWatermark(),
    )
    await d.dispatch(_event())
    assert fired == ["ok"]


async def test_unresolvable_app_fires_nothing():
    fired: list = []
    d = EventTriggerDispatcher(
        triggers=lambda: [_event_trigger()],
        app_of_item=lambda _i: None,  # item's app can't be resolved
        start=_noop_start,
        watermark=FakeWatermark(),
    )
    await d.dispatch(_event())
    assert fired == []


async def test_entity_store_write_flows_into_the_dispatcher():
    """The whole seam: an ``EntityStore`` create with the dispatcher wired as its ``on_write``
    fires a matching event trigger — a human's UI/API create → a workflow run (#429 P9)."""
    from workspace_app.entity.catalog import EntityCatalog, EntityType
    from workspace_app.entity.schema import EntitySchema, FieldSpec, Role
    from workspace_app.entity.store import EntityStore
    from workspace_app.filestore.memory import MemoryFileStore

    fired: list = []

    async def start(t, ev, depth):
        fired.append((t.id, ev.number, ev.actor, ev.action))
        return None

    dispatcher = EventTriggerDispatcher(
        triggers=lambda: [_event_trigger(entity="issue", on="created")],
        app_of_item=lambda _i: "rca",
        start=start,
        watermark=FakeWatermark(),
    )
    schema = EntitySchema(fields=[FieldSpec(name="title", role=Role.TEXT, required=True)])
    catalog = EntityCatalog(
        {
            "issue": EntityType(
                name="issue",
                schema=schema,
                skeleton="---\ntitle: {{arg.title}}\n---\n",
                records_path="issues",
            )
        }
    )
    store = EntityStore(MemoryFileStore(), "rca/i1", catalog, on_write=dispatcher.dispatch)
    await store.create("issue", {"title": "X"}, actor="alice")
    assert fired == [("on_issue", 1, "alice", "created")]


# ── specstar-backed watermark ────────────────────────────────────────────────


def test_specstar_watermark_advances_once_per_version(spec_instance: SpecStar):
    register_event_watermark(spec_instance)
    wm = SpecstarEventWatermark(spec_instance)
    assert wm.processed_version("t", "rca/i1:issue:5") == ""
    assert wm.try_advance("t", "rca/i1:issue:5", "v1") is True  # first version → fire
    assert wm.processed_version("t", "rca/i1:issue:5") == "v1"
    assert wm.try_advance("t", "rca/i1:issue:5", "v1") is False  # same version → no re-fire
    assert wm.try_advance("t", "rca/i1:issue:5", "v2") is True  # new version → fire
    assert wm.processed_version("t", "rca/i1:issue:5") == "v2"


def test_register_event_watermark_is_idempotent(spec_instance: SpecStar):
    register_event_watermark(spec_instance)
    register_event_watermark(spec_instance)


def test_specstar_watermark_handles_slashed_item_ids(spec_instance: SpecStar):
    """The entity key holds the item id, which contains '/', so the row must be keyed by a
    slash-free hash — not the raw composite (which specstar would reject)."""
    register_event_watermark(spec_instance)
    wm = SpecstarEventWatermark(spec_instance)
    assert wm.try_advance("rca:echo:t", "rca/deep/i1:issue:9", "v1") is True
    assert wm.processed_version("rca:echo:t", "rca/deep/i1:issue:9") == "v1"
