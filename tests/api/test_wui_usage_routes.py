"""P2–P3 of `docs/plan-wui-usage.md` through the real app: a deployed page's
link reports its opens (`POST …/wui/opened`) and names itself on every use
(`X-Wui-Deployed`), and only a page that IS deployed is counted (U7).

The counts are read from the app's own recorder, before any flush.
"""

from __future__ import annotations

from urllib.parse import quote

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

PAGE = "/pages/report/page.ai.yaml"
WUI = b"view: wui\ntitle: Shipping board\n"


def _world():
    """bob owns the item and deploys the page; alice may read it (a viewer)."""
    holder = {"id": "bob"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    rm = spec.get_resource_manager(RcaInvestigation)
    readers = ["user:alice"]
    with rm.using("bob"):
        iid = rm.create(
            RcaInvestigation(
                title="Line 3",
                owner="bob",
                permission=Permission(
                    visibility="restricted",
                    read_meta=readers,
                    read_chat=readers,
                    read_content=readers,
                ),
            )
        ).resource_id
    client = TestClient(app)
    base = f"/a/rca/items/{iid}"
    assert client.put(f"{base}/files{PAGE}", content=WUI).status_code == 204
    return client, holder, iid, app


def _deploy(client, iid: str, path: str = PAGE) -> None:
    assert client.post(f"/a/rca/items/{iid}/wui/deploy", json={"path": path}).status_code == 200


def _opens(app) -> dict[tuple[str, str, str], dict[str, list[int]]]:
    return app.state.wui_usage_store.recorder.drain()


def test_opening_a_deployed_page_by_its_link_is_counted_for_the_viewer():
    client, holder, iid, app = _world()
    _deploy(client, iid)
    holder["id"] = "alice"

    r = client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": PAGE})

    assert r.status_code == 204, r.text
    ((key, users),) = _opens(app).items()
    assert key[:2] == (iid, PAGE)
    assert users["alice"][0] == 1


def test_a_page_that_is_not_deployed_is_not_counted():
    """U7 and the bound on rows: only a page with a deploy record is a key."""
    client, holder, iid, app = _world()
    holder["id"] = "alice"

    r = client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": PAGE})

    assert r.status_code == 204
    assert _opens(app) == {}


def test_a_path_that_climbs_out_is_refused_and_not_counted():
    """The deploy route's rule for a path: a `..` segment is a 400."""
    client, holder, iid, app = _world()
    _deploy(client, iid)
    holder["id"] = "alice"

    r = client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": "/pages/../x/page.ai.yaml"})

    assert r.status_code == 400
    assert _opens(app) == {}


def test_a_made_up_path_is_not_counted():
    client, holder, iid, app = _world()
    _deploy(client, iid)
    holder["id"] = "alice"

    client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": "/nope/x.ai.yaml"})

    assert _opens(app) == {}


def test_a_path_spelled_differently_counts_as_the_same_page():
    """The path is normalised the way the deploy route normalises it."""
    client, holder, iid, app = _world()
    _deploy(client, iid)
    holder["id"] = "alice"

    client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": PAGE.lstrip("/")})

    ((key, _users),) = _opens(app).items()
    assert key[1] == PAGE


def test_someone_who_cannot_open_the_page_is_refused_and_not_counted():
    client, holder, iid, app = _world()
    _deploy(client, iid)
    holder["id"] = "mallory"

    r = client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": PAGE})

    assert r.status_code in (403, 404)
    assert _opens(app) == {}


def deployed(path: str = PAGE) -> dict[str, str]:
    """The header a deployed page's link puts on its requests."""
    return {"X-Wui-Deployed": quote(path, safe="")}


# ── P4: the page's owner reads its numbers on the overview (U5) ──────────


def _card(client, iid: str) -> dict:
    (row,) = [p for p in client.get("/wui").json()["pages"] if p["item_id"] == iid]
    return row


def test_the_owner_reads_the_page_s_numbers_on_its_card():
    client, holder, iid, app = _world()
    _deploy(client, iid)
    holder["id"] = "alice"
    client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": PAGE})
    holder["id"] = "bob"
    client.post(f"/a/rca/items/{iid}/wui/opened", json={"path": PAGE})
    app.state.wui_usage_store.recorder.used(iid, PAGE, user="alice", kind="tool", ms=1500)
    app.state.wui_usage_store.flush()

    usage = _card(client, iid)["usage"]

    assert usage == {
        "opened_total": 2,
        "opened_7d": 2,
        "used_total": 1,
        "used_ms_total": 1500,
        "used_7d": 1,
        "used_ms_7d": 1500,
    }


def test_someone_who_cannot_change_the_item_gets_no_numbers():
    """U5: the numbers are the page owner's; a viewer's card carries none."""
    client, holder, iid, app = _world()
    _deploy(client, iid)
    app.state.wui_usage_store.recorder.opened(iid, PAGE, user="alice")
    app.state.wui_usage_store.flush()
    holder["id"] = "alice"

    assert _card(client, iid)["usage"] is None


def test_a_page_nobody_has_used_shows_zeros_to_its_owner():
    client, _holder, iid, _app = _world()
    _deploy(client, iid)

    assert _card(client, iid)["usage"]["opened_total"] == 0


def test_the_overview_still_lists_its_pages_when_the_numbers_cannot_be_read(monkeypatch):
    """Review round 1 (regression 2): the numbers are extra — a store that
    fails takes them away, never the listing."""
    from workspace_app.api.wui_usage import WuiUsageStore

    client, _holder, iid, _app = _world()
    _deploy(client, iid)

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise OSError("db down")

    monkeypatch.setattr(WuiUsageStore, "summary", boom)

    r = client.get("/wui")

    assert r.status_code == 200, r.text
    assert _card(client, iid)["usage"] is None


# ── the app writes what it counted on its interval (U9; on stopping: test_wui_usage) ─


def _bare_app(**kw):  # noqa: ANN003, ANN202
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        **kw,
    )
    return app, spec


def _written(spec) -> int:  # noqa: ANN001
    from workspace_app.api.wui_usage import WuiUsageDay

    return len(spec.get_resource_manager(WuiUsageDay).list_resources())


def test_the_app_writes_the_page_counts_on_its_interval():
    import threading
    from datetime import timedelta

    app, spec = _bare_app(wui_usage_flush_interval=timedelta(milliseconds=20))
    with TestClient(app):
        app.state.wui_usage_store.recorder.opened("i1", PAGE, user="alice")
        for _ in range(200):
            if _written(spec):
                break
            threading.Event().wait(0.01)
        assert _written(spec) == 1


def test_the_counts_have_no_routes_of_their_own():
    """Review round 1 (R20): the counts, their totals and a run's tie to its
    page are registered after `spec.apply`, so specstar gives them no
    auto-CRUD — nobody can write a page's numbers but the counting itself."""
    app, _spec = _bare_app()
    paths = list(app.openapi()["paths"])

    # The control: a model registered before `spec.apply` has its routes, so
    # an absence below is not a wrong guess at the route's name.
    assert "/api/conversation/data" in paths
    for name in ("wui-usage-day", "wui-usage-total", "wui-run-use"):
        assert not [p for p in paths if p.startswith(f"/api/{name}")], name
