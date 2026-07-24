"""#636 — browse what the graph actually built.

Until now the only way into an entity was knowing its id. This is the list:
page through it, narrow it by the fields the entity table indexes, and search
it by name. The shape is set by measurement (see #636): counting the total
takes seconds because every row must be permission-checked, so there is no
"page 3 of 200" — only "here is a page, and whether more follow".
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphMention, mention_id
from workspace_app.resources.kb import Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _seed(spec, *, private: bool = False) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="c", permission=Permission(visibility="private") if private else None)
        ).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    for surface, kind in (("回焊爐", "機台"), ("錫膏", "材料"), ("PPOOIXUX", "recipe")):
        with mrm.using("bob"):
            mrm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id="deck-A",
                    surface=surface,
                    norm_surface=norm_surface(surface),
                    kind=kind,
                    norm_kind=norm_surface(kind),
                    occurrences=1,
                    chunk_ids=["deck-A#0"],
                    collection_visibility="private" if private else "public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id("deck-A", surface),
            )
    link_identical_mentions(spec)
    return cid


def _client(who: dict[str, str]):
    spec = make_spec(default_user=lambda: who["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: who["id"],
    )
    return TestClient(app), spec


def test_the_list_pages_without_ever_counting_the_whole_thing():
    who = {"id": "bob"}
    client, spec = _client(who)
    _seed(spec)
    body = client.get("/kb/graph/entities?limit=2").json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True  # "more follow", never "N pages"
    assert "total" not in body

    nxt = client.get(f"/kb/graph/entities?limit=2&offset={body['next_offset']}").json()
    assert nxt["items"], "the second page is not empty"
    first = {i["id"] for i in body["items"]}
    assert not (first & {i["id"] for i in nxt["items"]}), "pages must not overlap"


def test_the_list_narrows_by_kind():
    who = {"id": "bob"}
    client, spec = _client(who)
    _seed(spec)
    all_names = {i["name"] for i in client.get("/kb/graph/entities?limit=50").json()["items"]}
    assert {"回焊爐", "錫膏", "PPOOIXUX"} <= all_names

    only = client.get("/kb/graph/entities?limit=50&kind=機台").json()["items"]
    assert [i["name"] for i in only] == ["回焊爐"]


def test_the_list_narrows_by_knowledge_base():
    who = {"id": "bob"}
    client, spec = _client(who)
    cid = _seed(spec)
    got = client.get(f"/kb/graph/entities?limit=50&collection={cid}").json()["items"]
    assert {"回焊爐", "錫膏", "PPOOIXUX"} <= {i["name"] for i in got}
    assert client.get("/kb/graph/entities?limit=50&collection=nope").json()["items"] == []


def test_the_list_shows_nothing_from_a_knowledge_base_you_cannot_open():
    who = {"id": "bob"}
    client, spec = _client(who)
    _seed(spec, private=True)
    assert client.get("/kb/graph/entities?limit=50").json()["items"], "owner sees them"
    who["id"] = "alice"
    assert client.get("/kb/graph/entities?limit=50").json()["items"] == []


def test_searching_by_name_finds_it_without_the_exact_spelling():
    who = {"id": "bob"}
    client, spec = _client(who)
    _seed(spec)
    got = client.get("/kb/graph/entities?limit=50&q=ppooi").json()["items"]
    assert [i["name"] for i in got] == ["PPOOIXUX"]


def test_search_results_respect_permission_too():
    who = {"id": "bob"}
    client, spec = _client(who)
    _seed(spec, private=True)
    who["id"] = "alice"
    assert client.get("/kb/graph/entities?limit=50&q=ppooi").json()["items"] == []
