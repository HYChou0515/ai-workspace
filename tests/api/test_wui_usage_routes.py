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
