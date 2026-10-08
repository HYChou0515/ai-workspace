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
        # A cron is the whole "when" (docs/plan-schedule-cron.md decision 4):
        # the `every` row's fields go, the zone stays.
        (
            {"cron": "0 9 * * 1-5", "at": "09:00", "tz": "Asia/Taipei"},
            {"cron": "0 9 * * 1-5", "tz": "Asia/Taipei"},
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
    # The presser is NOT the owner (review round 1): a fire runs as the owner,
    # so a test where the two are one person cannot tell them apart.
    runner = Permission(visibility="restricted", read_meta=["user:alice"], execute=["user:alice"])
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=runner)
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

        holder["id"] = "alice"
        r = client.post(
            _wp(iid, "/schedules/run"),
            json={"path": ITEM_SCHEDULES, "trigger_id": before["trigger_id"]},
        )
        holder["id"] = "bob"
        (run,) = client.get(_wp(iid, "/runs")).json()
        after = _row_of(client, ITEM_SCHEDULES)

    assert r.status_code == 202, r.text
    assert r.json()["run_id"] == run["run_id"]
    assert (run["chat_id"], run["captured_user"], run["trigger_payload"]) == (
        before["trigger_id"],
        "alice",
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


def test_a_row_the_sweep_refuses_can_still_be_removed_by_what_it_says():
    """A malformed row has no identity — it never fires — but it can still be
    removed: found by its value as written (order inside a list matters, the
    order of an object's keys does not)."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    malformed = {"every": "fortnightly", "run": "w0", "with": {"ids": [1, 2]}}
    keep = {"every": "fortnightly", "run": "w0", "with": {"ids": [2, 1]}}
    good = {"every": "hourly", "run": "w0"}
    _put(client, iid, ITEM_SCHEDULES, _schedules(malformed, keep, good))
    bad = next(r for r in _rows(client) if r["index"] == 0)
    assert bad["trigger_id"] == ""

    reordered = {"with": {"ids": [1, 2]}, "run": "w0", "every": "fortnightly"}
    r = client.post(
        _wp(iid, "/schedules/remove"),
        json={"path": ITEM_SCHEDULES, "trigger_id": "", "raw": reordered, "index": 0},
    )

    assert r.status_code == 204, r.text
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [keep, good]


def test_two_schedules_differing_only_in_the_order_of_a_list_are_two_rows():
    """`with: {ids: [1, 2]}` and `[2, 1]` are two schedules to the sweep (its
    key fingerprints the payload as written), so removing one keeps the other —
    the rule the panel used to hold itself, now held where the write happens."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    a = {"every": "hourly", "run": "w0", "with": {"ids": [1, 2]}}
    b = {"every": "hourly", "run": "w0", "with": {"ids": [2, 1]}}
    _put(client, iid, ITEM_SCHEDULES, _schedules(a, b))
    second = next(r for r in _rows(client) if r["index"] == 1)

    r = client.post(
        _wp(iid, "/schedules/remove"),
        json={"path": ITEM_SCHEDULES, "trigger_id": second["trigger_id"]},
    )

    assert r.status_code == 204, r.text
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [a]


def test_a_row_that_is_not_an_object_is_written_back_exactly_as_it_was():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(
        client,
        iid,
        ITEM_SCHEDULES,
        json.dumps({"schedules": [5, {"every": "hourly", "run": "w0"}]}),
    )
    good = next(r for r in _rows(client) if r["index"] == 1)

    r = client.post(
        _wp(iid, "/schedules/remove"),
        json={"path": ITEM_SCHEDULES, "trigger_id": good["trigger_id"]},
    )

    assert r.status_code == 204, r.text
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [5]


def test_only_remove_finds_a_row_by_its_value():
    """Edit and Run need a row the sweep would read: a malformed row has no
    time to move and nothing that would run."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    malformed = {"every": "fortnightly", "run": "w0"}
    _put(client, iid, ITEM_SCHEDULES, _schedules(malformed))
    ref = {"path": ITEM_SCHEDULES, "trigger_id": "", "raw": malformed}

    edit = client.post(_wp(iid, "/schedules/edit"), json={**ref, "every": "hourly"})
    run = client.post(_wp(iid, "/schedules/run"), json=ref)

    assert (edit.status_code, run.status_code) == (409, 409)


def test_the_item_panel_says_what_the_viewer_may_do():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=READER)
    _seed(client, iid)

    owner = client.get(_wp(iid, "/schedules")).json()
    holder["id"] = "alice"
    reader = client.get(_wp(iid, "/schedules")).json()

    assert (owner["can_edit"], owner["can_run"]) == (True, True)
    assert (reader["can_edit"], reader["can_run"]) == (False, False)


def _restoring(monkeypatch) -> None:
    """The live workspace mid-restore: every read answers "not there" and the
    listing is empty, while the durable copy is whole — what `_warm` does today
    for a sandbox that exists but has not finished restoring (it never asks
    `is_ready`). Starting a run is what puts an item in this window, so the
    refetch right after Run now landed in it and the page said "nothing is
    scheduled"."""
    from workspace_app.files import WorkspaceFiles
    from workspace_app.filestore.protocol import FileNotFound

    async def _missing(self, workspace_id: str, path: str, **kw) -> bytes:
        raise FileNotFound(path)

    async def _empty(self, workspace_id: str, prefix: str = "", **kw):
        return []

    monkeypatch.setattr(WorkspaceFiles, "read", _missing)
    monkeypatch.setattr(WorkspaceFiles, "ls", _empty)


def test_a_workspace_still_restoring_is_read_from_the_durable_copy(monkeypatch):
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    _restoring(monkeypatch)

    rows = _rows(client)

    assert sorted(r["path"] for r in rows) == sorted([ITEM_SCHEDULES, PAGE_SCHEDULES])
    # Graded against the same copy: its workflow is known, so the rows run.
    assert all(r["known"] and r["runnable"] for r in rows)


def test_the_item_panel_reads_a_restoring_workspace_from_the_durable_copy_too(monkeypatch):
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    _restoring(monkeypatch)

    body = client.get(_wp(iid, "/schedules")).json()

    assert [(r["run"], r["known"]) for r in body["rows"]] == [("w0", True)]


def test_listing_schedules_never_wakes_a_sandbox(monkeypatch):
    """Opening the page reads every listed item; on a host-managed deploy a
    read that may wake would rebuild every idle-reaped sandbox it touched
    (`WorkspaceFiles._warm`). Every listing read asks not to."""
    from workspace_app.files import WorkspaceFiles

    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    asked: list[bool] = []
    real_read, real_ls = WorkspaceFiles.read, WorkspaceFiles.ls

    async def _read(self, workspace_id, path, *, wake=True):
        asked.append(wake)
        return await real_read(self, workspace_id, path, wake=wake)

    async def _ls(self, workspace_id, prefix="", *, wake=True):
        asked.append(wake)
        return await real_ls(self, workspace_id, prefix, wake=wake)

    monkeypatch.setattr(WorkspaceFiles, "read", _read)
    monkeypatch.setattr(WorkspaceFiles, "ls", _ls)

    assert len(_rows(client)) == 2
    assert client.get(_wp(iid, "/schedules")).status_code == 200

    assert asked and not any(asked), asked


def test_one_items_failure_does_not_take_the_page_down(monkeypatch):
    """Review round 1: a read that raises anything but "not there" on one item
    500'd the whole page for every viewer of that item. The item is reported
    with a sentence; every other item still lists."""
    from workspace_app.files import WorkspaceFiles

    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    broken = _item(spec, by="bob", title="Broken")
    fine = _item(spec, by="bob", title="Fine")
    _seed(client, broken)
    _seed(client, fine)
    real_read = WorkspaceFiles.read

    async def _read(self, workspace_id, path, **kw):
        if workspace_id == broken:
            raise IsADirectoryError(path)
        return await real_read(self, workspace_id, path, **kw)

    monkeypatch.setattr(WorkspaceFiles, "read", _read)
    body = client.get("/schedules").json()

    assert {r["item_id"] for r in body["rows"]} == {fine}
    assert {f["item_id"] for f in body["files"]} == {broken}
    assert all(f["problems"] for f in body["files"])


def test_a_viewer_who_may_not_read_the_files_does_not_get_what_a_row_sends(monkeypatch):
    """Review round 1: `read_meta` lists a schedule; the row's `with` is file
    content, which `read_content` guards (`GET /files/...` is a 403 for this
    viewer). The time, the workflow and the identity are still there."""
    meta_only = Permission(visibility="restricted", read_meta=["user:alice"])
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=meta_only)
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    secret = _schedules({"every": "hourly", "run": "w0", "with": {"token": "s3cret"}})
    # Both files: the overview lists both, the item's panel reads the item's own.
    _put(client, iid, PAGE_SCHEDULES, secret)
    _put(client, iid, ITEM_SCHEDULES, secret)
    holder["id"] = "alice"

    rows = _rows(client)
    panel = client.get(_wp(iid, "/schedules"))

    assert len(rows) == 2 and "s3cret" not in json.dumps(rows)
    assert all(r["raw"] == {"every": "hourly", "run": "w0"} and r["trigger_id"] for r in rows)
    assert panel.status_code == 200 and panel.json()["rows"], panel.text
    assert "s3cret" not in panel.text


def test_a_malformed_with_is_not_repeated_back_in_the_rows_problem():
    """Review round 2: the problem sentence quoted the `with` value — the very
    file content a viewer without `read_content` is not sent. It names the
    type instead, which is what the author needs to fix it."""
    meta_only = Permission(visibility="restricted", read_meta=["user:alice"])
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=meta_only)
    _put(
        client, iid, ITEM_SCHEDULES, _schedules({"every": "hourly", "run": "w0", "with": "s3cret"})
    )
    holder["id"] = "alice"

    overview = client.get("/schedules").text
    panel = client.get(_wp(iid, "/schedules")).text

    assert "s3cret" not in overview and "s3cret" not in panel
    assert "`with` must be an object" in panel


@pytest.mark.parametrize(("pick", "left"), [(0, 1), (1, 0)])
def test_who_may_not_read_the_file_removes_exactly_the_row_they_picked(pick: int, left: int):
    """Review round 2: with `edit_content` but not `read_content` a viewer sees
    a refused row without its `with`. Removing "by value" with that redacted
    value matched a DIFFERENT row — one that looks the same without `with` —
    and deleted it. The row is named by its position AND its value as this
    viewer was shown it, so the row picked is the row removed."""
    editor = Permission(
        visibility="restricted", read_meta=["user:alice"], edit_content=["user:alice"]
    )
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=editor)
    rows = [
        {"every": "fortnightly", "run": "w0", "with": {"to": "x"}},
        {"every": "fortnightly", "run": "w0"},
    ]
    _put(client, iid, ITEM_SCHEDULES, _schedules(*rows))
    holder["id"] = "alice"
    listed = sorted(_rows(client), key=lambda r: r["index"])
    assert [r["can_read"] for r in listed] == [False, False]
    assert listed[0]["raw"] == listed[1]["raw"]  # what makes the old match ambiguous

    r = client.post(
        _wp(iid, "/schedules/remove"),
        json={
            "path": ITEM_SCHEDULES,
            "trigger_id": "",
            "raw": listed[pick]["raw"],
            "index": listed[pick]["index"],
        },
    )

    assert r.status_code == 204, r.text
    holder["id"] = "bob"
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [rows[left]]


def test_a_reader_removing_by_a_value_that_changed_since_listing_gets_a_conflict():
    """A viewer who may read is matched on the whole value, `with` included: a
    row whose `with` changed since it was listed is not the row they saw."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    seen = {"every": "fortnightly", "run": "w0", "with": {"to": "x"}}
    now = {"every": "fortnightly", "run": "w0", "with": {"to": "y"}}
    _put(client, iid, ITEM_SCHEDULES, _schedules(now))

    r = client.post(
        _wp(iid, "/schedules/remove"),
        json={"path": ITEM_SCHEDULES, "trigger_id": "", "raw": seen, "index": 0},
    )

    assert r.status_code == 409, r.text
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [now]


def test_removing_by_value_needs_the_rows_position_and_it_must_still_match():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    bad = {"every": "fortnightly", "run": "w0"}
    _put(client, iid, ITEM_SCHEDULES, _schedules(bad))
    ref = {"path": ITEM_SCHEDULES, "trigger_id": "", "raw": bad}

    no_index = client.post(_wp(iid, "/schedules/remove"), json=ref)
    moved = client.post(_wp(iid, "/schedules/remove"), json={**ref, "index": 1})

    assert (no_index.status_code, moved.status_code) == (409, 409)
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [bad]


def test_saving_keeps_text_readable_and_tells_other_viewers(monkeypatch):
    """Review round 1: the rewrite turned 品管課 into \\u54c1…, and — unlike the
    file PUT the panel used before — told nobody: an open tab of the file kept
    the old rows and could save them back."""
    from workspace_app.api.events import FileChanged

    holder = {"id": "bob"}
    client, spec, app = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    neighbour = {"every": "hourly", "run": "w0", "with": {"to": "品管課"}}
    _put(
        client,
        iid,
        ITEM_SCHEDULES,
        _schedules({"every": "daily", "at": "09:00", "run": "w0"}, neighbour),
    )
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(app.state.turn_engine, "publish", lambda key, ev: seen.append((key, ev)))
    ref = {"path": ITEM_SCHEDULES, "trigger_id": _row_of(client, ITEM_SCHEDULES)["trigger_id"]}

    def written_now() -> int:
        return sum(
            e["kind"] == "file_written" and e["ref"].get("path") == ITEM_SCHEDULES
            for e in client.get("/activity").json()
        )

    written_before = written_now()
    r = client.post(_wp(iid, "/schedules/remove"), json=ref)

    assert r.status_code == 204, r.text
    assert "品管課" in client.get(_wp(iid, f"/files{ITEM_SCHEDULES}")).text
    changed = [ev for key, ev in seen if key == iid and isinstance(ev, FileChanged)]
    assert [(ev.path, ev.kind, ev.by) for ev in changed] == [(ITEM_SCHEDULES, "written", "bob")]
    # …and the activity log says so, as it does for the file PUT: one more
    # `file_written` for this path than before the press.
    assert written_now() == written_before + 1


def test_a_folder_with_two_deployed_pages_opens_the_latest_deploy():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    for name in ("old", "new"):
        view = f"/reports/scrap/{name}.ai.yaml"
        _put(client, iid, view, "view: wui\n")
        assert client.post(_wp(iid, "/wui/deploy"), json={"path": view}).status_code == 200

    pages = {r["path"]: r["page_path"] for r in _rows(client)}

    assert pages[PAGE_SCHEDULES] == "/reports/scrap/new.ai.yaml"


def test_a_file_the_index_still_names_but_that_is_gone_is_skipped():
    """The index may name a deleted file (it is stale in that direction only);
    the listing skips it rather than failing the page."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    assert client.delete(_wp(iid, f"/files{PAGE_SCHEDULES}")).status_code in (200, 204)

    assert [r["path"] for r in _rows(client)] == [ITEM_SCHEDULES]


def test_rows_name_the_workflow_and_the_page_by_their_titles():
    """The overview says what a person named things (polish decision 10): the
    workflow's title, not its file name, and the Deployed page's title, not its
    folder. A workflow with no title has none to show — the page falls back to
    its id."""
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("Daily summary"))
    _put(client, iid, "/.workflows/w1.json", _workflow(""))
    _put(
        client,
        iid,
        ITEM_SCHEDULES,
        _schedules({"every": "hourly", "run": "w0"}, {"every": "hourly", "run": "w1"}),
    )
    _put(client, iid, PAGE_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))
    view = "/reports/scrap/page.ai.yaml"
    _put(client, iid, view, "view: wui\ntitle: Scrap board\n")
    assert client.post(_wp(iid, "/wui/deploy"), json={"path": view}).status_code == 200

    named = sorted((r["path"], r["run"], r["run_title"], r["page_title"]) for r in _rows(client))

    assert named == sorted(
        [
            (ITEM_SCHEDULES, "w0", "Daily summary", ""),
            (ITEM_SCHEDULES, "w1", "", ""),
            (PAGE_SCHEDULES, "w0", "Daily summary", "Scrap board"),
        ]
    )


def test_a_profile_workflow_is_named_by_its_manifest_title_and_the_items_own_file_shadows_it():
    from workspace_app.apps.playground.model import PlaygroundItem

    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="bob", profile="multi"))
        .resource_id
    )
    base = f"/a/playground/items/{iid}"
    # `beta` is the profile's; `alpha` is the profile's too, but the item's own
    # file shadows it — the one that runs is the one named.
    r = client.put(f"{base}/files/.workflows/alpha.json", content=_workflow("Our alpha").encode())
    assert r.status_code == 204, r.text
    r = client.put(
        f"{base}/files{ITEM_SCHEDULES}",
        content=_schedules(
            {"every": "hourly", "run": "alpha"}, {"every": "hourly", "run": "beta"}
        ).encode(),
    )
    assert r.status_code == 204, r.text

    titles = {r["run"]: r["run_title"] for r in _rows(client)}
    panel = client.get(f"{base}/schedules").json()

    assert titles == {"alpha": "Our alpha", "beta": "Beta workflow"}
    assert {r["run"]: r["run_title"] for r in panel["rows"]} == titles


def test_last_run_says_whether_it_was_run_by_hand():
    """ "手動" on the last run (polish decision 8): Run now marks its run; a
    fire does not."""
    holder = {"id": "bob"}
    client, spec, app = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    with client:
        _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
        _put(client, iid, ITEM_SCHEDULES, _schedules({"every": "hourly", "run": "w0"}))
        _fire(client, app, iid)
        (fired,) = _rows(client)
        _settle(client, iid)

        r = client.post(
            _wp(iid, "/schedules/run"),
            json={"path": ITEM_SCHEDULES, "trigger_id": fired["trigger_id"]},
        )
        assert r.status_code == 202, r.text
        (pressed,) = _rows(client)

    assert fired["last_run"]["by_hand"] is False
    assert pressed["last_run"]["run_id"] == r.json()["run_id"]
    assert pressed["last_run"]["by_hand"] is True


def _settle(client, iid: str) -> None:
    """Wait for the item's runs to finish — Run now is refused while one is
    going."""
    import time

    runs: list = []
    for _ in range(200):
        runs = client.get(_wp(iid, "/runs")).json()
        if all(run["status"] in {"done", "error", "cancelled"} for run in runs):
            return
        time.sleep(0.02)
    raise AssertionError(f"runs never settled: {runs}")


def test_a_cron_row_edited_back_to_a_period_loses_its_cron():
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/.workflows/w0.json", _workflow("w0"))
    _put(
        client,
        iid,
        ITEM_SCHEDULES,
        _schedules({"cron": "0 9 * * 1-5", "tz": "Asia/Taipei", "run": "w0"}),
    )
    row = _row_of(client, ITEM_SCHEDULES)
    assert row["runnable"], row

    r = client.post(
        _wp(iid, "/schedules/edit"),
        json={
            "path": ITEM_SCHEDULES,
            "trigger_id": row["trigger_id"],
            "every": "daily",
            "at": "09:00",
            "tz": "Asia/Taipei",
        },
    )

    assert r.status_code == 200, r.text
    assert _file_rows(client, iid, ITEM_SCHEDULES) == [
        {"every": "daily", "at": "09:00", "tz": "Asia/Taipei", "run": "w0"}
    ]


@pytest.mark.parametrize(
    ("asked", "said"),
    [
        ({}, "either `every` or `cron`"),
        ({"every": "daily", "at": "09:00", "cron": "0 9 * * *"}, "either `every` or `cron`"),
        ({"cron": "0 25 * * *"}, "not a cron expression"),
    ],
)
def test_an_edit_that_is_not_one_time_is_refused_and_the_file_is_untouched(asked: dict, said: str):
    holder = {"id": "bob"}
    client, spec, _ = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _seed(client, iid)
    before = _file_rows(client, iid, ITEM_SCHEDULES)
    ref = {"path": ITEM_SCHEDULES, "trigger_id": _row_of(client, ITEM_SCHEDULES)["trigger_id"]}

    r = client.post(_wp(iid, "/schedules/edit"), json={**ref, **asked})

    assert r.status_code == 422, r.text
    assert said in r.json()["detail"]
    assert _file_rows(client, iid, ITEM_SCHEDULES) == before
