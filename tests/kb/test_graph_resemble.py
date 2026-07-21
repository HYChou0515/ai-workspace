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
    """Answers every adjudication the same way, and counts how often it was asked."""

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

    judge = _Judge('{"same": true, "why": "both name the reflow equipment"}')
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
    assert link_resembling_entities(spec, _Judge('{"same": false, "why": "different"}')) == 0
    assert _links(spec, "pending") == []


def test_pairs_a_rule_already_settles_never_reach_the_model():
    """The model is the expensive part and the only part that can be wrong without
    saying so. Numbers that disagree are vetoed before anyone is asked — spending a
    call, and then a person's attention, to reject RO-3 against RO-4 is waste."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "RO-3")
    _mention(spec, cid, "deck-B", "RO-4")
    link_identical_mentions(spec)
    judge = _Judge('{"same": true, "why": "look alike"}')
    assert link_resembling_entities(spec, judge) == 0
    assert judge.asked == []


def test_unrelated_names_never_reach_the_model_either():
    """Every pair would be too many calls, so a cheap overlap test narrows the
    field first. Missing a pair here costs a merge nobody proposed — visible as two
    entries — while asking about everything costs money on every run."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "錫膏印刷")
    link_identical_mentions(spec)
    judge = _Judge('{"same": true, "why": "sure"}')
    link_resembling_entities(spec, judge)
    assert judge.asked == []


def test_proposing_the_same_pair_twice_changes_nothing():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "回焊機")
    link_identical_mentions(spec)
    judge = _Judge('{"same": true, "why": "same equipment"}')
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
