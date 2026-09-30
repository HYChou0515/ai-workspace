"""Viewer login (`docs/plan-wui-viewer-login.md` Q7/Q8/Q12) — "run as me".

A page's schedules run with nobody's private values until a person lends
theirs. These are the routes the page's platform bar calls: list the page's
schedules with who each runs as, bind one to the caller, and unbind one's own.
"""

from __future__ import annotations

import json

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

PAGE = "/report"
FILE = f"{PAGE}/schedules.json"
DAILY = {"every": "daily", "at": "09:00", "run": "build-report"}


def _world():
    """bob owns the item; carol is a Collaborator (holds `execute`); alice is a
    Participant (reads, talks to the agent, runs nothing)."""
    holder = {"id": "bob"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    reader = ["user:alice", "user:carol"]
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        rid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(
                    visibility="restricted",
                    read_meta=reader,
                    read_chat=reader,
                    read_content=reader,
                    converse=reader,
                    add_content=["user:carol"],
                    edit_content=["user:carol"],
                    execute=["user:carol"],
                ),
            )
        ).resource_id
    client = TestClient(app)
    put = client.put(f"/a/rca/items/{rid}/files{FILE}", content=json.dumps({"schedules": [DAILY]}))
    assert put.status_code == 204, put.text
    return client, holder, rid, spec


def _list(client, rid):
    r = client.get(f"/a/rca/items/{rid}/schedule-bindings", params={"path": FILE})
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def _bind(client, rid, key):
    return client.put(f"/a/rca/items/{rid}/schedule-bindings/{key}", params={"path": FILE})


def test_a_pages_schedules_are_listed_bound_to_nobody_at_first():
    client, _holder, rid, _ = _world()

    (row,) = _list(client, rid)

    assert row["run"] == "build-report"
    assert row["bound_to"] == ""
    assert row["mine"] is False
    assert row["trigger_id"]


def test_a_collaborator_binds_a_schedule_to_themself():
    client, holder, rid, _ = _world()
    holder["id"] = "carol"
    (row,) = _list(client, rid)

    r = _bind(client, rid, row["trigger_id"])

    assert r.status_code == 200, r.text
    (after,) = _list(client, rid)
    assert (after["bound_to"], after["mine"]) == ("carol", True)


def test_the_sweep_finds_the_binding_the_route_made():
    """The route and the sweep must compute the SAME key for a row, or a binding
    is made that no fire ever looks up — "run as me" that silently does nothing.
    The sweep's own key rule (`trigger_id_for(item, folder-of-the-file, row)`)
    is the oracle."""
    from workspace_app.workflow.schedule_bindings import ScheduleBindings
    from workspace_app.workflow.user_schedules import trigger_id_for, usable_rows

    client, holder, rid, spec = _world()
    holder["id"] = "carol"
    (row,) = _list(client, rid)
    _bind(client, rid, row["trigger_id"])

    (parsed,), _ = usable_rows(json.dumps({"schedules": [DAILY]}))
    assert ScheduleBindings(spec).binder(trigger_id_for(rid, PAGE, parsed)) == "carol"


def test_someone_who_cannot_run_work_here_cannot_bind():
    """`execute` — the verb that starts a run from this page (`wui/run`). A
    Participant may talk to the agent but not make the item run things."""
    client, holder, rid, _ = _world()
    holder["id"] = "alice"
    (row,) = _list(client, rid)

    assert _bind(client, rid, row["trigger_id"]).status_code == 403


def test_replacing_a_binding_tells_the_person_replaced():
    """Q8: one person per schedule. Taking it over is allowed; the one who lent
    their values finds out from a notice, not from results that changed."""
    from workspace_app.api.notifications import Notification

    client, holder, rid, spec = _world()
    holder["id"] = "carol"
    (row,) = _list(client, rid)
    _bind(client, rid, row["trigger_id"])

    holder["id"] = "bob"
    assert _bind(client, rid, row["trigger_id"]).status_code == 200

    notes = [
        r.data
        for r in spec.get_resource_manager(Notification).list_resources()
        if r.data.recipient == "carol"
    ]
    assert [n.kind for n in notes] == ["schedule_binding_replaced"]


def test_a_schedule_that_changed_since_the_listing_cannot_be_bound():
    client, holder, rid, _ = _world()
    holder["id"] = "carol"

    assert _bind(client, rid, "no-such-key").status_code == 404


def test_only_the_binder_takes_their_own_name_off():
    client, holder, rid, _ = _world()
    holder["id"] = "carol"
    (row,) = _list(client, rid)
    _bind(client, rid, row["trigger_id"])
    url = f"/a/rca/items/{rid}/schedule-bindings/{row['trigger_id']}"

    holder["id"] = "bob"
    assert client.delete(url, params={"path": FILE}).status_code == 403

    holder["id"] = "carol"
    assert client.delete(url, params={"path": FILE}).status_code == 204
    (after,) = _list(client, rid)
    assert after["bound_to"] == ""


def test_the_path_must_name_a_schedules_file():
    client, _holder, rid, _ = _world()

    r = client.get(f"/a/rca/items/{rid}/schedule-bindings", params={"path": "/report/index.html"})

    assert r.status_code == 400
