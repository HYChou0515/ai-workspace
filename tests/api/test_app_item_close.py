"""#89 P8 slice 1 — generic, lifecycle-driven close for any App's WorkItem.

`POST /a/{slug}/items/{item_id}/close` replaces the Investigation-hardcoded
`POST /a/{slug}/items/{id}/close`: it loads the App's model by slug, and a
non-null target status must be one of the manifest's `lifecycle.closing_states`
(set onto `lifecycle.status_field`); a null status is a pure teardown that
leaves the item's status untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.apps.rca.model import RcaInvestigation, Status
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.users import MockUserDirectory, User


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


def _app_and_spec():
    spec = make_spec(default_user="default-user")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
    )
    return app, spec


def _create_rca_item(client: TestClient, **fields: object) -> str:
    body: dict = {"title": "x", **fields}
    resp = client.post("/a/rca/items", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["resource_id"]


def test_close_sets_status_field_to_a_closing_state():
    app, spec = _app_and_spec()
    client = TestClient(app)
    item_id = _create_rca_item(client, title="closes ok")

    resp = client.post(f"/a/rca/items/{item_id}/close", json={"status": "resolved"})

    assert resp.status_code == 204, resp.text
    got = spec.get_resource_manager(RcaInvestigation).get(item_id).data
    assert isinstance(got, RcaInvestigation)
    assert got.status is Status.RESOLVED


def test_close_accepts_any_declared_closing_state():
    app, spec = _app_and_spec()
    client = TestClient(app)
    item_id = _create_rca_item(client, title="dead end")

    resp = client.post(f"/a/rca/items/{item_id}/close", json={"status": "abandoned"})

    assert resp.status_code == 204, resp.text
    got = spec.get_resource_manager(RcaInvestigation).get(item_id).data
    assert isinstance(got, RcaInvestigation)
    assert got.status is Status.ABANDONED


def test_pure_close_leaves_status_untouched():
    app, spec = _app_and_spec()
    client = TestClient(app)
    item_id = _create_rca_item(client, title="still open")
    rm = spec.get_resource_manager(RcaInvestigation)
    before = rm.get(item_id).data
    assert isinstance(before, RcaInvestigation)

    for payload in ({}, {"status": None}):
        resp = client.post(f"/a/rca/items/{item_id}/close", json=payload)
        assert resp.status_code == 204, resp.text
        after = rm.get(item_id).data
        assert isinstance(after, RcaInvestigation)
        assert after.status is before.status


def test_close_rejects_a_non_closing_state():
    """`triaging` is a valid status enum value but NOT a declared closing
    state, so it can't be used to close — the item stays untouched."""
    app, spec = _app_and_spec()
    client = TestClient(app)
    item_id = _create_rca_item(client, title="x")
    rm = spec.get_resource_manager(RcaInvestigation)
    before = rm.get(item_id).data
    assert isinstance(before, RcaInvestigation)

    resp = client.post(f"/a/rca/items/{item_id}/close", json={"status": "triaging"})

    assert resp.status_code in (400, 422), resp.text
    after = rm.get(item_id).data
    assert isinstance(after, RcaInvestigation)
    assert after.status is before.status


def test_close_records_activity_with_the_target_status():
    app, _ = _app_and_spec()
    client = TestClient(app)
    item_id = _create_rca_item(client, title="Voids spike")

    client.post(f"/a/rca/items/{item_id}/close", json={"status": "resolved"})

    feed = client.get("/activity").json()
    closed = next(e for e in feed if e["kind"] == "item_closed")
    assert "Voids spike" in closed["text"]
    assert "resolved" in closed["text"]


def test_pure_close_records_a_session_closed_activity():
    app, _ = _app_and_spec()
    client = TestClient(app)
    item_id = _create_rca_item(client, title="still open")

    client.post(f"/a/rca/items/{item_id}/close", json={})

    feed = client.get("/activity").json()
    assert any(e["kind"] == "session_closed" for e in feed)


def _multiuser_client(holder: dict[str, str]) -> TestClient:
    return TestClient(
        create_app(
            spec=make_spec(),
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=_Runner(),
            agent_config_catalog=AgentConfigCatalog(),
            get_user_id=lambda: holder["id"],
            users=MockUserDirectory(
                [User("alice", "Alice", "A"), User("bob", "Bob", "B"), User("carol", "Carol", "C")]
            ),
        )
    )


def test_close_notifies_owner_and_watchers_not_the_actor():
    holder = {"id": "alice"}
    client = _multiuser_client(holder)
    item_id = _create_rca_item(client, title="Reflow drift", members=["bob"])  # owner=alice (auth)

    holder["id"] = "carol"  # a third party closes it
    resp = client.post(f"/a/rca/items/{item_id}/close", json={"status": "resolved"})
    assert resp.status_code == 204

    assert client.get("/notifications").json() == []  # carol (actor) isn't notified
    holder["id"] = "alice"
    assert len(client.get("/notifications").json()) == 1  # owner
    holder["id"] = "bob"
    assert len(client.get("/notifications").json()) == 1  # watcher


def test_close_unknown_app_is_404():
    app, _ = _app_and_spec()
    resp = TestClient(app).post("/a/nope/items/whatever/close", json={"status": "resolved"})
    assert resp.status_code == 404


def test_close_unknown_item_is_404():
    app, _ = _app_and_spec()
    resp = TestClient(app).post("/a/rca/items/no-such-id/close", json={"status": "resolved"})
    assert resp.status_code == 404
