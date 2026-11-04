"""P4 of `docs/plan-wui-publish-and-agent-hub.md`, server half: the `/wui`
overview lists published WUIs (W4).

A WUI that hands out copies is listed to whoever may USE it (C9) — reading
the source is not asked, since nobody works in the source; one that uses the
workspace directly is listed to whoever may read the source, as a Deploy
always was. Agents are not listed here: they live in Agent Hub (A1). A page
Deployed before Publish existed keeps its row and reads as "use my
workspace directly" (W2). Usage numbers are shown to everyone, without
names (W4).
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.wui_deploy import DeployedPages, DeployedWui
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

PAGE = "/pages/report/page.ai.yaml"
AGENT = "/agents/sales/agent.ai.yaml"


def _app(holder: dict[str, str]):
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


PRIVATE = Permission(visibility="private")


def _source(spec, *, by: str = "bob", permission=PRIVATE) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(
            RcaInvestigation(title="Line 3", owner=by, permission=permission)
        ).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _files(client, iid: str) -> None:
    for path, body in (
        (PAGE, b"view: wui\ntitle: Shipping board\n"),
        (AGENT, b"view: agent\ntitle: Sales helper\ninstructions: Help.\n"),
    ):
        assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204


def _publish(client, iid: str, path: str = PAGE, **body):
    r = client.post(_wp(iid, "/publish"), json={"path": path, **body})
    assert r.status_code == 200, r.text


def _listed(client) -> list[dict]:
    r = client.get("/wui")
    assert r.status_code == 200, r.text
    return r.json()["pages"]


def test_a_wui_that_hands_out_copies_is_listed_to_who_may_use_it_not_who_may_read_the_source():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    _files(client, src)
    _publish(client, src, sharing="per_person", reach="everyone")
    holder["id"] = "amy"

    rows = _listed(client)

    assert [(r["item_id"], r["path"], r["title"], r["sharing"], r["published"]) for r in rows] == [
        (src, PAGE, "Shipping board", "per_person", True)
    ]


def test_a_wui_for_the_publisher_alone_is_not_listed_to_others():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    _files(client, src)
    _publish(client, src, sharing="per_person", reach="me")
    holder["id"] = "amy"

    assert _listed(client) == []


def test_agents_are_not_listed_on_the_wui_overview():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    _files(client, src)
    _publish(client, src, AGENT, sharing="per_conversation", reach="everyone")

    assert _listed(client) == []


def test_a_page_deployed_before_publish_reads_as_use_my_workspace_directly():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec, permission=None)
    _files(client, src)
    DeployedPages(spec).record(
        DeployedWui(
            slug="rca", item_id=src, path=PAGE, title="Old", deployed_by="bob", deployed_at=1
        )
    )

    rows = _listed(client)

    assert [(r["title"], r["sharing"], r["published"]) for r in rows] == [("Old", "direct", False)]


def test_publishing_a_deployed_page_replaces_its_old_row():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec, permission=None)
    _files(client, src)
    DeployedPages(spec).record(
        DeployedWui(
            slug="rca", item_id=src, path=PAGE, title="Old", deployed_by="bob", deployed_at=1
        )
    )

    _publish(client, src, sharing="fresh", reach="everyone")

    assert [(r["title"], r["sharing"]) for r in _listed(client)] == [("Shipping board", "fresh")]
    assert DeployedPages(spec).newest_first(src) == []


def test_usage_numbers_count_opens_and_people_without_naming_them():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    _files(client, src)
    _publish(client, src, sharing="fresh", reach="everyone")
    for who in ("amy", "amy", "carl"):
        holder["id"] = who
        r = client.post("/published/open", json={"item_id": src, "path": PAGE})
        assert r.status_code == 200, r.text

    (row,) = _listed(client)

    assert (row["opens"], row["people"]) == (3, 2)
    assert "amy" not in str(row) and "carl" not in str(row)


def test_only_who_may_manage_it_may_take_it_down():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    _files(client, src)
    _publish(client, src, sharing="per_person", reach="everyone")

    assert _listed(client)[0]["can_remove"] is True
    holder["id"] = "amy"
    assert _listed(client)[0]["can_remove"] is False


def test_a_deploy_row_written_after_a_publish_does_not_list_the_page_twice():
    """An old page's Deploy (a browser tab still on the previous version) can
    write its row after the page was published; the publication wins."""
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec, permission=None)
    _files(client, src)
    _publish(client, src, sharing="fresh", reach="everyone")
    DeployedPages(spec).record(
        DeployedWui(
            slug="rca", item_id=src, path=PAGE, title="Old", deployed_by="bob", deployed_at=9
        )
    )

    assert [(r["title"], r["sharing"]) for r in _listed(client)] == [("Shipping board", "fresh")]
