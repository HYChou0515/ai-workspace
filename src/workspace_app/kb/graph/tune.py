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
from .preview import preview_samples

#: How many surfaces the model is shown from each set. Enough to see what the
#: criterion is doing, small enough to leave room for the prompt it must write.
_SHOWN = 60


@dataclass(frozen=True)
class Round:
    """One version: the prompt it used, and how it scored on both sets."""

    version: int
    prompt: str
    tune: dict[str, Any]
    holdout: dict[str, Any]


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


REVISE = """You are improving the prompt that extracts a knowledge graph from a
technical corpus. Your whole output is the REVISED PROMPT — no commentary, no
explanation, no code fences.

## What the corpus is, and what a good extraction looks like

Manufacturing and process documents. The things worth extracting are the ones a
person on the line could point at or look up: machines and their identifiers,
part and component numbers, materials, named process steps, defects, named
process parameters, suppliers.

GOOD names, from this corpus: 回焊爐 RO-3 / SPI / PPOOIXUX / 冷焊 / 錫膏印刷偏移
/ 第四溫區 / 輸送帶速度

BAD names, and these are the actual failure being fixed — the corpus is currently
full of them: 基礎知識 / 細結構 / 影響比對模式 / 典型 / 系統 / 問題 / 資料 / 方法.
They are words ABOUT documents rather than things in a factory, and they compose
endlessly, which is why one document currently yields 163 distinct names.

A measured VALUE is not a thing: 245°C, 98.7%, 12 件 belong to the statements the
prompt already collects, not to the list of things.

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


def revision_prompt(current: str, tune: dict, holdout: dict, history: list[Round]) -> str:
    """What the model is asked, in full.

    The HOLDOUT numbers are shown too, and labelled. A model told only how it did
    on the passages it was tuned against has no way to know it is overfitting,
    and neither would anyone reading its output later.
    """
    scores = (
        "### On the passages being tuned against\n"
        + json.dumps(tune, ensure_ascii=False, indent=2)
        + "\n\n### On the held-out passages (NOT tuned against — if these stop "
        "improving while the ones above do, the prompt is learning the sample "
        "rather than the corpus)\n" + json.dumps(holdout, ensure_ascii=False, indent=2)
    )
    rows = [
        f"- v{r.version}: tune per_doc={r.tune['mentions_per_document']} "
        f"digits={r.tune['mentions_starting_with_a_digit']} | "
        f"holdout per_doc={r.holdout['mentions_per_document']} "
        f"digits={r.holdout['mentions_starting_with_a_digit']}"
        for r in history
    ]
    return REVISE.format(
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
    reviser: ILlm | None = None,
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

    for half, folder in (("tune", tune_dir), ("holdout", holdout_dir)):
        preview_samples(llm, folder, out_dir=here / half, prompt=current, max_tokens=chunk_tokens)
    tune = scorecard(here / "tune")
    holdout = scorecard(here / "holdout")
    (here / "scorecard.json").write_text(
        json.dumps({"tune": tune, "holdout": holdout}, ensure_ascii=False, indent=2) + "\n"
    )

    history = _history(rounds_dir, upto=version)
    _write_index(rounds_dir, history)

    revised = (reviser or llm).collect(revision_prompt(current, tune, holdout, history))
    nxt = rounds_dir / f"v{version + 1}"
    nxt.mkdir(parents=True, exist_ok=True)
    (nxt / "prompt.txt").write_text(_usable(revised, fallback=current))
    return version


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
                    "holdout": {k: r.holdout[k] for k in _HEADLINE},
                }
                for r in history
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


_HEADLINE = ("mentions_per_document", "distinct_names", "mentions_starting_with_a_digit")
