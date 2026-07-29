"""Cutting a graph extraction pass into jobs that can actually finish (#534).

The pass used to be split by DOCUMENT count. The work a batch does is one LLM
extraction per CHUNK (`write_doc_graph`), and chunks-per-doc is unbounded — it
follows document length — so a fixed doc count bought no bound at all: the same
20-doc batch was 20 model calls over short notes and 2000 over long decks. The
long tail ran past the 30-minute ceiling the job policy allows, and a job killed
there is redelivered rather than finished, so it retries forever and the GPU
time is spent twice over with nothing to show.

Budgeting CHUNKS makes the unit of work bounded by the thing that costs the
time. The counts are free: `_collection_doc_ids` already asks specstar for a
per-doc `Count()` and used to throw it away.

A document is ATOMIC here. `write_doc_graph` wipes and rewrites both layers per
`source_doc_id`, so splitting one document across two jobs would have them
delete each other's work. One document larger than the whole budget therefore
still travels alone — the budget bounds what it can, and an oversized document
at least stops dragging its neighbours into a job that cannot finish.
"""

from __future__ import annotations


def into_chunk_budget_batches(docs: list[tuple[str, int]], budget: int) -> list[list[str]]:
    """Group ``(doc_id, chunk_count)`` pairs into batches of at most ``budget``
    chunks, preserving order.

    A document whose own count exceeds ``budget`` becomes a batch by itself
    rather than being split (see the module docstring on atomicity).
    """
    if budget <= 0:
        # Treating this as 1 would bury a misconfiguration under a pass that
        # takes hours; 0 would never make progress at all.
        raise ValueError("budget must be positive")

    batches: list[list[str]] = []
    current: list[str] = []
    used = 0
    for doc_id, chunks in docs:
        if current and used + chunks > budget:
            batches.append(current)
            current, used = [], 0
        current.append(doc_id)
        used += chunks
        if used >= budget:
            batches.append(current)
            current, used = [], 0
    if current:
        batches.append(current)
    return batches
