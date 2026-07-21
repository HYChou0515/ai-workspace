"""#534 B — the one basis that needs a person.

A resemblance points at nothing outside the model, so it is never applied: it is
PROPOSED, and someone decides. Everything about this pass is shaped by that being
the expensive path — candidates are narrowed by cheap rules first, pairs a rule
can already settle never reach the model, and the whole pass is one call the
reconcile makes, so commenting out that line turns it off without touching
anything else.
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB, SpecStar

from workspace_app.kb.graph.link import (
    link_identical_mentions,
    link_resembling_entities,
    reconcile_vocabulary,
)
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphEntityLink, GraphMention, mention_id
from workspace_app.resources.kb import Collection


class _Judge(ILlm):
    """Stands in for the model, and counts how many times it was called — the
    number that decides whether this pass is affordable."""

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


def _mention(spec: SpecStar, cid: str, doc: str, surface: str) -> None:
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("bob"):
        rm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id=doc,
                surface=surface,
                norm_surface=norm_surface(surface),
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id(doc, surface),
        )


def _links(spec: SpecStar, state: str) -> list[GraphEntityLink]:
    rm = spec.get_resource_manager(GraphEntityLink)
    out = []
    for r in rm.list_resources((QB["state"] == state).build()):
        assert isinstance(r.data, GraphEntityLink)
        out.append(r.data)
    return out


def test_an_accepted_resemblance_is_proposed_never_applied():
    """The model saying yes is not the decision. Nothing merges until a person
    acts, which is the entire reason this basis is separated from the other three."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "回焊機")
    link_identical_mentions(spec)

    judge = _Judge(
        '{"groups": [{"names": ["回焊爐", "回焊機"], "why": "both name the reflow equipment"}]}'
    )
    assert link_resembling_entities(spec, judge) == 1
    pending = _links(spec, "pending")
    assert len(pending) == 1
    assert pending[0].basis == "resembles"
    assert "reflow equipment" in pending[0].evidence
    # the identities are untouched until someone accepts
    assert len(_links(spec, "active")) == 2


def test_a_refused_resemblance_leaves_nothing_behind():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "回焊機")
    link_identical_mentions(spec)
    assert link_resembling_entities(spec, _Judge('{"groups": []}')) == 0
    assert _links(spec, "pending") == []


def test_the_whole_vocabulary_costs_one_call_not_one_per_pair():
    """Why no cheap test decides who gets asked: asking pair by pair costs N²
    calls, which is what made a filter necessary, and every filter over spelling
    met an exception within a corpus or two — character overlap admitted
    "condition" against "dose", a digit rule refused 第2型糖尿病 against
    第二型糖尿病. A batch costs one call and asks the question that was meant."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    for i, name in enumerate(["回焊爐", "回焊機", "錫膏印刷", "SPI", "空洞", "假焊"]):
        _mention(spec, cid, f"deck-{i}", name)
    link_identical_mentions(spec)
    judge = _Judge('{"groups": []}')
    link_resembling_entities(spec, judge)
    assert len(judge.asked) == 1


def test_a_group_naming_something_nobody_wrote_is_ignored():
    """A model listing a term that is not in the vocabulary has invented it, and
    an invented name cannot be evidence for anything."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "回焊機")
    link_identical_mentions(spec)
    judge = _Judge('{"groups": [{"names": ["回焊爐", "熱風爐"], "why": "made up"}]}')
    assert link_resembling_entities(spec, judge) == 0


def test_terms_that_differ_only_by_a_number_are_the_model_s_call_now():
    """There is no rule here refusing them. Every rule of that shape had an
    exception — the one that would have vetoed 500mg against 850mg also vetoed
    第2型糖尿病 against 第二型糖尿病 — so the question goes to something that can
    tell the two cases apart, and its answer is a proposal either way."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "第2型糖尿病")
    _mention(spec, cid, "deck-B", "第二型糖尿病")
    link_identical_mentions(spec)
    judge = _Judge('{"groups": [{"names": ["第2型糖尿病", "第二型糖尿病"], "why": "同一個疾病"}]}')
    assert link_resembling_entities(spec, judge) == 1
    assert len(_links(spec, "pending")) == 1


def test_proposing_the_same_pair_twice_changes_nothing():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "回焊機")
    link_identical_mentions(spec)
    judge = _Judge('{"groups": [{"names": ["回焊爐", "回焊機"], "why": "same equipment"}]}')
    link_resembling_entities(spec, judge)
    before = len(_links(spec, "pending"))
    link_resembling_entities(spec, judge)
    assert len(_links(spec, "pending")) == before


def test_the_reconcile_runs_without_a_model_at_all():
    """The line that asks a model is one line. Passing no model skips it — the same
    thing commenting that line out does — and the deterministic bases still run."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "回焊爐")
    reconcile_vocabulary(spec, llm=None)
    assert len(_links(spec, "active")) == 2
    assert _links(spec, "pending") == []


def test_a_kind_can_be_proposed_even_though_nothing_mentions_it():
    """A kind and a predicate are identities like any other — that was the claim.
    But a proposal was recorded as pending links over the other side's MENTIONS,
    and a kind has none: nothing mentions "機台", things are labelled with it. So
    kinds could never be proposed at all, and the taxonomy stayed split by
    language while the design said otherwise."""
    from workspace_app.kb.graph.link import name_predicates
    from workspace_app.kb.graph.review import list_proposals

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    rm = spec.get_resource_manager(GraphMention)
    for doc, surface, kind in (("deck-A", "回焊爐", "機台"), ("deck-B", "Reflow Oven", "tool")):
        with rm.using("bob"):
            rm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=doc,
                    surface=surface,
                    norm_surface=norm_surface(surface),
                    kind=kind,
                    norm_kind=norm_surface(kind),
                    collection_visibility="public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(doc, surface),
            )
    link_identical_mentions(spec)
    name_predicates(spec)
    judge = _Judge('{"groups": [{"names": ["機台", "tool"], "why": "同一個類型的中英文"}]}')
    assert link_resembling_entities(spec, judge) == 1
    (proposal,) = list_proposals(spec)
    assert {proposal.name, proposal.other_name} == {"機台", "tool"}


def test_a_kind_proposal_is_visible_to_someone_who_can_read_its_evidence():
    """The proposal existed but nobody could see it: the link was created without
    the collections its evidence lives in, and an empty list is what the scope
    reads as "nothing vouches for this". Third row in this slice created without
    one — the rule is right, the omission keeps being mine."""
    from workspace_app.kb.graph.review import list_proposals

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    rm = spec.get_resource_manager(GraphMention)
    for doc, surface, kind in (("deck-A", "回焊爐", "機台"), ("deck-B", "Reflow Oven", "tool")):
        with rm.using("bob"):
            rm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=doc,
                    surface=surface,
                    norm_surface=norm_surface(surface),
                    kind=kind,
                    norm_kind=norm_surface(kind),
                    collection_visibility="public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(doc, surface),
            )
    link_identical_mentions(spec)
    judge = _Judge('{"groups": [{"names": ["機台", "tool"], "why": "同一個類型"}]}')
    link_resembling_entities(spec, judge)
    assert len(list_proposals(spec, as_user="bob")) == 1
