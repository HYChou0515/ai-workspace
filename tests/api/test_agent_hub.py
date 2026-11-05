"""P7 of `docs/plan-wui-publish-and-agent-hub.md`: Agent Hub's server half.

Every published agent the viewer may use (C9), with usage numbers and no
names (A7); WUIs are not here. An agent's page: its latest release notes and
the viewer's OWN conversations — each a copy — with their size (A8/A9/C8).
"""

from __future__ import annotations

from urllib.parse import quote

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

AGENT = "/agents/sales/agent.ai.yaml"
YAML = b"view: agent\ntitle: Sales helper\ndescription: Orders.\ninstructions: Help.\n"


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


def _source(spec, by: str = "bob") -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title="Line 3", owner=by)).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _publish(client, src: str, reach: str = "everyone", note: str = "", path: str = AGENT):
    assert client.put(_wp(src, f"/files{path}"), content=YAML).status_code == 204
    body = {"path": path, "sharing": "per_conversation", "reach": reach, "note": note}
    r = client.post(_wp(src, "/publish"), json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _open(client, src: str) -> str:
    r = client.post("/published/open", json={"item_id": src, "path": AGENT})
    assert r.status_code == 200, r.text
    return r.json()["item_id"]


def test_lists_the_agents_the_viewer_may_use_not_wuis_not_others():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    everyone = _source(spec)
    mine_only = _source(spec)
    _publish(client, everyone)
    _publish(client, mine_only, reach="me")
    page = "/pages/a/page.ai.yaml"
    assert (
        client.put(_wp(everyone, f"/files{page}"), content=b"view: wui\ntitle: B\n").status_code
        == 204
    )
    r = client.post(
        _wp(everyone, "/publish"), json={"path": page, "sharing": "fresh", "reach": "everyone"}
    )
    assert r.status_code == 200, r.text
    holder["id"] = "amy"

    agents = client.get("/agents").json()["agents"]

    assert [(a["item_id"], a["title"], a["description"]) for a in agents] == [
        (everyone, "Sales helper", "Orders.")
    ]
    assert agents[0]["can_remove"] is False
    holder["id"] = "bob"
    assert {a["item_id"] for a in client.get("/agents").json()["agents"]} == {everyone, mine_only}


def test_usage_is_counts_never_names():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    _publish(client, src)
    for who in ("amy", "amy", "carl"):
        holder["id"] = who
        _open(client, src)

    (card,) = client.get("/agents").json()["agents"]

    assert (card["opens"], card["people"]) == (3, 2)
    assert "amy" not in str(card) and "carl" not in str(card)


def test_an_agents_page_lists_the_viewers_own_conversations_newest_first():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    agent_id = _publish(client, src, note="Older.")
    _publish(client, src, note="Shorter answers.")
    holder["id"] = "amy"
    first = _open(client, src)
    second = _open(client, src)
    holder["id"] = "carl"
    _open(client, src)
    holder["id"] = "amy"
    r = client.post(_wp(second, "/messages"), json={"content": "Which orders shipped late?"})
    assert r.status_code in (200, 202), r.text

    resp = client.get(f"/agents/{quote(agent_id, safe='')}")
    assert resp.status_code == 200, resp.text
    page = resp.json()

    assert page["agent"]["title"] == "Sales helper"
    assert [n["text"] for n in page["notes"]] == ["Shorter answers.", "Older."]
    assert [c["item_id"] for c in page["mine"]] == [second, first]
    assert page["mine"][0]["hint"] == "Which orders shipped late?"
    assert page["mine"][1]["hint"] == ""
    assert all(c["slug"] == "rca" and c["bytes"] >= 0 for c in page["mine"])


def test_an_agent_not_published_to_the_viewer_is_not_there():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    agent_id = _publish(client, src, reach="me")
    holder["id"] = "amy"

    assert client.get(f"/agents/{quote(agent_id, safe='')}").status_code == 404
    assert client.get("/agents/nothing").status_code == 404


def test_a_wui_row_is_not_an_agents_page():
    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    page = "/pages/a/page.ai.yaml"
    assert (
        client.put(_wp(src, f"/files{page}"), content=b"view: wui\ntitle: B\n").status_code == 204
    )
    r = client.post(
        _wp(src, "/publish"), json={"path": page, "sharing": "fresh", "reach": "everyone"}
    )
    assert r.status_code == 200, r.text

    assert client.get(f"/agents/{quote(r.json()['id'], safe='')}").status_code == 404


def test_a_long_first_question_is_cut_to_name_the_conversation():
    from workspace_app.api.agent_hub_routes import HINT_CHARS

    holder = {"id": "bob"}
    client, spec = _app(holder)
    src = _source(spec)
    agent_id = _publish(client, src)
    copy = _open(client, src)
    long = "why " * 60
    assert client.post(_wp(copy, "/messages"), json={"content": long}).status_code in (200, 202)

    (mine,) = client.get(f"/agents/{quote(agent_id, safe='')}").json()["mine"]

    assert len(mine["hint"]) == HINT_CHARS
    assert mine["hint"].endswith("…")


def test_a_link_names_its_agent_however_often_it_was_decoded():
    """A row id is `quote(item) + path`; a browser, a router and the server
    may each decode it once, and every spelling must reach the same row."""
    from workspace_app.api.publish import canonical_id, published_id

    rid = published_id("rca-investigation:abc", "/agents/sales/agent.ai.yaml")
    decoded = rid.replace("%3A", ":")

    assert canonical_id(rid) == rid
    assert canonical_id(decoded) == rid
    assert canonical_id("nothing") == "nothing"
