"""Entity CRUD routes end-to-end (#419), through the real app + WorkspaceFiles.
The schema/skeleton are shipped into the item as ordinary workspace files, then
the entity endpoints discover, create, project, and update records."""

from __future__ import annotations

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


def test_item_without_entity_schema_lists_no_types(harness: Harness) -> None:
    """Opt-in: an item that shipped no `.entity/` dir has an empty catalog."""
    out = harness.client.get(harness.wpath("/entities")).json()
    assert out["types"] == []
