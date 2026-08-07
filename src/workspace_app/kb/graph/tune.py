"""#697 — let the model revise the extraction prompt, one version at a time.

Writing the criterion takes many passes, and the person who owns the corpus is
not always at the keyboard. This runs ONE pass per invocation: score the current
prompt, hand the model what it produced, take back a revision, and file both
under a new version. Trigger it repeatedly and the versions accumulate.

**Every version is kept, and every version is scored on BOTH sets.** A prompt
tuned against the passages it was shown gets better at those passages; whether
it got better at the corpus is a different question, and the only way to see the
difference later is to have recorded both at the time. `index.json` is that
record — one row per version, readable at a glance, so a run of rounds that
improved `tune` while `holdout` sat still can be spotted and walked back to.

**The obvious way to score well is to extract nothing.** Every count here falls
when the criterion refuses everything, and a model optimising against counts
alone will find that. So the model is shown WHAT SURVIVED, told that an empty
answer is the worst outcome rather than the best, and the scorecard carries the
kept names beside the numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any

from ..llm import ILlm
from .entity_extract import built_in_prompt
from .normalize import norm_surface
from .preview import preview_samples

#: How many surfaces the model is shown from each set. Enough to see what the
#: criterion is doing, small enough to leave room for the prompt it must write.
_SHOWN = 60

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


@dataclass(frozen=True)
class Round:
    """One version: the prompt it used, and how it scored on both sets."""

    version: int
    prompt: str
    tune: dict[str, Any]
    holdout: dict[str, Any] | None


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


def load_prompt(rounds_dir: Path, version: int) -> str:
    """The prompt a version ran with — the built-in one when nothing is filed."""
    path = rounds_dir / f"v{version}" / "prompt.txt"
    return path.read_text() if path.is_file() else built_in_prompt()


def scorecard(out_dir: Path, *, seed: int = 0) -> dict[str, Any]:
    """The numbers plus a sample of the NAMES, read back off a preview run.

    The names are not decoration. Every number here improves when the criterion
    refuses more, so numbers alone point at extracting nothing; what stops that
    is seeing which names are still there.
    """
    summary = json.loads((out_dir / "summary.json").read_text())
    mentions = json.loads((out_dir / "mentions.json").read_text())
    names = sorted(
        {f"{m['surface']}({m['kind']})" if m["kind"] else m["surface"] for m in mentions}
    )
    Random(seed).shuffle(names)
    return {
        "mentions_per_document": summary["mentions_per_document"],
        "distinct_names": summary["distinct_names"],
        "mentions": summary["mentions"],
        "mentions_starting_with_a_digit": summary["mentions_starting_with_a_digit"],
        "kinds": dict(list(summary["kinds"].items())[:12]),
        "kept": sorted(names[:_SHOWN]),
    }


def ensure_probes(llm: ILlm, rounds_dir: Path, holdout_dir: Path, *, per_doc: int = 3) -> list[str]:
    """The frozen probe set: names a reader would come back to look up.

    Drawn ONCE, from the holdout, and never again. A target redrawn each round is
    not a target — an improvement and a re-draw would be the same shape in the
    numbers, and nobody reading the run later could tell which they were seeing.

    Kept as plain text on purpose. Whoever owns the corpus can delete a probe
    that was never reasonable, and this will not write over them.
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
        key = norm_surface(name)
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


def probe_score(out_dir: Path, names: list[str]) -> dict[str, Any]:
    """Would ``lookup_entity`` find each probe in the graph this version built?

    This is the one number in the scorecard that FALLS when the criterion
    refuses more. Every other one rewards refusing everything, so without this
    the loop has no way to notice it has tightened past useful — the meta-prompt
    can only warn about that, and a warning is not a signal.

    Matched exactly the way the tool being modelled matches: through
    ``norm_surface`` against a live entity's ``norm_keys``. Tombstones are
    skipped because ``entity_card`` skips them — their evidence lives at the host
    now, so counting them would credit a lookup that returns nothing.
    """
    entities = json.loads((out_dir / "entities.json").read_text())
    keys = {
        key
        for entity in entities
        if not entity.get("merged_into")
        for key in entity.get("norm_keys", ())
    }
    missed = [name for name in names if norm_surface(name) not in keys]
    return {
        # The rate alone cannot be checked by a reader. The names can: they can
        # open the document and see whether the miss was the extractor's fault.
        "lookup_hit_rate": round((len(names) - len(missed)) / len(names), 3) if names else 0.0,
        "probes_total": len(names),
        "probes_missed": missed,
    }


#: What a name has to BE, and the ones this corpus actually produced that it must
#: not. Stated as a PROPERTY plus real observations, never as a list of good
#: words: naming the good ones would mean guessing what the corpus is about from
#: outside it, and every criterion this project invented that way was wrong
#: within a corpus or two. The bad ones are quoted from real runs, which is a
#: different kind of claim — they are evidence, not a guess.
#:
#: Overridable: drop an `examples.md` in the rounds folder and it replaces this.
#: Whoever owns the corpus knows its vocabulary; this only has to be right enough
#: to start.
DEFAULT_EXAMPLES = """A name earns its place if a person could POINT AT IT or LOOK IT UP:
a machine, a part or component number, a material, a named process step, a defect, a named
parameter, a supplier. The test that separates them: **if the word could appear
in a document from any other field, it is not what THIS document is about.**

These came out of the real corpus and are the failure being fixed:

- 基礎知識 / 細結構 / 影響比對模式 / 典型 — words about KNOWLEDGE rather than
  about the subject. They compose endlessly, which is why one document currently
  yields 163 distinct names.
- 訊息 / 討論 / product — the document's own furniture: a section heading, a
  column label, a generic category. Every document has them, so they distinguish
  nothing.
- severe — a severity LEVEL. It is the VALUE of some thing's attribute, and the
  prompt already collects values separately.
- 「10 product (10prds)」 — a table cell or a legend read as an object. A count
  is not a thing.

A measured value is never a thing either: it belongs to the statements the
prompt already collects."""

REVISE = """You are improving the prompt that extracts a knowledge graph from a
technical corpus. Your whole output is the REVISED PROMPT — no commentary, no
explanation, no code fences.

## What the corpus is, and what a good extraction looks like

{examples}

## The rule that must not be broken

Extracting NOTHING scores perfectly on every number below and is the WORST
possible outcome. If the names under "kept" stop containing machines, part
numbers and defects, you have gone too far and must pull back. Recall matters as
much as precision; you are removing a category of noise, not tightening until
little survives.

## What the prompt must still do

Keep the JSON output contract EXACTLY as it is — the same four keys, the same
field names, the same "No prose." instruction, and the `{{text}}` placeholder at
the end. Anything that parses differently is discarded downstream and the whole
run reports nothing.

## The prompt you are revising

```
{current}
```

## What it produced

{scores}

## History

{history}

Revise the prompt. Change what the evidence above says is wrong; leave what is
working alone. Output the full revised prompt and nothing else."""


def revision_prompt(
    current: str,
    tune: dict,
    holdout: dict | None,
    history: list[Round],
    examples: str = DEFAULT_EXAMPLES,
) -> str:
    """What the model is asked, in full.

    The HOLDOUT numbers are shown too, and labelled. A model told only how it did
    on the passages it was tuned against has no way to know it is overfitting,
    and neither would anyone reading its output later.
    """
    scores = (
        "### On the passages drawn for this round\n"
        + json.dumps(tune, ensure_ascii=False, indent=2)
        + "\n\n### On the held-out passages (NOT tuned against — if these stop "
        "improving while the ones above do, the prompt is learning the sample "
        "rather than the corpus)\n"
        + (
            json.dumps(holdout, ensure_ascii=False, indent=2)
            if holdout
            else "(not run this round — the history below carries the last time it was)"
        )
    )
    rows = [
        f"- v{r.version}: tune per_doc={r.tune['mentions_per_document']} "
        f"digits={r.tune['mentions_starting_with_a_digit']} | "
        + (
            f"holdout per_doc={r.holdout['mentions_per_document']} "
            f"digits={r.holdout['mentions_starting_with_a_digit']}"
            if r.holdout
            else "holdout not run"
        )
        for r in history
    ]
    return REVISE.format(
        examples=examples,
        current=current,
        scores=scores,
        history="\n".join(rows) if rows else "(this is the first version)",
    )


def run_round(
    llm: ILlm,
    *,
    rounds_dir: Path,
    tune_dir: Path,
    holdout_dir: Path,
    chunk_tokens: int = 256,
    chunk_overlap: int = 32,
    concurrency: int = 1,
    reviser: ILlm | None = None,
    batch: int = 0,
    holdout_every: int = 1,
) -> int:
    """Score the newest prompt, ask for a revision, file it as the next version.

    Returns the version just written. One invocation is one round: the caller
    triggers it as often as it likes, and the versions accumulate so a later
    reader can walk back through them.
    """
    version = next_version(rounds_dir)
    current = load_prompt(rounds_dir, version)
    here = rounds_dir / f"v{version}"
    here.mkdir(parents=True, exist_ok=True)
    (here / "prompt.txt").write_text(current)

    preview_samples(
        llm,
        tune_dir,
        out_dir=here / "tune",
        prompt=current,
        max_tokens=chunk_tokens,
        overlap_tokens=chunk_overlap,
        concurrency=concurrency,
        only=_batch(tune_dir, batch, seed=version),
    )
    tune = scorecard(here / "tune")
    holdout: dict[str, Any] | None = None
    if holdout_every <= 1 or version % holdout_every == 0:
        # Before the extraction, so a run killed part-way keeps the probes it
        # paid for: they are the one artefact here that must never be redrawn.
        probes = ensure_probes(llm, rounds_dir, holdout_dir)
        preview_samples(
            llm,
            holdout_dir,
            out_dir=here / "holdout",
            prompt=current,
            max_tokens=chunk_tokens,
            overlap_tokens=chunk_overlap,
            concurrency=concurrency,
        )
        holdout = scorecard(here / "holdout") | probe_score(here / "holdout", probes)
    (here / "scorecard.json").write_text(
        json.dumps({"tune": tune, "holdout": holdout}, ensure_ascii=False, indent=2) + "\n"
    )

    history = _history(rounds_dir, upto=version)
    _write_index(rounds_dir, history)

    # Whoever owns the corpus knows its vocabulary better than this file does.
    notes = rounds_dir / "examples.md"
    revised = (reviser or llm).collect(
        revision_prompt(
            current,
            tune,
            holdout,
            history,
            examples=notes.read_text() if notes.is_file() else DEFAULT_EXAMPLES,
        )
    )
    nxt = rounds_dir / f"v{version + 1}"
    nxt.mkdir(parents=True, exist_ok=True)
    (nxt / "prompt.txt").write_text(_usable(revised, fallback=current))
    return version


def _batch(tune_dir: Path, size: int, *, seed: int) -> set[str] | None:
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
    names = sorted(p.stem for p in tune_dir.glob("*.txt"))
    if len(names) <= size:
        return None
    Random(seed).shuffle(names)
    return set(names[:size])


def _usable(revised: str, *, fallback: str) -> str:
    """The revision, or the prompt it was meant to replace.

    A model that drops `{text}` produces a prompt under which every passage
    extracts to nothing — and the extractor never raises, so the next round would
    score a silent zero and revise from THAT. Keeping the previous prompt makes a
    bad revision cost one round instead of ending the run.
    """
    body = revised.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return body if "{text}" in body else fallback


def _history(rounds_dir: Path, *, upto: int) -> list[Round]:
    out: list[Round] = []
    for version in range(upto + 1):
        card = rounds_dir / f"v{version}" / "scorecard.json"
        if not card.is_file():
            continue
        data = json.loads(card.read_text())
        out.append(
            Round(
                version=version,
                prompt=(rounds_dir / f"v{version}" / "prompt.txt").read_text(),
                tune=data["tune"],
                holdout=data["holdout"],
            )
        )
    return out


def _write_index(rounds_dir: Path, history: list[Round]) -> None:
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
                    "tune": {k: r.tune[k] for k in _HEADLINE},
                    # The holdout row carries the downstream number too — it is
                    # the one a person is actually looking for when they open
                    # this file, and the only column that falls when the
                    # criterion has tightened past useful.
                    "holdout": (
                        {k: r.holdout.get(k) for k in (*_HEADLINE, "lookup_hit_rate")}
                        if r.holdout
                        else None
                    ),
                }
                for r in history
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


_HEADLINE = ("mentions_per_document", "distinct_names", "mentions_starting_with_a_digit")
