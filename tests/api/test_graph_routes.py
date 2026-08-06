"""#534 B — the graph reaches the outside.

Three reads and two decisions. Every one of them runs as the caller, so the
filtering is the access scope doing its job rather than a second copy of the
permission rules living in the route layer — the copy is what drifts, and a
permission rule that drifts is a leak.
"""

from __future__ import annotations

import asyncio
from typing import Any

from specstar import QB, SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app, kb_routes
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.graph.link import reconcile_vocabulary
from workspace_app.kb.graph.normalize import norm_attribute, norm_surface
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import (
    GraphClaim,
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    link_id,
    mention_id,
)
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
    reconcile_vocabulary(spec, llm=None)
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
    from workspace_app.kb.graph.link import reconcile_vocabulary
    from workspace_app.kb.graph.review import accept_proposal

    reconcile_vocabulary(spec, llm=None)
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


def test_the_entity_response_reads_the_statement_table_from_both_ends():
    """#630 P5 over HTTP: a value some document also discusses is an identity,
    and its page answers "who has this as a value" (「這個 recipe 被哪些機台使用」)."""
    from workspace_app.kb.graph.normalize import norm_attribute

    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    crm = spec.get_resource_manager(Collection)
    cid = next(iter(crm.list_resources(QB.all().build()))).info.resource_id  # ty: ignore[unresolved-attribute]
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id="deck-R",
                surface="PPOOIXUX",
                norm_surface=norm_surface("PPOOIXUX"),
                kind="recipe",
                norm_kind=norm_surface("recipe"),
                occurrences=1,
                chunk_ids=["deck-R#0"],
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-R", "PPOOIXUX"),
        )
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("bob"):
        rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                chunk_id="deck-A#0",
                norm_subject=norm_surface("回焊爐"),
                subject="回焊爐",
                norm_attribute=norm_attribute("recipe"),
                attribute="recipe",
                value="PPOOIXUX",
                norm_value=norm_surface("PPOOIXUX"),
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        )
    reconcile_vocabulary(spec, llm=None)

    rid = _entity_id(spec, "PPOOIXUX")
    body = client.get(f"/kb/graph/entities/{rid}").json()
    (held,) = body["value_of"]
    assert held["subject"] == "回焊爐"
    assert held["attribute"] == "recipe"
    assert held["source_doc_id"] == "deck-A"
    assert body["claims"] == []  # the recipe itself has no attributes stated


def _pending_pair(spec: SpecStar) -> tuple[str, str]:
    """Two identities with a merge proposal waiting between them."""
    row = next(iter(spec.get_resource_manager(Collection).list_resources(QB.all().build())))
    collection_id = row.info.resource_id  # ty: ignore[unresolved-attribute]
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=collection_id,
                source_doc_id="deck-C",
                surface="Reflow Oven",
                norm_surface=norm_surface("Reflow Oven"),
                kind="機台",
                norm_kind=norm_surface("機台"),
                occurrences=1,
                chunk_ids=["deck-C#0"],
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-C", "Reflow Oven"),
        )
    reconcile_vocabulary(spec, llm=None)
    host, other = _entity_id(spec, "回焊爐"), _entity_id(spec, "Reflow Oven")
    mid = mention_id("deck-C", "Reflow Oven")
    lrm = spec.get_resource_manager(GraphEntityLink)
    with lrm.using("bob"):
        lrm.create(
            GraphEntityLink(
                entity_id=host,
                mention_id=mid,
                basis="resembles",
                evidence="the machine that reflows solder",
                state="pending",
                proposed_from=other,
                collection_ids=[collection_id],
            ),
            resource_id=link_id(host, mid, "resembles", other),
        )
    return host, other


def _loop_spy(recorded: list[bool]):
    def spy(*_args: object, **_kwargs: object) -> None:
        # A thread that has a running loop IS the loop's thread. Blocking work
        # there stops every other request on the pod, which is the whole reason
        # this file already reaches for `asyncio.to_thread` a dozen times.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            recorded.append(False)
        else:
            recorded.append(True)

    return spy


def test_accepting_a_merge_does_not_block_the_event_loop(monkeypatch) -> None:
    """#697 P10 — accepting a proposal re-derives the vocabulary over the WHOLE
    corpus (P8, deliberately: one implementation of the merge, not two). That is
    job-scale work — every mention, relationship and link read, recomputed and
    written — and it ran straight from an ``async def``, where it holds the loop
    and every other request on the pod waits behind one person's click.
    """
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    host, other = _pending_pair(spec)

    recorded: list[bool] = []
    monkeypatch.setattr(kb_routes, "accept_proposal", _loop_spy(recorded))
    r = client.post(f"/kb/graph/proposals/{host}/accept", params={"other": other})

    assert r.status_code == 200, r.text
    assert recorded, "the route never called accept_proposal"
    assert not any(recorded)


def test_rejecting_a_merge_does_not_block_the_event_loop(monkeypatch) -> None:
    """Same rule: it is two scans of every pending link in the corpus — the queue
    read once to find the pair and once to mark it."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    host, other = _pending_pair(spec)

    recorded: list[bool] = []
    monkeypatch.setattr(kb_routes, "reject_proposal", _loop_spy(recorded))
    r = client.post(f"/kb/graph/proposals/{host}/reject", params={"other": other})

    assert r.status_code == 200, r.text
    assert recorded, "the route never called reject_proposal"
    assert not any(recorded)


def _watch_loop(monkeypatch, recorded: list[bool]) -> None:
    """Record, for every `list_proposals` the request makes, whether it ran on
    the loop — then let the real one answer."""
    real = kb_routes.list_proposals

    def spy(*args: Any, **kwargs: Any):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            recorded.append(False)
        else:
            recorded.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(kb_routes, "list_proposals", spy)


def test_finding_the_proposal_is_off_the_loop_too(monkeypatch) -> None:
    """#697 P17 — the first version moved the decision and left the LOOKUP.

    `_require_proposal` scans every pending link in the corpus and, per pair,
    fetches both identities and their evidence. On any queue worth reviewing
    that is the larger half of the request, and it ran on the loop — so the
    route still held the pod while one person's click was served, and the tests
    could not see it because they asserted which function was moved rather than
    that nothing blocking was left behind.
    """
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    host, other = _pending_pair(spec)

    recorded: list[bool] = []
    _watch_loop(monkeypatch, recorded)
    r = client.post(f"/kb/graph/proposals/{host}/accept", params={"other": other})

    assert r.status_code == 200, r.text
    assert recorded, "the route never looked the proposal up"
    assert not any(recorded)


def test_finding_the_proposal_is_off_the_loop_when_rejecting_too(monkeypatch) -> None:
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _seed(spec)
    host, other = _pending_pair(spec)

    recorded: list[bool] = []
    _watch_loop(monkeypatch, recorded)
    r = client.post(f"/kb/graph/proposals/{host}/reject", params={"other": other})

    assert r.status_code == 200, r.text
    assert recorded, "the route never looked the proposal up"
    assert not any(recorded)
