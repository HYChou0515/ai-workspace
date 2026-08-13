"""Adding what a document said to what a card already knew.

Pure, and deliberately dull: the interesting part is that it ADDS. The pipeline
this replaces picked a winner between per-document bodies, which could only lose
the others — 「蘋果是水果」 and 「蘋果是紅色」 arriving from two documents left one
of them on the floor.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...resources.kb import CardStatement


def accumulate(
    held: Sequence[CardStatement], arriving: Sequence[CardStatement]
) -> list[CardStatement]:
    """The card's evidence after a run, oldest first.

    Deduped by the CLAIM, not by the document: a corpus repeats itself — a spec
    and the deck summarising it say the same sentence — and carrying the claim
    twice invites the body to state the fact twice. The first occurrence keeps
    its provenance, which is the earliest document known to say it.

    Idempotent, because card generation is triggered by hand and re-run over the
    same corpus constantly. That is exactly how the old pipeline grew several
    cards per term.
    """
    out: list[CardStatement] = []
    seen: set[str] = set()
    for statement in [*held, *arriving]:
        if statement.text in seen:
            continue
        seen.add(statement.text)
        out.append(statement)
    return out
