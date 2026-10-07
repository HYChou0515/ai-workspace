"""The schedules overview (`docs/plan-schedule-overview.md`): every schedule a
viewer can see, across items, with its last run and its next.

Real `create_app` + a real spec, for the reason the WUI overview's tests give:
the listing's whole job is a permission filter, which a double of the locator
would only pretend to apply.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

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


def test_a_page_schedule_names_its_deployed_page_so_open_can_go_there():
    """A row of a page's schedules opens that page — the Deployed view file in
    the same folder, the address the WUI overview opens. An item-level row, or
    a page never Deployed, has none: Open goes to the item."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    view = "/reports/scrap/page.ai.yaml"
    _put(client, iid, view, "view: wui\ntitle: Scrap\n")
    assert client.post(_wp(iid, "/wui/deploy"), json={"path": view}).status_code == 200

    pages = {r["path"]: r["page_path"] for r in _rows(client)}

    assert pages == {PAGE_SCHEDULES: view, ITEM_SCHEDULES: ""}


def test_next_runs_in_different_zones_are_comparable_as_one_instant():
    """`next_at` is each row's own wall clock — "09:00" in Taipei is eight hours
    before "09:00" in UTC — so the page sorts on `next_ms`, the same instant in
    epoch milliseconds."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(client, iid, "/.workflows/w1.json", _workflow("w1"))
    _put(
        client,
        iid,
        ITEM_SCHEDULES,
        _schedules(
            {"every": "daily", "at": "09:00", "run": "w0", "tz": "Asia/Taipei"},
            {"every": "daily", "at": "09:00", "run": "w1"},
        ),
    )

    rows = {r["run"]: r for r in _rows(client)}

    assert rows["w0"]["next_at"][-5:] == rows["w1"]["next_at"][-5:] == "09:00"
    gap = (rows["w1"]["next_ms"] - rows["w0"]["next_ms"]) % (24 * 3600 * 1000)
    assert gap == 8 * 3600 * 1000


# ── acting on one row ────────────────────────────────────────────────────────


def _row_of(client, path: str, run: str = "w0") -> dict:
    return next(r for r in _rows(client) if r["path"] == path and r["run"] == run)


def _file_rows(client, iid: str, path: str) -> list:
    r = client.get(_wp(iid, f"/files{path}"))
    assert r.status_code == 200, r.text
    return json.loads(r.content)["schedules"]


def test_editing_the_time_rewrites_that_row_and_keeps_the_others_as_written():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(client, iid, "/.workflows/w1.json", _workflow("w1"))
    malformed = {"every": "fortnightly", "run": "w1"}
    _put(
        client,
        iid,
        PAGE_SCHEDULES,
        _schedules(
            {"every": "daily", "at": "09:00", "run": "w0", "with": {"line": "A"}},
            malformed,
            {"every": "hourly", "run": "w1"},
        ),
    )
    before = _row_of(client, PAGE_SCHEDULES)

    r = client.post(
        _wp(iid, "/schedules/edit"),
        json={
            "path": PAGE_SCHEDULES,
            "trigger_id": before["trigger_id"],
            "every": "weekly",
            "dow": "fri",
            "at": "17:30",
            "tz": "Asia/Taipei",
        },
    )

    assert r.status_code == 200, r.text
    rows = _file_rows(client, iid, PAGE_SCHEDULES)
    assert rows[0] == {
        "every": "weekly",
        "dow": "fri",
        "at": "17:30",
        "tz": "Asia/Taipei",
        "run": "w0",
        "with": {"line": "A"},
    }
    assert rows[1:] == [malformed, {"every": "hourly", "run": "w1"}]
    after = _row_of(client, PAGE_SCHEDULES)
    assert after["trigger_id"] != before["trigger_id"]
    assert after["describe"] == "weekly on fri at 17:30 Asia/Taipei"


@pytest.mark.parametrize(
    ("asked", "written"),
    [
        # Only the fields the new `every` reads are written — a stray `dom` on a
        # weekly row is a field nothing consults and a reader has to puzzle over.
        ({"every": "minutes", "n": 15, "at": "09:00", "dom": 3}, {"every": "minutes", "n": 15}),
        ({"every": "hourly", "at": "09:00"}, {"every": "hourly"}),
        (
            {"every": "monthly", "dom": 1, "at": "06:00", "dow": "mon"},
            {"every": "monthly", "dom": 1, "at": "06:00"},
        ),
    ],
)
def test_each_period_writes_only_the_fields_it_reads(asked: dict, written: dict):
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    ref = {"path": ITEM_SCHEDULES, "trigger_id": _row_of(client, ITEM_SCHEDULES)["trigger_id"]}

    r = client.post(_wp(iid, "/schedules/edit"), json={**ref, **asked})

    assert r.status_code == 200, r.text
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [{**written, "run": "w0"}]


def test_an_invalid_time_is_refused_with_the_sweeps_reason_and_the_file_is_untouched():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    before = _file_rows(client, iid, ITEM_SCHEDULES)
    row = _row_of(client, ITEM_SCHEDULES)

    r = client.post(
        _wp(iid, "/schedules/edit"),
        json={
            "path": ITEM_SCHEDULES,
            "trigger_id": row["trigger_id"],
            "every": "daily",
            "at": "25:00",
        },
    )

    assert r.status_code == 422, r.text
    assert "25:00" in r.json()["detail"]
    assert _file_rows(client, iid, ITEM_SCHEDULES) == before


def test_acting_on_a_row_that_changed_meanwhile_is_a_conflict_not_a_guess():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    stale = _row_of(client, ITEM_SCHEDULES)["trigger_id"]
    # Somebody (the AI, the page) moves it first.
    _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "daily", "at": "07:00", "run": "w0"}))
    ref = {"path": ITEM_SCHEDULES, "trigger_id": stale}

    edit = client.post(_wp(iid, "/schedules/edit"), json={**ref, "every": "hourly"})
    remove = client.post(_wp(iid, "/schedules/remove"), json=ref)
    run = client.post(_wp(iid, "/schedules/run"), json=ref)

    assert (edit.status_code, remove.status_code, run.status_code) == (409, 409, 409)
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [
        {"every": "daily", "at": "07:00", "run": "w0"}
    ]


@pytest.mark.parametrize("now", [None, "{not json", '{"schedules": 5}'])
def test_a_file_deleted_or_broken_meanwhile_is_a_conflict(now: str | None):
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    ref = {"path": PAGE_SCHEDULES, "trigger_id": _row_of(client, PAGE_SCHEDULES)["trigger_id"]}
    if now is None:
        assert client.delete(_wp(iid, f"/files{PAGE_SCHEDULES}")).status_code in (200, 204)
    else:
        _put(client, iid, PAGE_SCHEDULES, now)

    assert client.post(_wp(iid, "/schedules/remove"), json=ref).status_code == 409


def test_only_a_schedules_file_can_be_acted_on():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)

    r = client.post(
        _wp(iid, "/schedules/remove"), json={"path": "/reports/scrap/data.json", "trigger_id": "x"}
    )

    assert r.status_code == 400


def test_a_reader_may_neither_change_nor_run_a_schedule():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=READER)
    _seed(client, iid)
    ref = {"path": ITEM_SCHEDULES, "trigger_id": _row_of(client, ITEM_SCHEDULES)["trigger_id"]}
    holder["id"] = "alice"

    edit = client.post(_wp(iid, "/schedules/edit"), json={**ref, "every": "hourly"})
    remove = client.post(_wp(iid, "/schedules/remove"), json=ref)
    run = client.post(_wp(iid, "/schedules/run"), json=ref)

    assert (edit.status_code, remove.status_code, run.status_code) == (403, 403, 403)


def test_remove_drops_that_row_and_keeps_the_others_as_written():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(client, iid, "/.workflows/w1.json", _workflow("w1"))
    malformed = {"every": "fortnightly", "run": "w1"}
    keep = {"every": "hourly", "run": "w1"}
    _put(
        client,
        iid,
        PAGE_SCHEDULES,
        _schedules({"every": "daily", "at": "09:00", "run": "w0"}, malformed, keep),
    )
    ref = {"path": PAGE_SCHEDULES, "trigger_id": _row_of(client, PAGE_SCHEDULES)["trigger_id"]}

    r = client.post(_wp(iid, "/schedules/remove"), json=ref)

    assert r.status_code == 204, r.text
    assert _file_rows(client, iid, PAGE_SCHEDULES) == [malformed, keep]


def _wake(client, iid: str) -> None:
    woke = client.post(_wp(iid, "/exec"), json={"cmd": ["echo", "hi"]})
    assert woke.status_code == 200, woke.text


def test_run_now_runs_the_schedule_in_its_own_chat_as_the_presser_and_keeps_its_time():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    with client:
        _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
        _put(
            client,
            iid,
            ITEM_SCHEDULES,
            _schedules({"every": "daily", "at": "09:00", "run": "w0", "with": {"line": "A"}}),
        )
        _wake(client, iid)
        before = _row_of(client, ITEM_SCHEDULES)

        r = client.post(
            _wp(iid, "/schedules/run"),
            json={"path": ITEM_SCHEDULES, "trigger_id": before["trigger_id"]},
        )
        (run,) = client.get(_wp(iid, "/runs")).json()
        after = _row_of(client, ITEM_SCHEDULES)

    assert r.status_code == 202, r.text
    assert r.json()["run_id"] == run["run_id"]
    assert (run["chat_id"], run["captured_user"], run["trigger_payload"]) == (
        before["trigger_id"],
        "bob",
        {"line": "A"},
    )
    assert after["last_run"]["run_id"] == run["run_id"]
    assert (after["next_at"], after["due_now"]) == (before["next_at"], before["due_now"])


def test_run_now_is_refused_while_the_previous_run_is_still_going():
    gated = json.dumps(
        {
            "id": "ignored",
            "title": "Gated",
            "phases": [{"id": "p"}],
            "steps": [
                {"type": "gate", "phase": "p", "title": "go on?"},
                {"type": "sandbox", "run": "echo ok", "phase": "p", "cache": False},
            ],
        }
    )
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    with client:
        _put(client, iid, "/.workflows/w0.json", gated)
        _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))
        _wake(client, iid)
        ref = {"path": ITEM_SCHEDULES, "trigger_id": _row_of(client, ITEM_SCHEDULES)["trigger_id"]}

        first = client.post(_wp(iid, "/schedules/run"), json=ref)
        second = client.post(_wp(iid, "/schedules/run"), json=ref)

    assert first.status_code == 202, first.text
    assert second.status_code == 409, second.text
    assert "still going" in second.json()["detail"]


def test_run_now_names_a_workflow_the_item_lacks_or_that_will_not_parse():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(
        client,
        iid,
        "/.workflows/bad.json",
        '{"id":"x","phases":[{"id":"p"}],"steps":[{"type":"agent","prompt":"hi","phase":"p"}]}',
    )
    _put(
        client,
        iid,
        ITEM_SCHEDULES,
        _schedules({"every": "hourly", "run": "gone"}, {"every": "hourly", "run": "bad"}),
    )
    rows = {r["run"]: r for r in _rows(client)}

    missing = client.post(
        _wp(iid, "/schedules/run"),
        json={"path": ITEM_SCHEDULES, "trigger_id": rows["gone"]["trigger_id"]},
    )
    broken = client.post(
        _wp(iid, "/schedules/run"),
        json={"path": ITEM_SCHEDULES, "trigger_id": rows["bad"]["trigger_id"]},
    )

    assert missing.status_code == 403 and "gone" in missing.json()["detail"]
    assert broken.status_code == 422 and "`cache` is required" in broken.json()["detail"]


def test_a_file_the_index_still_names_but_that_is_gone_is_skipped():
    """The index may name a deleted file (it is stale in that direction only);
    the listing skips it rather than failing the page."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    assert client.delete(_wp(iid, f"/files{PAGE_SCHEDULES}")).status_code in (200, 204)

    assert [r["path"] for r in _rows(client)] == [ITEM_SCHEDULES]
