"""My environment variables (`docs/plan-personal-env.md`) — the per-person layer.

One row per person, usable by every item whose policy asks for a personal
value (Private first / Private only). Addressed only as "mine": the routes take
no user parameter, so nobody — a superuser included — can name someone else's.
Written whole each time, with no revision kept, so a rotated token leaves
nothing readable behind.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.private_env import PersonalEnv, PrivateEnvStore, register_private_env
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

URL = "/me/env"


def _world():
    holder = {"id": "alice"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), holder, spec


def _store(now):
    spec = make_spec(default_user="x")
    register_private_env(spec)
    return PrivateEnvStore(spec, now=lambda: now["t"]), spec


def test_a_person_reads_back_what_they_stored():
    client, _, _ = _world()

    assert client.put(URL, json={"values": {"ERP_TOKEN": "a-1"}}).status_code == 200

    got = client.get(URL).json()
    assert got["values"] == {"ERP_TOKEN": "a-1"}
    assert set(got["updated"]) == {"ERP_TOKEN"}


def test_nobody_else_reads_it():
    client, holder, _ = _world()
    client.put(URL, json={"values": {"ERP_TOKEN": "a-1"}})

    holder["id"] = "bob"

    assert client.get(URL).json() == {"values": {}, "updated": {}}


def test_a_superuser_cannot_read_it_either():
    holder = {"id": "alice"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=frozenset({"root"}),
    )
    client = TestClient(app)
    client.put(URL, json={"values": {"ERP_TOKEN": "a-1"}})

    holder["id"] = "root"

    assert client.get(URL).json()["values"] == {}


def test_there_is_no_raw_resource_route_to_list_everyones_rows():
    """Registered after `spec.apply`, so specstar emits no auto-CRUD for it —
    `/me/env` is its only door. (Probing a URL would not do: the SPA fallback
    answers every unknown path with the page.)"""
    client, _, _ = _world()
    client.put(URL, json={"values": {"ERP_TOKEN": "a-1"}})

    paths = {getattr(r, "path", "") for r in client.app.routes}

    assert not any("personal-env" in p for p in paths)


def test_clearing_forgets_the_row():
    client, _, _ = _world()
    client.put(URL, json={"values": {"ERP_TOKEN": "a-1"}})

    assert client.delete(URL).status_code == 204

    assert client.get(URL).json() == {"values": {}, "updated": {}}


def test_a_rotated_credential_leaves_no_history_behind():
    now = {"t": 1}
    store, spec = _store(now)
    store.replace_personal("alice", {"ERP_TOKEN": "old"})
    store.replace_personal("alice", {"ERP_TOKEN": "new"})

    rm = spec.get_resource_manager(PersonalEnv)
    (res,) = rm.list_resources()
    rid = res.info.resource_id
    revisions = list(rm.list_revisions(rid))
    assert len(revisions) == 1


def test_stored_values_come_back_in_the_order_they_were_given():
    now = {"t": 1}
    store, _ = _store(now)
    store.replace_personal("alice", {"Z": "1", "A": "2", "M": "3"})

    assert list(store.personal("alice")) == ["Z", "A", "M"]


def test_only_a_changed_name_gets_a_new_time():
    """The page shows when each value was last set — "signed in 3 days ago"
    — so writing the whole row must not make an untouched token look new."""
    now = {"t": 100}
    store, _ = _store(now)
    store.replace_personal("alice", {"KEEP": "k", "ROTATE": "r1"})

    now["t"] = 200
    store.replace_personal("alice", {"KEEP": "k", "ROTATE": "r2", "NEW": "n"})

    assert store.personal_updated("alice") == {"KEEP": 100, "ROTATE": 200, "NEW": 200}


def test_writing_nothing_leaves_no_row():
    now = {"t": 1}
    store, spec = _store(now)
    store.replace_personal("alice", {"ERP_TOKEN": "a"})

    store.replace_personal("alice", {})

    assert spec.get_resource_manager(PersonalEnv).list_resources() == []
    assert store.personal("alice") == {}
