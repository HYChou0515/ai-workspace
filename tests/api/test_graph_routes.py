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
from workspace_app.kb.graph.normalize import norm_attribute, norm_surface
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphEntity, GraphMention, mention_id
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
                    norm_kind=norm_surface("機台"),
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


def test_the_page_names_the_kind_once_the_vocabulary_has_run():
    """A kind is an identity too, so it only appears once something vouches for
    it — the same rule that hides an unbacked name. Before the fix that gave kinds
    their evidence, this was empty for everyone including the owner."""
    from workspace_app.kb.graph.link import reconcile_vocabulary

    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    eid = _seed(spec)
    reconcile_vocabulary(spec, llm=None)
    assert client.get(f"/kb/graph/entities/{eid}").json()["kind"] == "機台"


def test_the_aliases_shown_are_words_someone_wrote():
    """The keys are normalised — lowercased, folded — and nobody wrote them that
    way. Showing "stencil printer" where the deck said "Stencil Printer" puts a
    string no document contains in front of a reader, which is the one thing the
    display name rule exists to prevent; the same rule has to hold for aliases."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    mrm = spec.get_resource_manager(GraphMention)
    cid = mrm.get(mention_id("deck-A", "回焊爐")).data
    assert isinstance(cid, GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=cid.collection_id,
                source_doc_id="deck-B",
                surface="Reflow Oven",
                norm_surface=norm_surface("Reflow Oven"),
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-B", "Reflow Oven"),
        )
    from workspace_app.kb.graph.link import link_identical_mentions
    from workspace_app.kb.graph.review import accept_proposal

    link_identical_mentions(spec)
    eid = _entity_id(spec, "回焊爐")
    accept_proposal(spec, eid, _entity_id(spec, "Reflow Oven"), by="amy")
    aliases = client.get(f"/kb/graph/entities/{eid}").json()["aliases"]
    assert "Reflow Oven" in aliases  # as written, not "reflow oven"
    assert "reflow oven" not in aliases


def _entity_id(spec, name: str) -> str:
    from specstar import QB as _QB

    from workspace_app.resources.graph import GraphEntity

    erm = spec.get_resource_manager(GraphEntity)
    for r in erm.list_resources(_QB.all().build()):
        assert isinstance(r.data, GraphEntity)
        if r.data.canonical_name == name:
            return r.info.resource_id
    raise AssertionError(name)


def test_the_entity_page_carries_the_numbers_stated_beside_it():
    """#628 P2 — claims ride the entity response: a metric stated on a slide
    that names the entity arrives with enough provenance to open and check."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    eid = _seed(spec)
    crm = spec.get_resource_manager(Collection)
    rows = list(crm.list_resources(QB.all().build()))
    cid = rows[0].info.resource_id  # ty: ignore[unresolved-attribute]
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("bob"):
        rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                chunk_id="deck-A#0",
                norm_subject=norm_surface("回焊爐"),
                subject="回焊爐",
                norm_attribute=norm_attribute("良率"),
                attribute="良率",
                value="98.7",
                period="Q3",
                norm_period="q3",
                unit="%",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        )

    body = client.get(f"/kb/graph/entities/{eid}").json()
    assert len(body["claims"]) == 1
    c = body["claims"][0]
    assert c["attribute"] == "良率"
    assert c["norm_attribute"] == norm_attribute("良率")
    assert c["value"] == "98.7"
    assert c["unit"] == "%"
    assert c["period"] == "Q3"
    assert c["norm_period"] == "q3"
    assert c["source_doc_id"] == "deck-A"
    assert c["chunk_id"] == "deck-A#0"
