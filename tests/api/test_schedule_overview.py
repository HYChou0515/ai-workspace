"""The schedules overview (`docs/plan-schedule-overview.md`): every schedule a
viewer can see, across items, with its last run and its next.

Real `create_app` + a real spec, for the reason the WUI overview's tests give:
the listing's whole job is a permission filter, which a double of the locator
would only pretend to apply.
"""

from __future__ import annotations

import json
from datetime import timedelta

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

READER = Permission(visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"])

PAGE_SCHEDULES = "/reports/scrap/schedules.json"
ITEM_SCHEDULES = "/.workflows/schedules.json"


def _workflow(title: str) -> str:
    return json.dumps(
        {
            "id": "ignored",
            "title": title,
            "phases": [{"id": "p"}],
            "steps": [{"type": "sandbox", "run": "echo ok", "phase": "p", "cache": False}],
        }
    )


def _client_and_spec(holder: dict[str, str], *, superusers=frozenset()):
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
        trigger_check_interval=timedelta(hours=1),
    )
    return TestClient(app), spec, app


def _item(spec, *, by: str, title: str = "Line 3", permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title=title, owner=by, permission=permission)).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _put(client, iid: str, path: str, body: str) -> None:
    r = client.put(_wp(iid, f"/files{path}"), content=body.encode())
    assert r.status_code == 204, r.text


def _schedules(*rows: dict) -> str:
    return json.dumps({"schedules": list(rows)})


def _seed(client, iid: str) -> None:
    """One workflow, one item-level schedule, one page-level schedule."""
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "daily", "at": "09:00", "run": "w0"}))
    _put(
        client,
        iid,
        PAGE_SCHEDULES,
        _schedules({"every": "weekly", "dow": "mon", "at": "08:00", "run": "w0"}),
    )


def _rows(client) -> list[dict]:
    r = client.get("/schedules")
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def test_the_overview_lists_item_and_page_schedules_with_where_they_live():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)

    rows = _rows(client)

    assert sorted((r["item_id"], r["path"], r["run"], r["item_title"]) for r in rows) == sorted(
        [(iid, ITEM_SCHEDULES, "w0", "Line 3"), (iid, PAGE_SCHEDULES, "w0", "Line 3")]
    )
    assert all(r["slug"] == "rca" and r["runnable"] for r in rows)


def test_the_overview_shows_a_viewer_only_the_items_they_may_read():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    shared = _item(spec, by="bob", permission=READER)
    private = _item(spec, by="bob", permission=Permission(visibility="private"))
    _seed(client, shared)
    _seed(client, private)

    def seen() -> list[tuple[str, bool, bool]]:
        return sorted({(r["item_id"], r["can_edit"], r["can_run"]) for r in _rows(client)})

    # The owner: both, and may act on both.
    assert seen() == sorted([(shared, True, True), (private, True, True)])
    # A reader of one: that one, and may neither change nor run it.
    holder["id"] = "alice"
    assert seen() == [(shared, False, False)]
    # A stranger: nothing — and no hint that anything exists.
    holder["id"] = "carol"
    assert seen() == []
    # A superuser: everything.
    holder["id"] = "root"
    assert seen() == sorted([(shared, True, True), (private, True, True)])


def _fire(client, app, iid: str) -> None:
    """One sweep tick, the sandbox woken first (as the parity test does, for
    the reason it gives), with every file stamped as landed long ago so the
    rows are due now — the birth rule has its own test below."""
    from workspace_app.api.schedule_index import ScheduleIndex

    index = ScheduleIndex(app.state.spec)
    for path in index.paths(iid):
        index.stamp(iid, path, 0)
    woke = client.post(_wp(iid, "/exec"), json={"cmd": ["echo", "hi"]})
    assert woke.status_code == 200, woke.text
    assert client.portal is not None
    client.portal.call(app.state.user_schedule_sweeper.tick)


def test_last_run_is_none_before_the_first_run_and_the_run_after_it():
    holder = {"id": "bob"}
    client, spec, app = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    with client:
        _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
        _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))
        assert [r["last_run"] for r in _rows(client)] == [None]

        _fire(client, app, iid)
        runs = client.get(_wp(iid, "/runs")).json()
        (row,) = _rows(client)

    assert len(runs) == 1
    assert row["last_run"]["run_id"] == runs[0]["run_id"]
    assert row["last_run"]["status"] in {"pending", "running", "done"}
    assert row["trigger_id"].startswith("wui:")


def test_a_page_schedule_that_has_run_is_not_shown_as_due_again():
    """The ledger is keyed by the FILE's folder: asked under the item's own
    path, a page's row would read as never fired and promise another run."""
    holder = {"id": "bob"}
    client, spec, app = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    with client:
        _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
        _put(client, iid, PAGE_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))
        _fire(client, app, iid)
        (row,) = _rows(client)

    assert row["path"] == PAGE_SCHEDULES
    assert row["due_now"] is False
    assert row["next_at"]


def test_a_schedule_written_after_its_moment_is_not_shown_as_due_now():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))

    (row,) = _rows(client)

    assert row["runnable"] is True
    assert row["due_now"] is False


def test_a_file_that_does_not_parse_is_listed_with_its_problem():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, PAGE_SCHEDULES, "{not json")

    body = client.get("/schedules").json()

    assert body["rows"] == []
    assert [(f["item_id"], f["path"]) for f in body["files"]] == [(iid, PAGE_SCHEDULES)]
    assert body["files"][0]["problems"]


def test_a_schedule_chat_with_no_run_linked_or_a_run_since_deleted_reads_as_never_run():
    """`chat_for_schedule` makes the chat before the run starts, and a run row
    can be pruned; neither is a run to show."""
    from workspace_app.api.schedule_listing import last_run_of
    from workspace_app.resources import Conversation

    spec = make_spec()
    rm = spec.get_resource_manager(Conversation)
    rm.create(Conversation(item_id="i1", run_id=""), resource_id="wui:i1:aaaa")
    rm.create(Conversation(item_id="i1", run_id="gone"), resource_id="wui:i1:bbbb")

    assert last_run_of(spec, "wui:i1:aaaa") is None
    assert last_run_of(spec, "wui:i1:bbbb") is None
    assert last_run_of(spec, "wui:i1:never") is None


def test_a_file_the_index_still_names_but_that_is_gone_is_skipped():
    """The index may name a deleted file (it is stale in that direction only);
    the listing skips it rather than failing the page."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    assert client.delete(_wp(iid, f"/files{PAGE_SCHEDULES}")).status_code in (200, 204)

    assert [r["path"] for r in _rows(client)] == [ITEM_SCHEDULES]
