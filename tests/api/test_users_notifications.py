"""P1 foundation — current user (/me), the directory (/users), and per-user
notifications (produced by an investigation status change)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.users import MockUserDirectory, User


def _client(holder: dict[str, str]) -> TestClient:
    """App whose 'current user' follows holder['id'] — flip it between requests
    to simulate different people acting."""
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        users=MockUserDirectory(
            [
                User("alice", "Alice Chen", "Reflow"),
                User("bob", "Bob Liu", "SMT"),
                User("carol", "Carol Kao", "Quality"),
            ]
        ),
    )
    return TestClient(app)


def test_me_resolves_the_current_user_via_the_directory():
    c = _client({"id": "alice"})
    me = c.get("/me").json()
    assert me["id"] == "alice"
    assert me["name"] == "Alice Chen"
    assert me["section"] == "Reflow"


def test_me_falls_back_to_a_placeholder_for_an_unknown_id():
    c = _client({"id": "ghost"})
    me = c.get("/me").json()
    assert me == {"id": "ghost", "name": "ghost", "section": "", "email": "", "photo_url": None}


def test_users_lists_the_directory():
    c = _client({"id": "alice"})
    names = {u["name"] for u in c.get("/users").json()}
    assert {"Alice Chen", "Bob Liu", "Carol Kao"} <= names


def _make_investigation(c: TestClient, owner: str, members: list[str]) -> str:
    return c.post(
        "/investigation",
        json={"title": "Reflow drift", "owner": owner, "members": members},
    ).json()["resource_id"]


def test_status_change_notifies_owner_and_watchers_not_the_actor():
    holder = {"id": "alice"}
    c = _client(holder)
    inv = _make_investigation(c, owner="alice", members=["bob"])

    # carol resolves it → alice (owner) + bob (watcher) get notified; carol doesn't.
    holder["id"] = "carol"
    assert c.post(f"/investigations/{inv}/close", json={"status": "resolved"}).status_code == 204

    holder["id"] = "carol"
    assert c.get("/notifications").json() == []  # the actor isn't notified

    holder["id"] = "alice"
    alice_notifs = c.get("/notifications").json()
    assert len(alice_notifs) == 1
    n = alice_notifs[0]
    assert n["kind"] == "status"
    assert n["actor"] == "carol"
    assert n["link"] == f"/investigations/{inv}"
    assert "resolved" in n["title"]
    assert n["read"] is False

    holder["id"] = "bob"
    assert len(c.get("/notifications").json()) == 1  # the watcher too


def test_mark_all_read_and_mark_one_read():
    holder = {"id": "alice"}
    c = _client(holder)
    inv = _make_investigation(c, owner="alice", members=[])
    holder["id"] = "bob"
    # bob abandons it twice-worth of state changes → give alice two notifications
    c.post(f"/investigations/{inv}/close", json={"status": "abandoned"})
    inv2 = _make_investigation(c, owner="alice", members=[])
    c.post(f"/investigations/{inv2}/close", json={"status": "resolved"})

    holder["id"] = "alice"
    notifs = c.get("/notifications").json()
    assert len(notifs) == 2 and all(not n["read"] for n in notifs)

    # mark one read
    assert c.post(f"/notifications/{notifs[0]['resource_id']}/read").status_code == 204
    after = {n["resource_id"]: n["read"] for n in c.get("/notifications").json()}
    assert after[notifs[0]["resource_id"]] is True
    assert after[notifs[1]["resource_id"]] is False

    # marking the same one again is a no-op 204 (already read branch)
    assert c.post(f"/notifications/{notifs[0]['resource_id']}/read").status_code == 204

    # mark all read (covers both unread + already-read in the loop)
    assert c.post("/notifications/read-all").status_code == 204
    assert all(n["read"] for n in c.get("/notifications").json())
    # read-all again when everything is already read (no-op branch)
    assert c.post("/notifications/read-all").status_code == 204


def test_mark_read_guards_owner_and_missing():
    holder = {"id": "alice"}
    c = _client(holder)
    inv = _make_investigation(c, owner="alice", members=[])
    holder["id"] = "bob"
    c.post(f"/investigations/{inv}/close", json={"status": "resolved"})

    holder["id"] = "alice"
    nid = c.get("/notifications").json()[0]["resource_id"]

    # not your notification → 403
    holder["id"] = "bob"
    assert c.post(f"/notifications/{nid}/read").status_code == 403

    # missing → 404
    assert c.post("/notifications/notification:nope/read").status_code == 404
