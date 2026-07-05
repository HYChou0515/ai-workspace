"""#429 P11 — operator routes for the D2d event-trigger backfill, end-to-end through
the real app. A GET reports the lag (which records a trigger's watermark trails); a
POST re-dispatches them to catch up. The scenario each test sets up is a MISSED event:
an entity written while its trigger wasn't firing (pod died mid-dispatch / the trigger
was added later) leaves the watermark behind — exactly what backfill reconciles.
"""

from __future__ import annotations

import msgspec

from workspace_app.entity.events import EntityWriteEvent
from workspace_app.workflow.triggers import EventTrigger

from .conftest import Harness

_SCHEMA = (
    b"path: issues\n"
    b"fields:\n"
    b"  title: { role: text, required: true }\n"
    b"  status: { role: status, values: [open, done] }\n"
)
_SKELETON = b"---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"


def _ship_issue_schema(harness: Harness) -> None:
    assert harness.client.put(
        harness.wpath("/files/.entity/issue/schema.yaml"), content=_SCHEMA
    ).status_code in (200, 201, 204)
    assert harness.client.put(
        harness.wpath("/files/.entity/issue/skeleton.md"), content=_SKELETON
    ).status_code in (200, 201, 204)


def _issue_trigger() -> EventTrigger:
    return EventTrigger(
        id="on_issue",
        workflow_id="triage",
        acting_user="bot",
        entity="issue",
        on="created",
        slug="rca",
        profile="default",
    )


def test_lag_route_reports_a_record_the_watermark_is_behind_on(harness, monkeypatch) -> None:
    _ship_issue_schema(harness)
    c = harness.client
    # Written while no trigger is discoverable → the create's dispatch matches nothing, so
    # the watermark stays empty: a missed event.
    assert (
        c.post(harness.wpath("/entities/issue"), json={"args": {"title": "A"}}).status_code == 200
    )
    # Now the trigger exists (added after the write) → the watermark is behind.
    monkeypatch.setattr(
        harness.client.app.state.event_dispatcher, "_triggers", lambda: [_issue_trigger()]
    )

    resp = c.get(harness.wpath("/event-triggers/lag"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["entity"] == "issue"
    assert [e["number"] for e in body[0]["behind"]] == [1]


def test_backfill_route_redispatches_the_missed_records_and_is_idempotent(
    harness, monkeypatch
) -> None:
    _ship_issue_schema(harness)
    c = harness.client
    assert (
        c.post(harness.wpath("/entities/issue"), json={"args": {"title": "A"}}).status_code == 200
    )

    disp = harness.client.app.state.event_dispatcher
    monkeypatch.setattr(disp, "_triggers", lambda: [_issue_trigger()])
    fired: list = []

    async def rec_start(t: EventTrigger, event: EntityWriteEvent, depth: int) -> str | None:
        fired.append((t.id, event.number, depth))  # record instead of starting a real run
        return "run-x"

    monkeypatch.setattr(disp, "_start", rec_start)

    resp = c.post(harness.wpath("/event-triggers/backfill"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_id"] == harness.iid
    assert body["triggers"] == [{"trigger_id": "rca:default:on_issue", "fired": 1}]
    assert fired == [("on_issue", 1, 0)]  # the missed run re-dispatched at depth 0

    # Idempotent: the first backfill advanced the watermark, so a re-run finds nothing behind.
    resp2 = c.post(harness.wpath("/event-triggers/backfill"))
    assert resp2.json()["triggers"] == []
    assert fired == [("on_issue", 1, 0)]  # no duplicate run


def test_lag_route_ignores_a_trigger_for_a_type_the_item_does_not_declare(
    harness, monkeypatch
) -> None:
    """A trigger can name an entity type this item doesn't ship — that yields no records
    (not a crash), so it simply contributes no lag."""
    _ship_issue_schema(harness)  # the item declares 'issue' only
    monkeypatch.setattr(
        harness.client.app.state.event_dispatcher,
        "_triggers",
        lambda: [msgspec.structs.replace(_issue_trigger(), entity="task")],
    )

    resp = harness.client.get(harness.wpath("/event-triggers/lag"))

    assert resp.status_code == 200, resp.text
    assert resp.json() == []
