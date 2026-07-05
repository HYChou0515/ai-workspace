"""Entity CRUD routes end-to-end (#419), through the real app + WorkspaceFiles.
The schema/skeleton are shipped into the item as ordinary workspace files, then
the entity endpoints discover, create, project, and update records."""

from __future__ import annotations

from specstar import QB

from workspace_app.resources import Notification

from .conftest import Harness

_SCHEMA = (
    b"path: issues\n"
    b"fields:\n"
    b"  title: { role: text, required: true }\n"
    b"  status: { role: status, values: [open, done] }\n"
)
_SKELETON = b"---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"


def _ship_issue_schema(harness: Harness) -> None:
    ok = harness.client.put(harness.wpath("/files/.entity/issue/schema.yaml"), content=_SCHEMA)
    assert ok.status_code in (200, 201, 204), ok.text
    ok = harness.client.put(harness.wpath("/files/.entity/issue/skeleton.md"), content=_SKELETON)
    assert ok.status_code in (200, 201, 204), ok.text


def test_entity_crud_end_to_end(harness: Harness) -> None:
    _ship_issue_schema(harness)
    c = harness.client

    types = c.get(harness.wpath("/entities")).json()
    assert [t["name"] for t in types["types"]] == ["issue"]
    assert [f["name"] for f in types["types"][0]["form"]] == ["title", "body"]

    created = c.post(harness.wpath("/entities/issue"), json={"args": {"title": "Login broken"}})
    assert created.status_code == 200, created.text
    assert created.json()["number"] == 1
    assert created.json()["fields"]["title"] == "Login broken"

    listing = c.get(harness.wpath("/entities/issue")).json()
    assert [e["number"] for e in listing["entities"]] == [1]

    updated = c.put(harness.wpath("/entities/issue/1"), json={"patch": {"status": "done"}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["fields"]["status"] == "done"


def test_update_can_replace_the_markdown_body(harness: Harness) -> None:
    """§C2 — the single-entity file editor saves the frontmatter patch AND the
    markdown body through the one update write path (not a raw file write). An
    omitted body is preserved, not wiped."""
    _ship_issue_schema(harness)
    c = harness.client
    c.post(harness.wpath("/entities/issue"), json={"args": {"title": "A"}})

    updated = c.put(
        harness.wpath("/entities/issue/1"),
        json={"patch": {"status": "done"}, "body": "## Notes\nsome detail"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["fields"]["status"] == "done"
    assert updated.json()["body"] == "## Notes\nsome detail"

    # body omitted on a later update → the stored body is preserved.
    again = c.put(harness.wpath("/entities/issue/1"), json={"patch": {"title": "B"}})
    assert again.json()["body"] == "## Notes\nsome detail"


def test_update_rejects_a_stale_version_with_409(harness: Harness) -> None:
    """§C6: the update route echoes a `version`; a PUT carrying a stale
    `expected_version` (the record moved on) is a 409, not a silent overwrite."""
    _ship_issue_schema(harness)
    c = harness.client

    created = c.post(harness.wpath("/entities/issue"), json={"args": {"title": "A"}}).json()
    stale = created["version"]
    assert stale
    # a concurrent edit (no expected_version → unconditional) bumps the version
    c.put(harness.wpath("/entities/issue/1"), json={"patch": {"status": "done"}})

    conflict = c.put(
        harness.wpath("/entities/issue/1"),
        json={"patch": {"status": "open"}, "expected_version": stale},
    )
    assert conflict.status_code == 409, conflict.text
    # the concurrent edit stands
    listing = c.get(harness.wpath("/entities/issue")).json()
    assert listing["entities"][0]["fields"]["status"] == "done"


def test_unknown_type_and_number_are_404(harness: Harness) -> None:
    _ship_issue_schema(harness)
    c = harness.client
    assert c.get(harness.wpath("/entities/nope")).status_code == 404
    assert c.put(harness.wpath("/entities/issue/99"), json={"patch": {}}).status_code == 404


def test_entity_health_lists_findings_across_the_item(harness: Harness) -> None:
    """§E3: the health route flattens parser findings. A hand-edited broken record
    (the escape hatch) surfaces as an error finding — the health view's input."""
    _ship_issue_schema(harness)
    c = harness.client
    c.post(harness.wpath("/entities/issue"), json={"args": {"title": "A"}})  # clean
    ok = c.put(harness.wpath("/files/issues/2.md"), content=b"no frontmatter here")
    assert ok.status_code in (200, 201, 204), ok.text

    health = c.get(harness.wpath("/entity_health")).json()
    seen = {(f["number"], f["level"]) for f in health["findings"]}
    assert (2, "error") in seen


def test_item_without_entity_schema_lists_no_types(harness: Harness) -> None:
    """Opt-in: an item that shipped no `.entity/` dir has an empty catalog."""
    out = harness.client.get(harness.wpath("/entities")).json()
    assert out["types"] == []


# --- P9 collaboration: activity feed (§F3) + assignment notifications (§F2) ---

_ASSIGN_SCHEMA = (
    b"path: tasks\nfields:\n  title: { role: text, required: true }\n  assignee: { role: actor }\n"
)
_ASSIGN_SKELETON = b"---\ntitle: {{arg.title}}\nassignee: {{arg.assignee?}}\n---\n"


def _notifications_for(harness: Harness, recipient: str) -> list[Notification]:
    """Every notification addressed to `recipient` — read straight off the store
    (GET /notifications is scoped to the *caller*, but assignments land in the
    assignee's box, not the assigner's)."""
    rm = harness.spec.get_resource_manager(Notification)
    out: list[Notification] = []
    for r in rm.list_resources((QB["recipient"] == recipient).build()):
        assert isinstance(r.data, Notification)
        out.append(r.data)
    return out


def _ship_task_schema(harness: Harness) -> None:
    """A schema whose `assignee` field carries the `actor` role, so an assignment
    has a person to resolve and notify."""
    ok = harness.client.put(
        harness.wpath("/files/.entity/task/schema.yaml"), content=_ASSIGN_SCHEMA
    )
    assert ok.status_code in (200, 201, 204), ok.text
    ok = harness.client.put(
        harness.wpath("/files/.entity/task/skeleton.md"), content=_ASSIGN_SKELETON
    )
    assert ok.status_code in (200, 201, 204), ok.text


def test_create_and_update_land_in_the_activity_feed(harness: Harness) -> None:
    """§F3: creating and updating an entity each leave a coarse-grained entry in
    the shared feed (the notifications popover), tagged by type + number."""
    _ship_issue_schema(harness)
    c = harness.client
    c.post(harness.wpath("/entities/issue"), json={"args": {"title": "A"}})
    c.put(harness.wpath("/entities/issue/1"), json={"patch": {"status": "done"}})

    feed = c.get("/activity").json()
    seen = {(e["kind"], e["ref"].get("type"), e["ref"].get("number")) for e in feed}
    assert ("entity_created", "issue", 1) in seen
    assert ("entity_updated", "issue", 1) in seen


def test_assigning_an_actor_field_notifies_the_person(harness: Harness) -> None:
    """§F2: setting an `actor`-role field to a resolvable handle notifies that
    person — a real @mention, delivered to *their* bell (not the assigner's)."""
    _ship_task_schema(harness)
    harness.client.post(
        harness.wpath("/entities/task"), json={"args": {"title": "Ship it", "assignee": "alice"}}
    )

    mine = _notifications_for(harness, "alice")
    assert [n.kind for n in mine] == ["entity_assignment"]
    assert mine[0].title == "You were assigned task #1"
    assert mine[0].actor == "default-user"


def test_reassignment_on_update_notifies_the_new_assignee(harness: Harness) -> None:
    """§F2: assignment fires on the update path too — moving `assignee` to a new
    handle notifies the newcomer (patch carries the field, so it's 'changed')."""
    _ship_task_schema(harness)
    c = harness.client
    c.post(harness.wpath("/entities/task"), json={"args": {"title": "T"}})  # unassigned
    c.put(harness.wpath("/entities/task/1"), json={"patch": {"assignee": "bob"}})

    assert [n.kind for n in _notifications_for(harness, "bob")] == ["entity_assignment"]


def test_self_assignment_does_not_notify(harness: Harness) -> None:
    """§F2: assigning a record to yourself is not an @mention — no bell to your
    own notifications (the current user is `default-user`, handle `you`)."""
    _ship_task_schema(harness)
    harness.client.post(
        harness.wpath("/entities/task"), json={"args": {"title": "Mine", "assignee": "you"}}
    )

    assert _notifications_for(harness, "default-user") == []


def test_clearing_or_unknown_assignee_notifies_nobody(harness: Harness) -> None:
    """§F2 lint-not-block: an explicitly-empty actor field (touched but blank) and
    an unresolvable handle both write cleanly and notify no one — no crash, no
    stray bell."""
    _ship_task_schema(harness)
    c = harness.client
    c.post(harness.wpath("/entities/task"), json={"args": {"title": "Blank", "assignee": ""}})
    c.post(harness.wpath("/entities/task"), json={"args": {"title": "Ghost", "assignee": "nobody"}})

    assert _notifications_for(harness, "nobody") == []
    assert _notifications_for(harness, "") == []
