"""#534 B — the graph reaches the outside.

Three reads and two decisions. Every one of them runs as the caller, so the
filtering is the access scope doing its job rather than a second copy of the
permission rules living in the route layer — the copy is what drifts, and a
permission rule that drifts is a leak.
"""

from __future__ import annotations

from specstar import QB, SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphEntity, GraphMention, mention_id
from workspace_app.resources.kb import EMBED_DIM, Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client_and_spec(holder: dict[str, str]) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _seed(spec: SpecStar, *, private: bool = False) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(
                name="c",
                permission=Permission(visibility="private") if private else None,
            )
        ).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    for doc in ("deck-A", "deck-B"):
        with mrm.using("bob"):
            mrm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=doc,
                    surface="回焊爐",
                    norm_surface=norm_surface("回焊爐"),
                    kind="機台",
                    occurrences=2,
                    chunk_ids=[f"{doc}#0"],
                    collection_visibility="private" if private else "public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(doc, "回焊爐"),
            )
    link_identical_mentions(spec)
    erm = spec.get_resource_manager(GraphEntity)
    for r in erm.list_resources(QB.all().build()):
        assert isinstance(r.data, GraphEntity)
        if r.data.canonical_name == "回焊爐":
            return r.info.resource_id  # ty: ignore[unresolved-attribute]
    raise AssertionError("the seed produced no entity")


def test_the_entity_page_gathers_every_document_that_named_it():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    eid = _seed(spec)
    r = client.get(f"/kb/graph/entities/{eid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "回焊爐"
    assert body["occurrences"] == 4
    assert sorted(m["source_doc_id"] for m in body["mentions"]) == ["deck-A", "deck-B"]
    assert {m["basis"] for m in body["mentions"]} == {"identical"}


def test_an_entity_nobody_may_read_is_a_404_not_an_empty_page():
    """A bare name can leak — a customer code, an unreleased part — so the
    identity itself has to disappear, not merely arrive with nothing in it."""
    holder = {"id": "alice"}
    client, spec = _client_and_spec(holder)
    eid = _seed(spec, private=True)
    assert client.get(f"/kb/graph/entities/{eid}").status_code == 404


def test_the_queue_is_empty_when_nothing_was_proposed():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    r = client.get("/kb/graph/proposals")
    assert r.status_code == 200
    assert r.json() == []


def test_a_decision_needs_a_proposal_that_exists():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    eid = _seed(spec)
    r = client.post(f"/kb/graph/proposals/{eid}/reject", params={"other": "graph-entity:nope"})
    assert r.status_code == 404


def test_the_entity_page_shows_what_the_thing_connects_to():
    """The point of a graph rather than a list: open one thing and see what it
    leads to, with the sentence that said so and the slide it was on."""
    from workspace_app.resources.graph import GraphRelationship, relationship_id

    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    eid = _seed(spec)
    cid = spec.get_resource_manager(GraphMention).get(mention_id("deck-A", "回焊爐")).data
    assert isinstance(cid, GraphMention)
    rrm = spec.get_resource_manager(GraphRelationship)
    with rrm.using("bob"):
        rrm.create(
            GraphRelationship(
                collection_id=cid.collection_id,
                source_doc_id="deck-A",
                subject="回焊爐",
                predicate="造成",
                object="空洞",
                norm_subject=norm_surface("回焊爐"),
                norm_predicate=norm_surface("造成"),
                norm_object=norm_surface("空洞"),
                chunk_id="deck-A#0",
                quote="回焊爐溫度過高造成空洞",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=relationship_id("deck-A", "deck-A#0", "回焊爐", "造成", "空洞"),
        )
    body = client.get(f"/kb/graph/entities/{eid}").json()
    (rel,) = body["related"]
    assert rel["direction"] == "out"
    assert rel["predicate"] == "造成"
    assert rel["other_name"] == "空洞"
    assert rel["quote"] == "回焊爐溫度過高造成空洞"
    assert rel["chunk_id"] == "deck-A#0"
