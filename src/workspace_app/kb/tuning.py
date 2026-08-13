"""The bookkeeping every prompt-tuning loop needs, with no opinion about scoring.

Which prompt a round grades, where the versions live, which one the next
revision is written from, what the index file looks like — none of that depends
on WHAT is being extracted. The graph criterion and the card criterion are
deliberately separate pipelines (see ``docs/plan-context-card-evidence.md``:
they are being compared, so sharing an extractor would make them share a failure
mode). Sharing the harness is the opposite — it makes the comparison fairer,
because neither side wins by being searched more thoroughly than the other.

What stays with each task is the scorecard, the reward, and the meta-prompt.
Those are the whole substance; this is the filing cabinet.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any

from .llm import ILlm


def _key(text: str) -> str:
    """Deduping only — each pipeline matches probes with ITS OWN normaliser,
    the one its lookup tool uses. This just stops the same name being drawn
    twice under two spellings."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class Round:
    """One version: the prompt it ran, and how it scored on both sets."""

    version: int
    prompt: str
    tune: dict[str, Any]
    holdout: dict[str, Any] | None
    #: Which version this one's prompt was written from. Not always the version
    #: before it — see :func:`pick_parent`.
    parent: int | None = None


def next_version(rounds_dir: Path) -> int:
    """The version this run will SCORE — the newest one nothing has scored yet.

    Not "one past the highest", because a round leaves TWO versions behind: the
    one it scored and the revision it was handed. Counting folders would skip the
    revision, which is the only thing the round produced that is new.
    """
    written = {int(p.name[1:]) for p in rounds_dir.glob("v*") if p.name[1:].isdigit()}
    scored = {v for v in written if (rounds_dir / f"v{v}" / "scorecard.json").is_file()}
    pending = sorted(written - scored)
    return pending[0] if pending else 0


def load_prompt(rounds_dir: Path, version: int, *, default: str) -> str:
    """The prompt a version ran with — the built-in one when nothing is filed."""
    path = rounds_dir / f"v{version}" / "prompt.txt"
    return path.read_text() if path.is_file() else default


def pick_parent(history: list[Round], fitness: Callable[[dict[str, Any]], float]) -> Round:
    """The version the next revision is written from — the best one, not the last.

    Single-line hill climbing has no way back. A bad revision becomes the base
    for the next one, and the good version it came from stays on disk but out of
    play, so one unlucky edit can cost every round after it. Keeping the whole
    scored history in contention is what a beam is for (ProTeGi, arXiv
    2305.03495, whose own runs overfit a mini-batch around iteration 3-4 and
    which selects across a beam rather than a line for this reason).

    Only versions the HOLDOUT graded are eligible. A version scored on its own
    mini-batch is not comparable with one scored on the fixed set — choosing
    between them would reward a lucky draw, which is the failure the holdout
    exists to prevent.

    Ties go to the later version: on a plateau the loop should keep moving
    forward rather than re-deriving from the same old parent for ever.
    """
    graded = [r for r in history if r.holdout is not None]
    if not graded:
        return history[-1]
    best = graded[0]
    for candidate in graded[1:]:
        assert candidate.holdout is not None and best.holdout is not None
        if fitness(candidate.holdout) >= fitness(best.holdout):
            best = candidate
    return best


def history(rounds_dir: Path, *, upto: int) -> list[Round]:
    """Every version up to and including ``upto`` that has been scored."""
    out: list[Round] = []
    for version in range(upto + 1):
        card = rounds_dir / f"v{version}" / "scorecard.json"
        if not card.is_file():
            continue
        data = json.loads(card.read_text())
        parent = rounds_dir / f"v{version}" / "parent.txt"
        out.append(
            Round(
                version=version,
                prompt=(rounds_dir / f"v{version}" / "prompt.txt").read_text(),
                tune=data["tune"],
                holdout=data["holdout"],
                parent=int(parent.read_text()) if parent.is_file() else None,
            )
        )
    return out


def write_index(
    rounds_dir: Path,
    rounds: list[Round],
    *,
    headline: tuple[str, ...],
    holdout_only: tuple[str, ...] = (),
) -> None:
    """One row per version, so the whole run reads at a glance.

    This is what a person coming back to a folder of rounds actually opens, and
    it is the only place tune and holdout sit side by side — which is where
    overfitting shows up as a shape rather than as a number somebody has to go
    and compare by hand.
    """
    (rounds_dir / "index.json").write_text(
        json.dumps(
            [
                {
                    "version": r.version,
                    # Which version's prompt this one was written from. The beam
                    # means that is not always the version before it, and a
                    # search whose shape is invisible cannot be walked back.
                    "revised_from": r.parent,
                    "tune": {k: r.tune.get(k) for k in headline},
                    "holdout": (
                        {k: r.holdout.get(k) for k in (*headline, *holdout_only)}
                        if r.holdout
                        else None
                    ),
                }
                for r in rounds
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def batch(pool_dir: Path, size: int, *, seed: int) -> set[str] | None:
    """Which documents this round reads, or ``None`` for all of them.

    Drawing a few instead of reading the pool makes a round short enough to run
    many times, and — more usefully — makes consecutive rounds see DIFFERENT
    passages, so the prompt cannot settle into the peculiarities of one fixed
    set. The tuning corpus stops being a thing to overfit and becomes a pool.

    Seeded by the ROUND, so a retry after a crash reads the same passages while
    the next round reads others. Both halves of that matter: without the first a
    killed round changes the question it was answering; without the second the
    batch is the whole set with extra steps.
    """
    if size <= 0:
        return None
    names = sorted(p.stem for p in pool_dir.glob("*.txt"))
    if len(names) <= size:
        return None
    Random(seed).shuffle(names)
    return set(names[:size])


def usable(revised: str, *, fallback: str, required: tuple[str, ...]) -> str:
    """The revision, or the prompt it was meant to replace.

    A revision that dropped a placeholder produces a prompt under which every
    document extracts to nothing — and nothing raises, so the next round would
    score a silent zero and revise from THAT. Keeping the previous prompt makes a
    bad revision cost one round instead of ending the run.
    """
    body = revised.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return body if all(slot in body for slot in required) else fallback


#: Drawing the probe set. Deliberately a NARROWER question than extraction:
#: "name a few things a reader would look up" is answerable in a way that "list
#: everything this passage is about" is not, and that asymmetry is the only
#: reason a model may grade its own family's work here. It is not a licence to
#: trust it — the set is written to disk in plain text so the corpus owner can
#: strike out whatever was never reasonable.
PROBES = """Below is one document from a technical corpus.

Name up to {per_doc} things in it that a reader would come back to look up later —
something they would type into a search box expecting a page about that thing. A
machine, a part or component number, a material, a named process step, a defect,
a supplier.

NOT section headings, NOT generic categories, NOT measured values, NOT words
about knowledge itself. The test: if the word could appear in a document from any
other field, it is not one of these.

Answer as JSON and nothing else: {{"names": ["...", "..."]}}

DOCUMENT
{text}"""


def ensure_probes(llm: ILlm, rounds_dir: Path, holdout_dir: Path, *, per_doc: int = 3) -> list[str]:
    """The frozen probe set: names a reader would come back to look up.

    Drawn ONCE, from the holdout, and never again. A target redrawn each round is
    not a target — an improvement and a re-draw would be the same shape in the
    numbers, and nobody reading the run later could tell which they were seeing.

    Kept as plain text on purpose. Whoever owns the corpus can delete a probe
    that was never reasonable, and this will not write over them.

    Shared between the graph and the card pipelines ON PURPOSE. They extract
    independently — that is the experiment — but "what would a reader look up in
    these documents" belongs to the documents, not to either pipeline, and one
    set scored against both is the only way the comparison means anything.
    """
    path = rounds_dir / "probes.json"
    if path.is_file():
        names = json.loads(path.read_text())["names"]
        assert isinstance(names, list)
        return [str(n) for n in names]

    drawn: list[str] = []
    for doc in sorted(holdout_dir.glob("*.txt")):
        drawn.extend(
            _probe_names(llm.collect(PROBES.format(per_doc=per_doc, text=doc.read_text())))
        )
    seen: set[str] = set()
    unique: list[str] = []
    for name in drawn:
        key = _key(name)
        if key and key not in seen:
            seen.add(key)
            unique.append(name)
    rounds_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"names": unique}, ensure_ascii=False, indent=2) + "\n")
    return unique


def _probe_names(reply: str) -> list[str]:
    """The names out of one reply, or none — a model that answers unusably costs
    this document's probes, not the run."""
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end < start:
        return []
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    names = data.get("names") if isinstance(data, dict) else None
    return [str(n) for n in names if str(n).strip()] if isinstance(names, list) else []
