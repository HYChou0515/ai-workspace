"""Calibrating the judge against the only ground truth there is: the owner.

``defines_rate`` is the one number in the card scorecard built on a model's
opinion, so it is the one that can be confidently wrong — and a tuning loop
steering on a wrong metric is worse than no loop, because it makes progress
away from what anybody wanted.

What settles it is a person marking a handful of cards. That is the ONLY part a
person has to do: rewriting the criterion from the disagreements is
prompt-writing, which is what models are for. So this runs on the owner's
machine with the owner's model — mark, revise, re-score, repeat — rather than
sending examples away and waiting for someone to hand back a patch.

The marks are frozen at labelling time. A review row records both the verdict
the judge gave AND the owner's answer, so every later judge is scored against
what the owner actually thought rather than against its own predecessor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..llm import ILlm
from ..tuning import load_prompt as _load_prompt
from ..tuning import next_version, usable
from .tune import DEFINES, defines_score

REVISE_JUDGE = """You are sharpening the criterion a judge uses to decide
whether a glossary card tells a reader what its term IS.
Your whole output is the REVISED CRITERION — no commentary, no code fences.

A person has gone through the cards below and answered that question themselves.
The judge disagreed with them. The person is right by definition: the criterion
exists to reproduce their standard, not to be defensible on its own terms.

## The criterion the judge is using now

```
{current}
```

## Where it disagreed with the person

{disagreements}

## What it got right

{agreements}

Change what the disagreements show is wrong; leave what is working alone. Do not
narrow the criterion to these exact cards — a rule that only fires on the words
above will disagree with the person again on the next batch. State the property
that makes the difference.

Keep the `{{cards}}` placeholder: it is where the cards are inserted, and a
criterion without it judges nothing at all.

Output the full revised criterion and nothing else."""


def labels(review: list[dict[str, Any]]) -> dict[str, bool]:
    """The owner's answer per card, frozen at labelling time.

    An unmarked row is not missing data — it is agreement, recorded by omission.
    Marking only the disagreements is what turns twenty cards into three minutes,
    and the verdict that stood is kept beside it so a later judge is scored
    against the PERSON rather than against its own predecessor.
    """
    return {
        str(row["title"]): (bool(row["judge"]) if row.get("ok") is None else bool(row["ok"]))
        for row in review
    }


def agreement(verdicts: dict[str, bool], truth: dict[str, bool]) -> dict[str, Any]:
    """How often this judge said what the owner said."""
    disagreed = sorted(t for t, want in truth.items() if verdicts.get(t, want) != want)
    return {
        "reviewed": len(truth),
        "agreement": len(truth) - len(disagreed),
        "agreement_rate": round((len(truth) - len(disagreed)) / len(truth), 3) if truth else 0.0,
        "disagreed": disagreed,
    }


def judge_prompt(rounds_dir: Path, version: int) -> str:
    """The criterion a judge version ran with — the built-in one when none is filed."""
    return _load_prompt(rounds_dir / "judge", version, default=DEFINES)


def calibrate(llm: ILlm, *, rounds_dir: Path) -> int:
    """One calibration round: score the newest criterion, revise it, file the next.

    Returns the version just scored. Trigger it repeatedly and the criteria
    accumulate under ``judge/v0, v1, …`` exactly like the extraction prompts, so
    a criterion that got worse can be walked back to.
    """
    review = json.loads((rounds_dir / "review.json").read_text())
    truth = labels(review)
    if not truth:
        # An empty review agrees with everything. Reporting that as a calibrated
        # judge is how a loop ends up steering on a number nobody checked.
        raise SystemExit(
            f"{rounds_dir / 'review.json'} has no reviewed cards — mark some before calibrating"
        )

    home = rounds_dir / "judge"
    version = next_version(home)
    current = judge_prompt(rounds_dir, version)
    here = home / f"v{version}"
    here.mkdir(parents=True, exist_ok=True)
    (here / "prompt.txt").write_text(current)

    cards = [{"title": r["title"], "body": r.get("body", "")} for r in review]
    scored = defines_score(llm, cards, prompt=current)
    rejected = set(scored.get("does_not_define", []))
    verdicts = {str(r["title"]): str(r["title"]) not in rejected for r in review}
    result = agreement(verdicts, truth)
    (here / "scorecard.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    _write_index(home)

    body = {str(r["title"]): r.get("body", "") for r in review}
    revised = llm.collect(
        REVISE_JUDGE.format(
            current=current,
            disagreements=_show(result["disagreed"], body, truth, verdicts)
            or "(none — it agreed everywhere)",
            agreements=_show(
                [t for t in truth if t not in result["disagreed"]], body, truth, verdicts
            )
            or "(none)",
        )
    )
    nxt = home / f"v{version + 1}"
    nxt.mkdir(parents=True, exist_ok=True)
    (nxt / "prompt.txt").write_text(usable(revised, fallback=current, required=("{cards}",)))
    (nxt / "parent.txt").write_text(str(version))
    return version


def _show(
    titles: list[str], body: dict[str, str], truth: dict[str, bool], verdicts: dict[str, bool]
) -> str:
    """The cards themselves, with both answers. A criterion cannot be repaired
    from a score — only from the card it misjudged, beside what it should have
    said."""
    lines = []
    for title in titles:
        said = "DOES" if verdicts.get(title) else "does NOT"
        want = "DOES" if truth[title] else "does NOT"
        lines.append(
            f"- {title}: {body.get(title, '')}\n"
            f"    the judge said it {said} define the term; the person said it {want}"
        )
    return "\n".join(lines)


def _write_index(home: Path) -> None:
    """One row per scored criterion, so the whole calibration reads at a glance —
    including a version that agreed LESS than the one before it, which is the
    shape that has to be walkable back.

    Every version on disk, not just those up to the one just scored: deleting a
    scorecard by hand means "run this one again", and re-running v0 must not
    erase v1 and v2 from the record.
    """
    rows = []
    for folder in sorted(
        (p for p in home.glob("v*") if p.name[1:].isdigit()), key=lambda p: int(p.name[1:])
    ):
        card = folder / "scorecard.json"
        if card.is_file():
            rows.append({"version": int(folder.name[1:]), **json.loads(card.read_text())})
    (home / "index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
