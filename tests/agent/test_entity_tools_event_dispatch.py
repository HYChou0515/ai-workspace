"""#429 P10 — agent-tool entity writes fire the event-dispatch sink, carrying the
ambient trigger origin.

The single write path (`EntityStore`) already emits a post-commit
`EntityWriteEvent` when a sink is wired; the human/UI (`entity_routes`) and
workflow-handle write paths wire it, but the agent tools did not — so an AI edit
was invisible to event triggers (`on_event` workflows). That breaks the
"single write path, all writes indistinguishable" rule on the MOST common source
of entity changes. Worse, an agent editing an entity inside a triggered run must
carry that run's `EntityOrigin(trigger, depth)` so the recursion guards
(self-trigger + depth cap) still apply — else fixing the visibility hole reopens
the cycle risk the depth cap closes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import msgspec
from agents import RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    create_entity_impl,
    link_entity_impl,
    update_entity_impl,
)
from workspace_app.entity.events import EntityOrigin, EntityWriteEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.event_dispatch import EventTriggerDispatcher, IEventWatermark
from workspace_app.workflow.triggers import EventTrigger, trigger_key

_SCHEMA = (
    b"path: issues\n"
    b"fields:\n"
    b"  title: { role: text, required: true }\n"
    b"  status: { role: status, values: [open, done] }\n"
    b"  milestone: { role: ref, to: milestone }\n"
)
_SKELETON = (
    b"---\ntitle: {{arg.title}}\nstatus: open\n"
    b"milestone: {{arg.milestone?}}\n---\n\n{{arg.body?}}\n"
)


def _recording_sink() -> tuple[
    list[EntityWriteEvent], Callable[[EntityWriteEvent], Awaitable[None]]
]:
    events: list[EntityWriteEvent] = []

    async def sink(event: EntityWriteEvent) -> None:
        events.append(event)

    return events, sink


async def _ctx(
    *,
    sink: Callable[[EntityWriteEvent], Awaitable[None]] | None = None,
    origin: EntityOrigin | None = None,
) -> RunContextWrapper:
    fs = MemoryFileStore()
    await fs.write("ws", "/.entity/issue/schema.yaml", _SCHEMA)
    await fs.write("ws", "/.entity/issue/skeleton.md", _SKELETON)
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="ws",
            filestore=fs,
            acting_user="alice",
            entity_write_sink=sink,
            entity_write_origin=origin,
        )
    )


async def test_create_entity_emits_a_created_event_to_the_wired_sink() -> None:
    events, sink = _recording_sink()
    ctx = await _ctx(sink=sink)

    await create_entity_impl(ctx, "issue", {"title": "Login broken"})

    assert len(events) == 1
    (event,) = events
    assert event.action == "created"
    assert event.type_name == "issue"
    assert event.number == 1
    assert event.actor == "alice"


async def test_update_entity_emits_an_updated_event_carrying_the_actor() -> None:
    events, sink = _recording_sink()
    ctx = await _ctx(sink=sink)
    await create_entity_impl(ctx, "issue", {"title": "A"})

    await update_entity_impl(ctx, "issue", 1, {"status": "done"})

    assert events[-1].action == "updated"
    assert events[-1].number == 1
    assert events[-1].actor == "alice"


async def test_plain_chat_write_has_no_origin_so_it_fires_as_a_first_level_write() -> None:
    """A plain user chat carries no ambient origin ⇒ the event's origin is None
    (depth 0), so an AI edit a user asked for triggers on_event workflows just like
    the user editing it by hand — the "all writes indistinguishable" rule."""
    events, sink = _recording_sink()
    ctx = await _ctx(sink=sink)  # no origin

    await create_entity_impl(ctx, "issue", {"title": "A"})

    assert events[-1].origin is None


async def test_create_inside_a_triggered_run_carries_that_runs_origin() -> None:
    """When the turn runs inside a triggered workflow run, its ambient
    EntityOrigin(trigger, depth) is stamped on the write — so the dispatcher's
    self-trigger + depth-cap guards count an agent-mediated write like any other
    run write (else fixing the visibility hole would reopen the cycle risk)."""
    events, sink = _recording_sink()
    ctx = await _ctx(sink=sink, origin=EntityOrigin(trigger="pm/on-issue", depth=2))

    await create_entity_impl(ctx, "issue", {"title": "A"})

    assert events[-1].origin == EntityOrigin(trigger="pm/on-issue", depth=2)


async def test_update_inside_a_triggered_run_carries_that_runs_origin() -> None:
    events, sink = _recording_sink()
    ctx = await _ctx(sink=sink, origin=EntityOrigin(trigger="pm/on-issue", depth=1))
    await create_entity_impl(ctx, "issue", {"title": "A"})

    await update_entity_impl(ctx, "issue", 1, {"status": "done"})

    assert events[-1].action == "updated"
    assert events[-1].origin == EntityOrigin(trigger="pm/on-issue", depth=1)


# ── the DoD regression: agent writes flow into the REAL dispatcher and the
#    recursion guards count them like any other run write ──────────────────────


class _MemWatermark(IEventWatermark):
    def __init__(self) -> None:
        self._v: dict[tuple[str, str], str] = {}

    def processed_version(self, trigger_id: str, entity_key: str) -> str:
        return self._v.get((trigger_id, entity_key), "")

    def try_advance(self, trigger_id: str, entity_key: str, version: str) -> bool:
        if self._v.get((trigger_id, entity_key)) == version:
            return False
        self._v[(trigger_id, entity_key)] = version
        return True


def _issue_trigger(**over: object) -> EventTrigger:
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


def _wired_dispatcher(fired: list, *, max_depth: int = 3) -> EventTriggerDispatcher:
    async def start(t: EventTrigger, event: EntityWriteEvent, depth: int) -> str | None:
        fired.append((t.id, event.number, depth))
        return "run-x"

    return EventTriggerDispatcher(
        triggers=lambda: [_issue_trigger()],
        app_of_item=lambda _i: "pm",
        start=start,
        watermark=_MemWatermark(),
        max_depth=max_depth,
    )


async def test_agent_write_inside_a_triggered_run_cannot_bypass_the_depth_cap() -> None:
    """The core P10 safety property: an agent editing an entity INSIDE a triggered
    run at the depth cap produces an event the dispatcher counts as over-cap, so it
    fires nothing — an agent can't be the loophole that a workflow-handle write is
    guarded against. Contrast: the same agent write from a plain chat (no origin)
    DOES fire, proving the write reaches the dispatcher and it's the propagated
    depth — not a broken wire — that stops it."""
    over_cap: list = []
    ctx = await _ctx(
        sink=_wired_dispatcher(over_cap, max_depth=3).dispatch,
        origin=EntityOrigin(trigger="pm:echo:someone_else", depth=3),  # next would be 4 > cap
    )
    await create_entity_impl(ctx, "issue", {"title": "A"})
    assert over_cap == []

    first_level: list = []
    ctx2 = await _ctx(sink=_wired_dispatcher(first_level, max_depth=3).dispatch)  # no origin
    await create_entity_impl(ctx2, "issue", {"title": "A"})
    assert first_level == [("on_issue", 1, 0)]


async def test_agent_write_does_not_refire_the_trigger_that_spawned_its_run() -> None:
    """Recursion guard 1 over the agent path: a write whose ambient origin IS the
    matching trigger's own run is skipped — the direct A→A self-loop stays broken
    even when the mutation comes from an agent tool rather than the workflow handle."""
    fired: list = []
    ctx = await _ctx(
        sink=_wired_dispatcher(fired).dispatch,
        origin=EntityOrigin(trigger=trigger_key(_issue_trigger()), depth=0),
    )
    await create_entity_impl(ctx, "issue", {"title": "A"})
    assert fired == []


async def test_link_entity_is_an_update_that_also_carries_the_ambient_origin() -> None:
    """link_entity writes a reference field through the same store.update path — so
    it must emit with the run's origin too, else the link path is a second loophole
    around the depth cap that update_entity is guarded against."""
    events, sink = _recording_sink()
    ctx = await _ctx(sink=sink, origin=EntityOrigin(trigger="pm:echo:on_issue", depth=1))
    await create_entity_impl(ctx, "issue", {"title": "A"})

    await link_entity_impl(ctx, "issue", 1, "milestone", 3)

    assert events[-1].action == "updated"
    assert events[-1].origin == EntityOrigin(trigger="pm:echo:on_issue", depth=1)
    assert events[-1].actor == "alice"
