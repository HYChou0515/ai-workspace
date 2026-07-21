"""#534 B — deciding the proposals, and reading the result.

The review queue is the only place a person is asked to spend attention, so the
rules around it are about not wasting it: a decision is remembered, and a
question already answered is never asked again.
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB, SpecStar

from workspace_app.kb.graph.link import link_identical_mentions, link_resembling_entities
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.kb.graph.review import (
    accept_proposal,
    entity_page,
    list_proposals,
    reject_proposal,
)
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphEntity, GraphMention, mention_id
from workspace_app.resources.kb import Collection


class _Judge(ILlm):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.asked: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.asked.append(prompt)
        yield self.reply, False


def _collection(spec: SpecStar) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using("bob"):
        return rm.create(Collection(name="c")).resource_id


def _mention(
    spec: SpecStar, cid: str, doc: str, surface: str, *, n: int = 1, private: bool = False
) -> None:
    """The mirror must match the collection it claims to come from — it is the ONLY
    thing the scope reads, so a fixture that hardcodes "public" tests nothing."""
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("bob"):
        rm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id=doc,
                surface=surface,
                norm_surface=norm_surface(surface),
                occurrences=n,
                chunk_ids=[f"{doc}#0"],
                collection_visibility="private" if private else "public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id(doc, surface),
        )


def _two_proposed(spec: SpecStar) -> tuple[str, str]:
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐", n=4)
    _mention(spec, cid, "deck-B", "回焊機")
    link_identical_mentions(spec)
    link_resembling_entities(
        spec, _Judge('{"groups": [{"names": ["回焊爐", "回焊機"], "why": "same equipment"}]}')
    )
    (proposal,) = list_proposals(spec)
    return proposal.entity_id, proposal.proposed_from


def test_a_proposal_shows_both_sides_and_what_it_rested_on():
    """A reviewer needs the two names and the reason in front of them; going and
    fetching each side would make the queue useless at any volume."""
    spec = make_spec(default_user=lambda: "bob")
    _two_proposed(spec)
    (proposal,) = list_proposals(spec)
    assert {proposal.name, proposal.other_name} == {"回焊爐", "回焊機"}
    assert proposal.why == "same equipment"


def test_accepting_merges_the_two_and_records_who_said_so():
    spec = make_spec(default_user=lambda: "bob")
    host, other = _two_proposed(spec)
    accept_proposal(spec, host, other, by="amy")

    erm = spec.get_resource_manager(GraphEntity)
    kept = erm.get(host).data
    assert isinstance(kept, GraphEntity)
    assert sorted(kept.norm_keys) == sorted([norm_surface("回焊爐"), norm_surface("回焊機")])
    page = entity_page(spec, host, as_user="bob")
    assert {m.surface for m in page.mentions} == {"回焊爐", "回焊機"}
    assert {link.basis for link in page.links} == {"approved"}
    assert all(link.evidence == "amy" for link in page.links)
    assert list_proposals(spec) == []


def test_rejecting_leaves_both_identities_alone():
    spec = make_spec(default_user=lambda: "bob")
    host, other = _two_proposed(spec)
    reject_proposal(spec, host, other, by="amy")
    erm = spec.get_resource_manager(GraphEntity)
    # neither side moved: each identity still holds exactly its own name. Which
    # of the two the proposal happened to name as host is not the point.
    for eid in (host, other):
        kept = erm.get(eid).data
        assert isinstance(kept, GraphEntity)
        assert len(kept.norm_keys) == 1
    assert list_proposals(spec) == []


def test_a_rejected_pair_is_never_proposed_again():
    """The whole point of asking a person is that the answer is kept. A rejected
    pair coming back next week would put the same question in front of the same
    person every run, which is how a queue stops being read.

    The model is still asked — a batch is one call whatever it contains, so
    excluding decided pairs from the prompt would buy nothing and would break the
    groups it reasons over. What must not happen is the PROPOSAL returning."""
    spec = make_spec(default_user=lambda: "bob")
    host, other = _two_proposed(spec)
    reject_proposal(spec, host, other, by="amy")
    judge = _Judge('{"groups": [{"names": ["回焊爐", "回焊機"], "why": "same equipment"}]}')
    assert link_resembling_entities(spec, judge) == 0
    assert list_proposals(spec) == []


def test_an_accepted_pair_is_never_proposed_again():
    spec = make_spec(default_user=lambda: "bob")
    host, other = _two_proposed(spec)
    accept_proposal(spec, host, other, by="amy")
    judge = _Judge('{"groups": [{"names": ["回焊爐", "回焊機"], "why": "same equipment"}]}')
    assert link_resembling_entities(spec, judge) == 0
    assert list_proposals(spec) == []


def test_an_entity_page_gathers_the_evidence_across_documents():
    """What the whole slice was for: one thing, everything said about it, and where
    each piece came from."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐", n=4)
    _mention(spec, cid, "deck-B", "回焊爐", n=1)
    link_identical_mentions(spec)
    erm = spec.get_resource_manager(GraphEntity)
    (row,) = list(erm.list_resources(QB.all().build()))
    page = entity_page(spec, row.info.resource_id, as_user="bob")  # ty: ignore[unresolved-attribute]
    assert page.entity.canonical_name == "回焊爐"
    assert sorted(m.source_doc_id for m in page.mentions) == ["deck-A", "deck-B"]
    assert page.occurrences == 5


def _private_collection(spec: SpecStar) -> str:
    from workspace_app.perm import Permission

    rm = spec.get_resource_manager(Collection)
    with rm.using("bob"):
        return rm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id


def test_a_reader_sees_only_the_evidence_they_may_read():
    """The page is assembled from scoped reads, so the filtering is specstar's
    access_scope doing its one job — not a second copy of the permission rules
    written here, which would be a rule to keep in step and therefore a leak
    waiting to happen."""
    spec = make_spec(default_user=lambda: "bob")
    open_cid = _collection(spec)
    secret_cid = _private_collection(spec)
    _mention(spec, open_cid, "deck-A", "回焊爐", n=2)
    _mention(spec, secret_cid, "deck-S", "回焊爐", n=9, private=True)
    link_identical_mentions(spec)
    erm = spec.get_resource_manager(GraphEntity)
    (row,) = list(erm.list_resources(QB.all().build()))
    eid = row.info.resource_id  # ty: ignore[unresolved-attribute]

    owner = entity_page(spec, eid, as_user="bob")
    assert sorted(m.source_doc_id for m in owner.mentions) == ["deck-A", "deck-S"]

    outsider = entity_page(spec, eid, as_user="alice")
    assert [m.source_doc_id for m in outsider.mentions] == ["deck-A"]
    assert outsider.occurrences == 2


def test_an_entity_with_no_readable_evidence_is_not_a_page_at_all():
    """Not an empty page — the identity itself is hidden, because a bare name can
    leak. The scope on the entity is what answers this, before any assembly."""
    import pytest
    from specstar.types import ResourceIDNotFoundError

    spec = make_spec(default_user=lambda: "bob")
    secret_cid = _private_collection(spec)
    _mention(spec, secret_cid, "deck-S", "機密製程", private=True)
    link_identical_mentions(spec)
    erm = spec.get_resource_manager(GraphEntity)
    (row,) = list(erm.list_resources(QB.all().build()))
    eid = row.info.resource_id  # ty: ignore[unresolved-attribute]
    with pytest.raises(ResourceIDNotFoundError):
        entity_page(spec, eid, as_user="alice")


def test_a_link_is_hidden_from_someone_who_cannot_read_its_evidence():
    """A link is not neutral bookkeeping — a declared one carries a sentence
    quoted out of a document. Without a gate of its own, the auto-CRUD route hands
    that sentence to anyone, whatever the entity page does."""
    import pytest
    from specstar.types import ResourceIDNotFoundError

    from workspace_app.resources.graph import GraphEntityLink

    spec = make_spec(default_user=lambda: "bob")
    secret_cid = _private_collection(spec)
    _mention(spec, secret_cid, "deck-S", "機密製程", private=True)
    link_identical_mentions(spec)
    lrm = spec.get_resource_manager(GraphEntityLink)
    (row,) = list(lrm.list_resources(QB.all().build()))
    lid = row.info.resource_id  # ty: ignore[unresolved-attribute]

    with lrm.using("bob", apply_access_scope=True):  # ty: ignore[unknown-argument]
        assert lrm.get(lid).data is not None
    with (
        lrm.using("alice", apply_access_scope=True),  # ty: ignore[unknown-argument]
        pytest.raises(ResourceIDNotFoundError),
    ):
        lrm.get(lid)


def _relationship(spec, cid: str, doc: str, subj: str, pred: str, obj: str, *, private=False):
    from workspace_app.resources.graph import GraphRelationship, relationship_id

    rm = spec.get_resource_manager(GraphRelationship)
    with rm.using("bob"):
        rm.create(
            GraphRelationship(
                collection_id=cid,
                source_doc_id=doc,
                subject=subj,
                predicate=pred,
                object=obj,
                norm_subject=norm_surface(subj),
                norm_predicate=norm_surface(pred),
                norm_object=norm_surface(obj),
                chunk_id=f"{doc}#0",
                quote=f"{subj}{pred}{obj}",
                collection_visibility="private" if private else "public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=relationship_id(doc, f"{doc}#0", subj, pred, obj),
        )


def _entity_id_named(spec, name: str) -> str:
    erm = spec.get_resource_manager(GraphEntity)
    for r in erm.list_resources(QB.all().build()):
        assert isinstance(r.data, GraphEntity)
        if r.data.canonical_name == name:
            return r.info.resource_id
    raise AssertionError(f"no entity named {name}")


def test_the_page_shows_what_the_thing_connects_to():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-A", "空洞")
    _relationship(spec, cid, "deck-A", "回焊爐", "造成", "空洞")
    link_identical_mentions(spec)

    page = entity_page(spec, _entity_id_named(spec, "回焊爐"), as_user="bob")
    (rel,) = page.related
    assert (rel.direction, rel.predicate, rel.other_name) == ("out", "造成", "空洞")
    assert rel.other_entity_id == _entity_id_named(spec, "空洞")
    assert rel.quote == "回焊爐造成空洞"


def test_the_other_end_sees_the_same_connection_pointing_back():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-A", "空洞")
    _relationship(spec, cid, "deck-A", "回焊爐", "造成", "空洞")
    link_identical_mentions(spec)

    page = entity_page(spec, _entity_id_named(spec, "空洞"), as_user="bob")
    (rel,) = page.related
    assert (rel.direction, rel.other_name) == ("in", "回焊爐")


def test_a_connection_written_in_another_language_lands_on_the_same_page():
    """The payoff of the vocabulary layer. An English deck states the connection
    using "Reflow Oven"; a reader on the 回焊爐 page still sees it, because the
    ends are resolved through the identity rather than matched as strings."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "Reflow Oven")
    _mention(spec, cid, "deck-B", "void")
    _relationship(spec, cid, "deck-B", "Reflow Oven", "causes", "void")
    link_identical_mentions(spec)
    # the vocabulary learns the two names are one thing
    host = _entity_id_named(spec, "回焊爐")
    other = _entity_id_named(spec, "Reflow Oven")
    accept_proposal(spec, host, other, by="amy")

    page = entity_page(spec, host, as_user="bob")
    assert [r.predicate for r in page.related] == ["causes"]


def test_a_connection_from_an_unreadable_document_never_appears():
    spec = make_spec(default_user=lambda: "bob")
    open_cid = _collection(spec)
    secret_cid = _private_collection(spec)
    _mention(spec, open_cid, "deck-A", "回焊爐")
    _relationship(spec, secret_cid, "deck-S", "回焊爐", "造成", "機密缺陷", private=True)
    link_identical_mentions(spec)
    eid = _entity_id_named(spec, "回焊爐")
    assert entity_page(spec, eid, as_user="bob").related != []
    assert entity_page(spec, eid, as_user="alice").related == []


def test_a_predicate_is_an_identity_like_everything_else():
    """ "造成" and "leads to" are one connection written two ways. They go through
    the same pipeline as a thing and a kind — so once the vocabulary joins them,
    every page shows one predicate instead of two, and no separate mechanism had
    to be built to make that happen."""
    from workspace_app.kb.graph.link import reconcile_vocabulary

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-A", "空洞")
    _relationship(spec, cid, "deck-A", "回焊爐", "造成", "空洞")
    _relationship(spec, cid, "deck-B", "回焊爐", "leads to", "空洞")
    reconcile_vocabulary(spec, llm=None)

    zh = _entity_id_named(spec, "造成")
    en = _entity_id_named(spec, "leads to")
    page = entity_page(spec, _entity_id_named(spec, "回焊爐"), as_user="bob")
    assert sorted(r.predicate for r in page.related) == ["leads to", "造成"]

    accept_proposal(spec, zh, en, by="amy")
    page = entity_page(spec, _entity_id_named(spec, "回焊爐"), as_user="bob")
    assert sorted(r.predicate for r in page.related) == ["造成", "造成"]


def test_a_predicate_the_vocabulary_has_not_reached_still_reads():
    """Until the pass runs, the page falls back to the words the document used.
    A connection is worth showing before it is tidy."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _relationship(spec, cid, "deck-A", "回焊爐", "造成", "空洞")
    link_identical_mentions(spec)
    page = entity_page(spec, _entity_id_named(spec, "回焊爐"), as_user="bob")
    assert [r.predicate for r in page.related] == ["造成"]


def test_a_proposal_carries_what_each_side_actually_looked_like():
    """Measured against a real model, the reason field is the least trustworthy
    thing in the queue: it justified merging two DIFFERENT machines with a sentence
    that read perfectly and described only one of them. A reviewer given that and
    nothing else approves it.

    So each side arrives with the documents it came from and the words around it.
    Those are the documents' own sentences — the reviewer judges the evidence
    rather than the model's account of it."""
    from workspace_app.resources.kb import DocChunk

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    crm = spec.get_resource_manager(DocChunk)
    for doc, text in (
        ("deck-C", "錫膏印刷機(Stencil Printer)壓力不足會造成錫量不足"),
        ("deck-D", "Stencil Printer pressure is monitored by SPI"),
    ):
        with crm.using("bob"):
            crm.create(
                DocChunk(collection_id=cid, source_doc_id=doc, seq=0, start=0, end=1, text=text),
                resource_id=f"{doc}#0",
            )
    _mention(spec, cid, "deck-C", "錫膏印刷機")
    _mention(spec, cid, "deck-D", "SPI")
    link_identical_mentions(spec)
    link_resembling_entities(
        spec,
        _Judge(
            '{"groups": [{"names": ["SPI", "錫膏印刷機"], "why": "a machine that prints paste"}]}'
        ),
    )
    (proposal,) = list_proposals(spec, as_user="bob")
    seen = {(e.source_doc_id, e.text) for e in proposal.evidence + proposal.other_evidence}
    assert ("deck-D", "Stencil Printer pressure is monitored by SPI") in seen
    assert ("deck-C", "錫膏印刷機(Stencil Printer)壓力不足會造成錫量不足") in seen
