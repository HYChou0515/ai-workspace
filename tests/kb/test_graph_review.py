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
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.asked: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.asked.append(prompt)
        yield self.verdict, False


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
    link_resembling_entities(spec, _Judge('{"same": true, "why": "same equipment"}'))
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
    kept = erm.get(host).data
    assert isinstance(kept, GraphEntity)
    assert kept.norm_keys == [norm_surface("回焊爐")]
    assert list_proposals(spec) == []


def test_a_rejected_pair_is_never_proposed_again():
    """The whole point of asking a person is that the answer is kept. Re-proposing
    a rejected pair would re-spend the model AND put the same question back in
    front of them every week, which is how a queue stops being read."""
    spec = make_spec(default_user=lambda: "bob")
    host, other = _two_proposed(spec)
    reject_proposal(spec, host, other, by="amy")
    judge = _Judge('{"same": true, "why": "same equipment"}')
    assert link_resembling_entities(spec, judge) == 0
    assert judge.asked == []
    assert list_proposals(spec) == []


def test_an_accepted_pair_is_never_proposed_again():
    spec = make_spec(default_user=lambda: "bob")
    host, other = _two_proposed(spec)
    accept_proposal(spec, host, other, by="amy")
    judge = _Judge('{"same": true, "why": "same equipment"}')
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
