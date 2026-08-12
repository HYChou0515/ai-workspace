"""The model revising the card EXTRACTION prompt, one version per run.

Same harness as the graph criterion (``kb.tuning``), different reward — because
what a good card criterion does is not what a good entity criterion does.

**The reward is built around one asymmetry.** A claim whose quote is not in the
document is dropped before it reaches a card, so everything that survives is
grounded BY CONSTRUCTION: measuring the surviving cards can never show a prompt
inventing. What shows it is the ratio of what the model OFFERED to what was
grounded, and that is the number this loop steers on.

The synthesis prompt is not tuned here. It reads whatever extraction produced,
so tuning both at once would make each round's evidence a moving target for the
other; extraction gates everything, so extraction goes first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..context_cards import norm
from ..llm import ILlm
from ..tuning import Round, ensure_probes, history, next_version, usable, write_index
from ..tuning import batch as draw_batch
from ..tuning import load_prompt as _load_prompt
from ..tuning import pick_parent as _pick_best
from .extract import built_in_prompt
from .preview import preview_samples

#: How many terms the model is shown from each set. Enough to see what the
#: criterion is doing, small enough to leave room for the prompt it must write.
_SHOWN = 50

#: The columns `index.json` carries for every version, on BOTH document sets.
#: `grounded_rate` is how much of what the model offered was really in the text
#: — inventing, measured. `cards_from_one_document` is the proliferation the
#: owner reported: several thin cards about one term instead of one that
#: accumulated. `statements_per_card` is the same shape from the other side.
#: How many cards the judge is asked about in one round. A sample: the point is
#: a trend across rounds, and a trend does not need every card every time.
_JUDGED = 40

_HEADLINE: tuple[str, ...] = (
    "cards",
    "statements_per_card",
    "cards_from_one_document",
    "grounded_rate",
)
#: Only the holdout carries these: one is scored against probes drawn from the
#: holdout, the other is a card-level verdict that would move with the draw.
_HOLDOUT_ONLY: tuple[str, ...] = ("lookup_hit_rate", "defines_rate")


def load_prompt(rounds_dir: Path, version: int) -> str:
    """The prompt a version ran with — the built-in one when nothing is filed."""
    return _load_prompt(rounds_dir, version, default=built_in_prompt())


def fitness(card: dict[str, Any]) -> float:
    """One number for comparing versions: what it FINDS, discounted by inventing.

    Three factors, MULTIPLIED — the same trap as on the graph side. A prompt that
    extracts nothing offers nothing, so its grounded rate is a perfect 1.0 by
    vacuity; subtracting a penalty would hand it the win. As factors, a version
    that answers no probe scores zero however clean it looks.

    ``defines_rate`` defaults to 1.0 when absent, because absent means the judge
    could not be read — not that the cards were bad. It is the one input
    here that is not prompt-controlled, so reading its silence as failure would
    punish a version for something it did not do.
    """
    return (
        float(card.get("lookup_hit_rate", 0.0))
        * float(card.get("grounded_rate", 0.0))
        * float(card.get("defines_rate", 1.0))
    )


def pick_parent(rounds: list[Round]) -> Round:
    """The version the next revision is written from — the best by :func:`fitness`."""
    return _pick_best(rounds, fitness)


def scorecard(out_dir: Path, *, shown: int = _SHOWN) -> dict[str, Any]:
    """The numbers plus the TERMS, read back off a preview run.

    The terms are not decoration. A criterion that refuses more scores better on
    every count here, so counts alone point at carding nothing; seeing which
    terms are still there is what stops that.
    """
    summary = json.loads((out_dir / "summary.json").read_text())
    cards = json.loads((out_dir / "cards.json").read_text())
    return {
        **{k: summary[k] for k in ("documents", "cards", "statements", "statements_per_card")},
        "statements_offered": summary["statements_offered"],
        # The direct measurement of inventing. Everything that survives the quote
        # gate is grounded, so this is the only place the invention shows.
        "grounded_rate": summary["grounded_rate"],
        "sources_per_card": summary["sources_per_card"],
        # Cards standing on a single document are the shape that used to
        # proliferate: several thin cards about one term instead of one that
        # accumulated.
        "cards_from_one_document": summary["cards_from_one_document"],
        "terms": sorted(c["title"] for c in cards)[:shown],
    }


def probe_score(out_dir: Path, names: list[str]) -> dict[str, Any]:
    """Would ``lookup_glossary`` find a card for each probe?

    The one number that FALLS when the criterion tightens past useful. Matched
    through ``context_cards.norm`` against the card's own keys — the exact rule
    the lookup uses, so this measures what a reader would actually experience.

    The probe file is the SAME one the graph loop draws. They extract
    independently; scoring them against one set is what makes the two runs
    comparable at all.
    """
    cards = json.loads((out_dir / "cards.json").read_text())
    keys = {n for card in cards for key in card["keys"] if (n := norm(key))}
    missed = [name for name in names if norm(name) not in keys]
    return {
        "lookup_hit_rate": round((len(names) - len(missed)) / len(names), 3) if names else 0.0,
        "probes_total": len(names),
        "probes_missed": missed,
    }


#: What went wrong in the real corpus, quoted from the owner rather than
#: imagined. Stated as SHAPES — a thing that was invented, a thing that failed to
#: accumulate — never as a list of words to avoid: naming words would mean
#: guessing what the corpus is about from outside it, and every criterion this
#: project invented that way was wrong within a corpus or two.
#:
#: Overridable: drop an `examples.md` in the rounds folder and it replaces this.
DEFAULT_EXAMPLES = """A statement earns its place only if a reader can put a
finger on the sentence that makes it. These came out of the real corpus and are
the failure being fixed:

- A card for 蘋果 reading 蘋果是薔薇科蘋果屬 — no document ever mentioned 薔薇科
  or 蘋果屬. The model filled the gap from what it already knew. That is the
  failure: a knowledge base answering from general knowledge is not reporting
  what the corpus says, and a reader cannot tell the two apart.
- Several cards about 蘋果, each describing a different facet, because each
  document wrote its own.
- A card reading 「H2O2 是這次的材料」 — true, quotable, and worthless. It records
  which run used the material rather than what the material IS, and 這次 cannot
  be resolved by anyone reading the card.
- A card titled 「14k ratio」 whose body reads "increased from 7% in wave 1 to 20%
  in wave 2". Nothing is invented and nothing is ambiguous; it simply answers a
  different question from the one a reader opening it had. What the ratio IS
  never appears. One document said it is a fruit and another said it is
  red, and both became cards.

What the second one should have produced is ONE card reading 蘋果是紅色的水果 —
every statement carried, combined into a sentence rather than listed side by
side. Statements accumulate across documents; the card is written from
all of them at once."""

DEFINES = """Below are glossary cards. You are shown the cards ONLY — not the
documents they came from, which is exactly what a reader looking a term up will
have.

Someone typed the term into a search box because they did not know what it
meant. For each card, answer one question: after reading it, do they know what
the term IS?

Answer no in two cases, and they are the ones that actually happen:

- The card describes ONE OCCASION rather than the thing. 「H2O2 是這次的材料」 —
  這次 points at the document it came from, so outside that document it points at
  nothing. Same for 本批 / 目前 / 上述 / 如前所示.
- The card reports a FINDING or a MEASUREMENT instead of a meaning. A card titled
  「14k ratio」 whose body is "increased from 7% in wave 1 to 20% in wave 2" tells
  a reader what happened to it and never what it is. The number may well matter —
  it just does not answer the question this card was opened to answer.

CARDS
{cards}

Answer as JSON and nothing else:
{{"verdicts": [{{"title": "...", "defines": true}}, ...]}}"""


def defines_score(llm: ILlm, cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Does each card tell a reader what its term IS?

    Neither of the other numbers can see this. 「H2O2 是這次的材料」 really is in
    the text, so it is perfectly grounded; "14k ratio increased from 7% to 20%"
    resolves fine on its own, so it is perfectly standalone. Both leave a reader
    who typed the term knowing no more than before.

    What catches them is reading the card WITHOUT its source and asking the
    reader's question — so the judge is handed the bodies alone, never the
    documents.

    An unusable reply reports NOTHING rather than zero: a parse failure and "none
    of them stand alone" are the same number and opposite facts.
    """
    if not cards:
        return {}  # an empty glossary must not buy a clean sheet here
    listed = "\n".join(f"- {c.get('title', '')}: {c.get('body', '')}" for c in cards[:_JUDGED])
    reply = llm.collect(DEFINES.replace("{cards}", listed))
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end < start:
        return {}
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    verdicts = data.get("verdicts")
    rows = [v for v in verdicts if isinstance(v, dict)] if isinstance(verdicts, list) else []
    if not rows:
        return {}
    out = sorted(str(v.get("title", "")) for v in rows if not v.get("defines"))
    return {
        "defines_rate": round((len(rows) - len(out)) / len(rows), 3),
        "does_not_define": out[:_JUDGED],
    }


REVISE = """You are improving the prompt that pulls STATEMENTS about terms out of
a technical corpus, for a glossary. Your whole output is the REVISED PROMPT — no
commentary, no explanation, no code fences.

## What the corpus is, and what going wrong has looked like

{examples}

## What the prompt is for

Each document records what it STATES about a term, with the exact words that
state it. A later pass turns all the statements about one term into its
definition. So this prompt is not writing definitions; it is collecting evidence.

## The two ways it goes wrong

**Inventing.** A claim the document does not make. Every claim must carry a quote
copied character-for-character from the document, and a claim whose quote is not
found is DISCARDED before it reaches a card. `grounded_rate` below is how much of
what the last version offered survived that check — it is the direct measurement
of inventing, and the only one, because everything that survives is grounded by
construction.

**Answering a question nobody asked.** Someone types a term in because they do
not know what it means. Two shapes leave them no wiser, and both are quotable
and perfectly grounded, so the quote gate cannot catch either:

- the occasion instead of the thing — 「H2O2 是這次的材料」, where 這次 points at
  the source document and so, outside it, at nothing;
- a finding instead of a meaning — a 「14k ratio」 card whose body is "increased
  from 7% in wave 1 to 20% in wave 2". The number may matter; it is not what the
  term MEANS, and the card was opened to find that out.

`defines_rate` below is the share of cards that answer the reader's question.

**Refusing.** Extracting nothing makes `grounded_rate` a perfect 1.0 by vacuity
and every other count look tidy. It is the WORST outcome. `lookup_hit_rate` is
the guard: the share of terms a reader would actually look up that ended up with
a card. If it falls, you have gone too far.

## What the prompt must still do

Keep the JSON output contract EXACTLY as it is — the same keys (`cards`, and per
card `term` / `keys` / `statements`, and per statement `text` / `quote`), and the
`{{text}}` placeholder at the end. Anything that parses differently is discarded
and the whole run reports nothing.

## The prompt you are revising

```
{current}
```

## What it produced

{scores}

## History — what has already been tried

{history}

Revise the prompt. Change what the evidence above says is wrong; leave what is
working alone. Output the full revised prompt and nothing else."""


def revision_prompt(
    current: str,
    tune: dict[str, Any],
    holdout: dict[str, Any] | None,
    rounds: list[Round],
    *,
    carry: int = 2,
    examples: str = DEFAULT_EXAMPLES,
) -> str:
    """What the model is asked, in full.

    The recent prompts travel with their scores. Numbers alone leave it hill-
    climbing blind: it cannot recognise a change it already made and moved away
    from, so it undoes it, scores worse, redoes it, and oscillates.
    """
    scores = (
        "### On the documents drawn for this round\n"
        + json.dumps(tune, ensure_ascii=False, indent=2)
        + "\n\n### On the held-out documents (NOT tuned against — if these stop "
        "improving while the ones above do, the prompt is learning the sample "
        "rather than the corpus)\n"
        + (
            json.dumps(holdout, ensure_ascii=False, indent=2)
            if holdout
            else "(not run this round — the history below carries the last time it was)"
        )
    )
    rows = [
        f"- v{r.version}: tune grounded={r.tune.get('grounded_rate')} "
        f"cards={r.tune.get('cards')} | "
        + (
            f"holdout hit={r.holdout.get('lookup_hit_rate')} "
            f"grounded={r.holdout.get('grounded_rate')}"
            if r.holdout
            else "holdout not run"
        )
        for r in rounds
    ]
    for r in rounds[-carry:]:
        rows.append(f"\n### The prompt v{r.version} ran\n```\n{r.prompt.strip()}\n```")
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
    reviser: ILlm | None = None,
    probes_dir: Path | None = None,
    batch: int = 0,
    holdout_every: int = 1,
    concurrency: int = 1,
    synthesis_prompt: str | None = None,
) -> int:
    """Score the newest extraction prompt, ask for a revision, file it as next."""
    version = next_version(rounds_dir)
    current = load_prompt(rounds_dir, version)
    here = rounds_dir / f"v{version}"
    here.mkdir(parents=True, exist_ok=True)
    (here / "prompt.txt").write_text(current)

    preview_samples(
        llm,
        tune_dir,
        out_dir=here / "tune",
        extract_prompt=current,
        synthesis_prompt=synthesis_prompt,
        concurrency=concurrency,
        only=draw_batch(tune_dir, batch, seed=version),
    )
    tune = scorecard(here / "tune")

    holdout: dict[str, Any] | None = None
    if holdout_every <= 1 or version % holdout_every == 0:
        # Before the extraction, so a run killed part-way keeps the probes it
        # paid for: they are the one artefact here that must never be redrawn.
        # The probe set lives with the SAMPLES, not with this loop's versions:
        # the graph pipeline is scored against the same one, and two draws would
        # be two yardsticks — which is the one thing a comparison cannot have.
        probes = ensure_probes(llm, probes_dir or rounds_dir, holdout_dir)
        preview_samples(
            llm,
            holdout_dir,
            out_dir=here / "holdout",
            extract_prompt=current,
            synthesis_prompt=synthesis_prompt,
            concurrency=concurrency,
        )
        # The card-level verdict rides the holdout, not the rotating batch: it
        # judges CARDS, so on a document set that changes every round its share
        # moves with the draw rather than with the prompt.
        built = json.loads((here / "holdout" / "cards.json").read_text())
        holdout = (
            scorecard(here / "holdout")
            | probe_score(here / "holdout", probes)
            | defines_score(reviser or llm, built)
        )
    (here / "scorecard.json").write_text(
        json.dumps({"tune": tune, "holdout": holdout}, ensure_ascii=False, indent=2) + "\n"
    )

    past = history(rounds_dir, upto=version)
    write_index(rounds_dir, past, headline=_HEADLINE, holdout_only=_HOLDOUT_ONLY)

    parent = pick_parent(past)
    # Whoever owns the corpus knows its bad cards better than this file does.
    notes = rounds_dir / "examples.md"
    revised = (reviser or llm).collect(
        revision_prompt(
            parent.prompt,
            parent.tune,
            parent.holdout,
            past,
            examples=notes.read_text() if notes.is_file() else DEFAULT_EXAMPLES,
        )
    )
    nxt = rounds_dir / f"v{version + 1}"
    nxt.mkdir(parents=True, exist_ok=True)
    (nxt / "prompt.txt").write_text(usable(revised, fallback=parent.prompt, required=("{text}",)))
    (nxt / "parent.txt").write_text(str(parent.version))
    return version
