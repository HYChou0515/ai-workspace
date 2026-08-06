"""#630 P4 — one pass over a chunk writes BOTH layers.

The primary layer (what a document mentions) and its statements were two prompts
over the same text. That cost double the model time on every chunk of every deck
— the single most expensive thing the graph does — and it split the signal: the
pass that decided what the passage talks about and the pass that decided what it
says about those things never saw each other's answers, so a subject could be
named in one and a stranger to the other.

``write_doc_graph`` extracts once and hands the result to both writers.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from specstar import QB
from specstar.types import Binary

from workspace_app.kb.graph.doc_write import write_doc_graph
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphMention
from workspace_app.resources.kb import Collection, SourceDoc

_REPLY = (
    '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
    ' "aliases": [], "relationships": [],'
    ' "attributes": [{"subject": "回焊爐", "attribute": "良率", "value": "98.7",'
    ' "unit": "%", "period": "Q3"}]}'
)


class _CountingLlm(ILlm):
    def __init__(self, reply: str = _REPLY) -> None:
        self._reply = reply
        self.calls = 0

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.calls += 1
        yield self._reply, False


def _deck(spec, *, cid: str = "c1", doc_id: str = "deck-A") -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        crm.create(Collection(name="c"), resource_id=cid)
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            ),
            resource_id=doc_id,
        )
    return doc_id


def _rows(spec, model, doc: str):
    rm = spec.get_resource_manager(model)
    return [r.data for r in rm.list_resources((QB["source_doc_id"] == doc).build())]


def test_one_model_call_per_chunk_writes_both_layers():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    llm = _CountingLlm()

    write_doc_graph(
        spec,
        llm,
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t0"), ("deck-A#1", "t1")],
    )

    assert llm.calls == 2  # two chunks, ONE pass each — not two passes each
    assert [m.surface for m in _rows(spec, GraphMention, "deck-A")] == ["回焊爐"]
    claims = _rows(spec, GraphClaim, "deck-A")
    assert len(claims) == 2  # one per chunk
    assert {c.subject for c in claims} == {"回焊爐"}
    assert {c.attribute for c in claims} == {"良率"}


def test_re_running_replaces_both_layers_rather_than_doubling_them():
    """Both writers wipe-then-rewrite per doc, so tuning the prompt and re-running
    never accumulates — the property has to survive the merge."""
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    for _ in range(2):
        write_doc_graph(
            spec,
            _CountingLlm(),
            collection_id="c1",
            source_doc_id="deck-A",
            chunks=[("deck-A#0", "t")],
        )
    assert len(_rows(spec, GraphMention, "deck-A")) == 1
    assert len(_rows(spec, GraphClaim, "deck-A")) == 1


def test_a_deck_that_vanished_mid_run_leaves_nothing_behind():
    """Chunks outlive their deck (#104). A vanished deck has no permission to
    inherit and nothing worth recording — both layers wipe and skip rather than
    failing the batch its neighbours ride in."""
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_graph(
        spec,
        _CountingLlm(),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    spec.get_resource_manager(SourceDoc).permanently_delete("deck-A")

    write_doc_graph(
        spec,
        _CountingLlm(),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    assert _rows(spec, GraphMention, "deck-A") == []
    assert _rows(spec, GraphClaim, "deck-A") == []


class _DyingLlm(ILlm):
    """A model call that dies the way a killed job dies — part way through a
    document, with no chance to write anything back."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        raise RuntimeError("the job was redelivered / the pod went away")
        yield "", False  # pragma: no cover — unreachable, satisfies the protocol


def test_a_re_extraction_that_dies_leaves_the_previous_one_standing():
    """#697 P9: the wipe must not open before the model has answered.

    Extraction is minutes of model time per document and the job it rides in has
    a 30-minute ceiling it is known to hit (#670) — so "the run did not finish"
    is a case that HAPPENS, not a hypothetical. Clearing the document first makes
    every one of those a deletion: the rows are gone, nothing replaces them, and
    the vocabulary pass then recomputes over the hole and drops the identities
    too. A redelivery usually repairs it; a document that times out every time
    does not get repaired, and until it is, the graph is missing evidence it
    already had.

    The previous answer is worth more than no answer, so the destructive step
    waits until there is something to put in its place.
    """
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_graph(
        spec,
        _CountingLlm(),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    assert len(_rows(spec, GraphMention, "deck-A")) == 1

    with pytest.raises(RuntimeError):
        write_doc_graph(
            spec,
            _DyingLlm(),
            collection_id="c1",
            source_doc_id="deck-A",
            chunks=[("deck-A#0", "t")],
        )

    assert [m.surface for m in _rows(spec, GraphMention, "deck-A")] == ["回焊爐"]
    assert len(_rows(spec, GraphClaim, "deck-A")) == 1
