"""How a graph extraction pass is cut into jobs (#534).

`_split` used to batch by DOCUMENT count, but the work a batch does is one LLM
extraction per CHUNK, and chunks-per-doc is unbounded (it follows document
length). So a 20-doc batch was anywhere from 20 to 2000 model calls, and the
long tail ran past the 30-minute ceiling our job policy allows — where the work
is thrown away and redelivered, so it never completes and burns the GPU
forever.

Batching on the chunk count makes the unit of work bounded by the thing that
actually costs time. The counts come free: `_collection_doc_ids` already asks
specstar for a per-doc `Count()` and was discarding it.
"""

from __future__ import annotations

import pytest

from workspace_app.kb.graph.batching import into_chunk_budget_batches


def test_packs_documents_until_the_chunk_budget_is_reached():
    batches = into_chunk_budget_batches([("a", 1), ("b", 1), ("c", 1)], budget=2)
    assert batches == [["a", "b"], ["c"]]


def test_a_batch_never_exceeds_the_budget_when_a_document_would_straddle_it():
    """The old doc-count rule let a batch's real cost land anywhere; the budget
    is on chunks, so a doc that would push the total over starts the next
    batch."""
    batches = into_chunk_budget_batches([("a", 2), ("b", 3), ("c", 1)], budget=4)
    assert batches == [["a"], ["b", "c"]]


def test_a_document_larger_than_the_budget_gets_a_batch_to_itself():
    """A document is ATOMIC: `write_doc_graph` wipes and rewrites both layers per
    source_doc_id, so two jobs covering one doc would delete each other's work.
    The budget therefore bounds a batch's cost from above only where it CAN —
    one oversized doc still travels alone rather than dragging neighbours into
    a job that cannot finish."""
    batches = into_chunk_budget_batches([("a", 1), ("big", 99), ("c", 1)], budget=3)
    assert batches == [["a"], ["big"], ["c"]]


def test_an_oversized_document_does_not_swallow_the_documents_after_it():
    batches = into_chunk_budget_batches([("big", 99), ("a", 1), ("b", 1)], budget=3)
    assert batches == [["big"], ["a", "b"]]


def test_no_documents_produces_no_jobs():
    assert into_chunk_budget_batches([], budget=5) == []


def test_a_document_with_no_chunks_still_travels():
    """A doc whose chunks were deleted between the split and the count still
    needs its (empty) extraction so a stale graph gets wiped."""
    assert into_chunk_budget_batches([("a", 0), ("b", 0)], budget=2) == [["a", "b"]]


@pytest.mark.parametrize("budget", [0, -1])
def test_a_non_positive_budget_is_refused(budget: int):
    """Silently treating it as 1 would hide a misconfiguration behind a pass
    that takes hours; 0 would loop forever."""
    with pytest.raises(ValueError):
        into_chunk_budget_batches([("a", 1)], budget=budget)
