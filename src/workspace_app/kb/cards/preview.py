"""Run the card criterion over a folder of text files and write the result.

The tuning half of the loop. Once documents have been drawn out of the corpus
once, trying a criterion should cost a model call per document and nothing else
— no database, no permissions, no environment that has to be reachable.

The sample folder is the SAME one `graph_preview --dump-samples` writes. That is
deliberate: the two pipelines stay independent in what they extract, but reading
the same documents is what makes their results comparable at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msgspec

from ..llm import ILlm
from .build import Build, Card, DocSource, build


def read_samples(sample_dir: Path, *, only: set[str] | None = None) -> list[DocSource]:
    """Every `.txt` in the folder, in a stable order — or just the named ones.

    ``only`` is how a tuning round reads a mini-batch: fewer model calls per
    round, and a different draw each round so the prompt cannot settle into one
    fixed set of documents.
    """
    return [
        DocSource(doc_id=path.stem, text=path.read_text(encoding="utf-8"))
        for path in sorted(sample_dir.glob("*.txt"))
        if only is None or path.stem in only
    ]


def summarise(built: Build, docs: list[DocSource]) -> dict[str, Any]:
    """The numbers a person checks before reading anything.

    ``statements_per_card`` is the one to watch: a card resting on a single
    statement from a single document is the shape that used to proliferate —
    several thin cards about one term instead of one that accumulated.
    """
    cards = built.cards
    statements = sum(len(c.statements) for c in cards)
    return {
        "documents": len(docs),
        # What the model put forward, and how much of it was really in the text.
        # The rate is the direct measurement of inventing — see `Build`.
        "statements_offered": built.offered,
        "grounded_rate": round(built.grounded / built.offered, 3) if built.offered else 0.0,
        "cards": len(cards),
        "statements": statements,
        "statements_per_card": round(statements / len(cards), 2) if cards else 0.0,
        "sources_per_card": (
            round(sum(len(c.sources) for c in cards) / len(cards), 2) if cards else 0.0
        ),
        "cards_from_one_document": sum(1 for c in cards if len(c.sources) == 1),
    }


def preview_samples(
    llm: ILlm,
    sample_dir: Path,
    *,
    out_dir: Path,
    extract_prompt: str | None = None,
    synthesis_prompt: str | None = None,
    only: set[str] | None = None,
    concurrency: int = 1,
) -> list[Card]:
    """Build the cards for a folder of documents and write them out."""
    docs = read_samples(sample_dir, only=only)
    built = build(
        llm,
        docs,
        extract_prompt=extract_prompt,
        synthesis_prompt=synthesis_prompt,
        concurrency=concurrency,
    )
    write_preview(built, docs, out_dir=out_dir)
    return built.cards


def write_preview(built: Build, docs: list[DocSource], *, out_dir: Path) -> None:
    """One file per layer, in a deterministic order, so two runs either side of a
    criterion change diff to the change."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(built.cards, key=lambda c: c.norm_keys[:1] or [""])
    _dump(out_dir / "summary.json", summarise(built, docs))
    # `to_builtins`, not `structs.asdict`: a Card holds Statement structs, and
    # asdict flattens only the top level — the nested ones would reach json.dumps
    # as objects and land on disk as their repr, unreadable and undiffable.
    _dump(out_dir / "cards.json", [msgspec.to_builtins(c) for c in ordered])


def _dump(path: Path, payload: Any) -> None:
    # Indented and non-ASCII-preserving: a person reads these, and a corpus
    # written in Chinese would otherwise arrive as escape sequences.
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
