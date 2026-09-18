"""The WUI overview (`docs/plan-wui-overview.md`): Deploy lists a page, `/wui`
finds it, Remove takes it down.

Real `create_app` + a real spec, because the listing's whole job is to show a
viewer exactly the pages they could open by address — a permission filter,
which a double of the locator would only pretend to apply.
"""

from __future__ import annotations

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.wui_deploy import DeployedPages, DeployedWui
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client_and_spec(holder: dict[str, str], *, superusers=frozenset()):
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
    )
    return TestClient(app), spec


def _item(spec, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        item = RcaInvestigation(title="Line 3", owner=by, permission=permission)
        return rm.create(item).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


PAGE = "/pages/report/page.ai.yaml"


WUI = b"view: wui\ntitle: Shipping board\n"


def _page(client, iid: str, path: str = PAGE, body: bytes = WUI) -> None:
    assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204


def test_deploy_lists_the_page_under_the_title_its_view_file_gives_it():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid)

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["slug"], row["item_id"], row["path"], row["title"], row["deployed_by"]) == (
        "rca",
        iid,
        PAGE,
        "Shipping board",
        "bob",
    )
    assert isinstance(row["deployed_at"], int)
    listing = client.get("/wui")
    assert listing.status_code == 200, listing.text
    rows = listing.json()["pages"]
    assert [(p["item_id"], p["path"], p["title"], p["item_title"]) for p in rows] == [
        (iid, PAGE, "Shipping board", "Line 3")
    ]


def test_a_page_without_a_title_is_named_after_its_folder():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, body=b"view: wui\n")

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 200, r.text
    assert r.json()["title"] == "report"


@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("/status.ai.yaml", "status"),
        # A file named exactly `.ai.yaml`: the stem is empty, and an empty title
        # is a row nobody can read or press — the file's name is the fallback.
        ("/.ai.yaml", ".ai.yaml"),
    ],
)
def test_a_page_at_the_workspace_root_has_no_folder_so_it_takes_the_files_name(path, title):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, path=path, body=b"view: wui\n")

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": path})

    assert r.status_code == 200, r.text
    assert r.json()["title"] == title


@pytest.mark.parametrize(
    ("line", "icon"),
    [
        # The three forms App icons take (`plan-wui-overview-icon-favourites`):
        # a file in the page's folder, one emoji, a named-icon key. The server
        # stores the string; which form it is, and whether it resolves, is the
        # overview's call at render — a decoration must not block a Deploy.
        ("icon: logo.png\n", "logo.png"),
        ('icon: "📦"\n', "📦"),
        ("icon: kanban\n", "kanban"),
        # Stripped, like `title:`.
        ('icon: "  📦 "\n', "📦"),
    ],
)
def test_deploy_carries_the_icon_the_view_file_declares(line: str, icon: str):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, body=WUI + line.encode())

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 200, r.text
    assert r.json()["icon"] == icon
    assert [p["icon"] for p in client.get("/wui").json()["pages"]] == [icon]


@pytest.mark.parametrize(
    "line",
    [
        b"",
        # Not a string, or an empty one: "none". Deploy still passes — the
        # overview draws the default circle, which is how the author sees the
        # icon did not take.
        b"icon: 3\n",
        b"icon: [a]\n",
        b'icon: ""\n',
        b'icon: "   "\n',
    ],
)
def test_a_missing_or_malformed_icon_is_none_and_deploy_still_passes(line: bytes):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, body=WUI + line)

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 200, r.text
    assert r.json()["icon"] == ""


def test_a_row_written_before_the_icon_field_lists_with_none():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    # The P1 shape of the row — no `icon` — recorded straight into the store,
    # as every row Deployed before this field existed was.
    DeployedPages(spec).record(
        DeployedWui(slug="rca", item_id=iid, path=PAGE, title="t", deployed_by="bob", deployed_at=1)
    )

    rows = client.get("/wui").json()["pages"]

    assert [(p["title"], p["icon"]) for p in rows] == [("t", "")]


def test_the_row_names_the_person_who_pressed_deploy_not_the_owner():
    # Every other Deploy in this file is the owner's, so `deployed_by` could be
    # a constant and nothing would notice; an editor who is not the owner tells
    # the caller from the owner.
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec,
        by="bob",
        permission=Permission(
            visibility="restricted",
            read_meta=["user:alice"],
            read_content=["user:alice"],
            edit_content=["user:alice"],
        ),
    )
    _page(client, iid)
    holder["id"] = "alice"

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 200, r.text
    assert r.json()["deployed_by"] == "alice"
    assert r.json()["can_remove"] is True
    # …and the item's OWNER is a different person, named on the row too
    # (the author: 「Owner 也要在上面」) — in the Deploy response and the listing.
    assert r.json()["item_owner"] == "bob"
    assert [(p["deployed_by"], p["item_owner"]) for p in client.get("/wui").json()["pages"]] == [
        ("alice", "bob")
    ]


def test_a_view_file_nested_past_the_parsers_depth_is_a_400_not_a_500():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, body=b"view: " + b"[" * 5000)

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 400, r.text
    assert "not valid yaml" in r.json()["detail"]


def test_a_view_of_another_kind_is_refused_and_the_kind_is_named():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, path="/board.ai.yaml", body=b"view: board\nentity: task\n")

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": "/board.ai.yaml"})

    assert r.status_code == 400
    assert "board" in r.json()["detail"]
    assert client.get("/wui").json()["pages"] == []


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("/pages/report/index.html", "not a view file"),
        ("/node_modules/pkg/page.ai.yaml", "not a page of this item"),
        ("/pages/missing/page.ai.yaml", "view file not found"),
    ],
)
def test_refuses_what_cannot_be_a_page_and_says_why(path: str, why: str):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, path="/pages/report/index.html", body=b"<h1>hi</h1>")
    _page(client, iid, path="/node_modules/pkg/page.ai.yaml", body=b"view: wui\n")

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": path})

    assert r.status_code == 400, r.text
    assert why in r.json()["detail"]


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (b"view: [unclosed\n", "not valid yaml"),
        (b"- just\n- a list\n", "view 'none'"),
        (b"view: 3\n", "view 'none'"),
    ],
)
def test_a_view_file_that_does_not_say_what_it_is_is_refused(body: bytes, why: str):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, body=body)

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 400, r.text
    assert why in r.json()["detail"]


def test_the_overview_lists_the_latest_deploy_first(monkeypatch: pytest.MonkeyPatch):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, path="/a/page.ai.yaml", body=b"view: wui\ntitle: A\n")
    _page(client, iid, path="/b/page.ai.yaml", body=b"view: wui\ntitle: B\n")
    clock = iter([1_000, 2_000, 3_000])
    monkeypatch.setattr("workspace_app.api.wui_deploy.now_ms", lambda: next(clock))

    for path in ("/a/page.ai.yaml", "/b/page.ai.yaml", "/a/page.ai.yaml"):
        assert client.post(_wp(iid, "/wui/deploy"), json={"path": path}).status_code == 200

    # A was Deployed again after B, so A leads — the order is the LATEST
    # Deploy of each page, not the first.
    assert [(p["title"], p["deployed_at"]) for p in client.get("/wui").json()["pages"]] == [
        ("A", 3_000),
        ("B", 2_000),
    ]


# ── who may put a page up, and who sees it ─────────────────────────────────────

READER = Permission(visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"])


def test_a_reader_may_open_the_page_but_not_list_it():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=READER)
    _page(client, iid)
    holder["id"] = "alice"

    r = client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE})

    assert r.status_code == 403
    assert client.post(_wp("nope", "/wui/deploy"), json={"path": PAGE}).status_code == 404


def test_the_overview_shows_a_viewer_only_what_they_could_open_by_address():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))
    shared = _item(spec, by="bob", permission=READER)
    private = _item(spec, by="bob", permission=Permission(visibility="private"))
    _page(client, shared)
    _page(client, private)
    assert client.post(_wp(shared, "/wui/deploy"), json={"path": PAGE}).status_code == 200
    assert client.post(_wp(private, "/wui/deploy"), json={"path": PAGE}).status_code == 200

    def seen() -> list[tuple[str, bool]]:
        return [(p["item_id"], p["can_remove"]) for p in client.get("/wui").json()["pages"]]

    # The owner: both, and may take both down.
    assert sorted(seen()) == sorted([(shared, True), (private, True)])
    # A reader of one: that one, and may not take it down.
    holder["id"] = "alice"
    assert seen() == [(shared, False)]
    # A stranger: nothing — and no hint that anything exists.
    holder["id"] = "carol"
    assert seen() == []
    # A superuser: everything.
    holder["id"] = "root"
    assert sorted(seen()) == sorted([(shared, True), (private, True)])


def test_a_deleted_items_pages_vanish_from_the_overview_but_not_from_the_store():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    # Written straight into the store rather than through Deploy: the locator
    # memoises a POSITIVE access answer for five seconds (`_ACCESS_WINDOW_S`),
    # so a page Deployed and then deleted inside one window is still listed
    # for the rest of it — the same window every gate on the platform has
    # (`test_item_perm.py` records it). What this test pins is the FILTER, and
    # it must meet the deleted item on its first look.
    DeployedPages(spec).record(
        DeployedWui(slug="rca", item_id=iid, path=PAGE, title="t", deployed_by="bob", deployed_at=1)
    )

    spec.get_resource_manager(RcaInvestigation).delete(iid)

    assert client.get("/wui").json()["pages"] == []
    # The row is still there — the listing FILTERED it, nothing cascaded. That
    # is the difference between "the item is gone" and "somebody removed the
    # page", and the plan keeps them apart on purpose.
    assert [r.path for r in DeployedPages(spec).newest_first()] == [PAGE]


def test_deploying_the_same_page_again_refreshes_its_row_rather_than_adding_one():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid, body=b"view: wui\ntitle: First\n")
    assert client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE}).status_code == 200
    _page(client, iid, body=b"view: wui\ntitle: Second\n")
    _page(client, iid, path="/other.ai.yaml", body=b"view: wui\ntitle: Other\n")

    assert client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE}).status_code == 200
    assert client.post(_wp(iid, "/wui/deploy"), json={"path": "/other.ai.yaml"}).status_code == 200

    # One row per page — the positive control is the second page, which IS a
    # second row.
    assert sorted((p["path"], p["title"]) for p in client.get("/wui").json()["pages"]) == [
        ("/other.ai.yaml", "Other"),
        (PAGE, "Second"),
    ]


# ── Remove ─────────────────────────────────────────────────────────────────────


def test_remove_takes_the_page_off_the_overview_and_a_second_press_is_not_an_error():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _page(client, iid)
    assert client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE}).status_code == 200

    r = client.delete(_wp(iid, "/wui/deploy"), params={"path": PAGE})

    assert r.status_code == 204, r.text
    assert client.get("/wui").json()["pages"] == []
    assert client.delete(_wp(iid, "/wui/deploy"), params={"path": PAGE}).status_code == 204


def test_a_reader_may_not_remove_a_page():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=READER)
    _page(client, iid)
    assert client.post(_wp(iid, "/wui/deploy"), json={"path": PAGE}).status_code == 200
    holder["id"] = "alice"

    r = client.delete(_wp(iid, "/wui/deploy"), params={"path": PAGE})

    assert r.status_code == 403
    assert [p["path"] for p in client.get("/wui").json()["pages"]] == [PAGE]
