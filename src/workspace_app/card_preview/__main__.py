"""CLI glue for the read-only card preview.

Settings-driven composition, omitted from coverage like the other CLI roots; the
seams it calls are unit-tested in ``kb.cards``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ..config.loader import load
from ..factories import get_kb_llm
from ..kb.cards.build import built_in_synthesis_prompt
from ..kb.cards.extract import built_in_prompt
from ..kb.cards.preview import preview_samples
from ..kb.cards.tune import DEFINES


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.card_preview",
        description="Build context cards from a folder of text files and write them as JSON. "
        "Reads only — nothing is stored.",
    )
    p.add_argument(
        "--samples",
        type=Path,
        required=False,
        help="a folder of .txt files — the one `graph_preview --dump-samples` wrote",
    )
    p.add_argument("-o", "--out-dir", type=Path, default=Path("./card-preview"))
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    p.add_argument(
        "--extract-prompt",
        type=Path,
        default=None,
        help="the WHOLE extraction prompt, replacing the built-in one. Must contain {text}",
    )
    p.add_argument(
        "--synthesis-prompt",
        type=Path,
        default=None,
        help="the WHOLE synthesis prompt. Must contain {term} and {statements}",
    )
    p.add_argument(
        "--dump-prompts",
        type=Path,
        default=None,
        metavar="DIR",
        help="write both built-in prompts to DIR and exit, as a starting point to edit",
    )
    p.add_argument(
        "--tune-round",
        type=Path,
        default=None,
        metavar="DIR",
        help="run ONE prompt-improvement round in DIR: score the newest unscored extraction "
        "prompt on DIR/tune and DIR/holdout, ask the model for a revision, file it as the next "
        "version. Every version is kept and scored on both sets, so a later reader can see "
        "where tuning became overfitting and walk back",
    )
    p.add_argument(
        "--from",
        dest="samples_from",
        type=Path,
        default=None,
        metavar="DIR",
        help="where tune/, holdout/ and probes.json live (default: the --tune-round dir). "
        "Point both pipelines at ONE of these: they must read the same documents and be "
        "scored against the same probes, while keeping their versions in separate folders",
    )
    p.add_argument(
        "--batch",
        type=int,
        default=0,
        metavar="N",
        help="score only N documents drawn at random from DIR/tune each round (0 = all of it)",
    )
    p.add_argument(
        "--holdout-every", type=int, default=1, metavar="N", help="run the holdout every Nth round"
    )
    p.add_argument(
        "--review",
        type=Path,
        default=None,
        metavar="DIR",
        help="draw a random sample of cards into DIR/review.json for a person to mark. "
        "Pre-filled with the judge's own verdict, so only the ones you DISAGREE with "
        "need an answer",
    )
    p.add_argument(
        "--calibrate",
        type=Path,
        default=None,
        metavar="DIR",
        help="run ONE calibration round against DIR/review.json: score the newest judge "
        "criterion against the person's marks, hand it the cards it got wrong, and file "
        "the revision as the next version under DIR/judge/",
    )
    p.add_argument(
        "--cards", type=Path, default=None, help="the cards.json a --review sample is drawn from"
    )
    p.add_argument(
        "--judge-from",
        type=Path,
        default=None,
        metavar="DIR",
        help="use the criterion calibrated in DIR, and skip the cards DIR already reviewed. "
        "This is how a FRESH batch checks a calibrated judge: the cards it was fitted on "
        "cannot tell you whether it learnt your standard or just those twenty answers",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=0,
        metavar="N",
        help="which sample --review draws. Change it for every fresh batch",
    )
    p.add_argument(
        "--sample", type=int, default=20, metavar="N", help="how many cards --review draws"
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        metavar="N",
        help="read N documents at once, and write up N terms at once. The calls do not "
        "depend on each other; raise it until the model server, not this process, is the "
        "thing that is busy",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if args.dump_prompts:
        args.dump_prompts.mkdir(parents=True, exist_ok=True)
        (args.dump_prompts / "card_extraction.md").write_text(built_in_prompt(), encoding="utf-8")
        (args.dump_prompts / "card_synthesis.md").write_text(
            built_in_synthesis_prompt(), encoding="utf-8"
        )
        print(
            f"wrote both prompts to {args.dump_prompts} — edit them, then pass them back",
            file=sys.stderr,
        )
        return

    if not (args.samples or args.tune_round or args.review or args.calibrate):
        raise SystemExit(
            "give --samples <folder>, --tune-round <dir>, --review <dir>, "
            "--calibrate <dir>, or --dump-prompts <dir>"
        )

    settings = load(config_path=args.config)
    llm = get_kb_llm(settings)
    if llm is None:
        # Loudly, and before anything runs: extraction IS the model call, so a
        # preview without one would write empty files and read like a corpus
        # that defines nothing.
        raise SystemExit("no retrieval LLM is configured (kb.retrieval_llm)")

    if args.review:
        import random

        from ..kb.cards.calibrate import best_judge_prompt
        from ..kb.cards.tune import defines_score

        home = args.judge_from or args.review
        already = home / "review.json"
        seen = {r["title"] for r in json.loads(already.read_text())} if already.is_file() else set()
        source = args.cards or (args.review / "cards.json")
        cards = [c for c in json.loads(source.read_text()) if c["title"] not in seen]
        random.Random(args.seed).shuffle(cards)  # not the first N — cards.json is key-sorted
        sample = [{"title": c["title"], "body": c["body"]} for c in cards[: args.sample]]
        criterion = best_judge_prompt(home)
        rejected = set(defines_score(llm, sample, prompt=criterion).get("does_not_define", []))
        args.review.mkdir(parents=True, exist_ok=True)
        (args.review / "review.json").write_text(
            json.dumps(
                [{**c, "judge": c["title"] not in rejected, "ok": None} for c in sample],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        fitted = (
            "the criterion you calibrated" if criterion != DEFINES else "the built-in criterion"
        )
        print(
            f"{args.review}/review.json — {len(sample)} cards, judged by {fitted}"
            + (f", skipping the {len(seen)} already reviewed in {home}" if seen else ""),
            file=sys.stderr,
        )
        print(
            '  Mark ONLY the ones you disagree with: set "ok" to true or false.',
            file=sys.stderr,
        )
        print("  Leave null where the judge got it right.", file=sys.stderr)
        print(
            "  If you stop part way, DELETE the rows you never looked at — a row left "
            "null counts as your agreement, and rows you did not read would inflate the score.",
            file=sys.stderr,
        )
        return

    if args.calibrate:
        from ..kb.cards.calibrate import calibrate

        version = calibrate(llm, rounds_dir=args.calibrate)
        for row in json.loads((args.calibrate / "judge" / "index.json").read_text()):
            wrong = f"   still wrong on: {', '.join(row['disagreed'])}" if row["disagreed"] else ""
            print(
                f"  judge v{row['version']}: agreed with you on "
                f"{row['agreement']}/{row['reviewed']}  ({row['agreement_rate']}){wrong}",
                file=sys.stderr,
            )
        print(f"wrote judge v{version + 1} — run again to score it", file=sys.stderr)
        return

    if args.tune_round:
        from ..kb.cards.tune import run_round

        shared = args.samples_from or args.tune_round
        version = run_round(
            llm,
            rounds_dir=args.tune_round,
            tune_dir=shared / "tune",
            holdout_dir=shared / "holdout",
            probes_dir=shared,
            batch=args.batch,
            holdout_every=args.holdout_every,
            concurrency=args.concurrency,
            synthesis_prompt=(args.synthesis_prompt.read_text() if args.synthesis_prompt else None),
        )
        print(f"scored v{version}, wrote v{version + 1}", file=sys.stderr)
        for row in json.loads((args.tune_round / "index.json").read_text()):
            print(
                f"  v{row['version']}: tune {row['tune']} | holdout {row['holdout']}",
                file=sys.stderr,
            )
        return

    cards = preview_samples(
        llm,
        args.samples,
        out_dir=args.out_dir,
        extract_prompt=args.extract_prompt.read_text() if args.extract_prompt else None,
        synthesis_prompt=args.synthesis_prompt.read_text() if args.synthesis_prompt else None,
        concurrency=args.concurrency,
    )
    summary = json.loads((args.out_dir / "summary.json").read_text())
    print(
        f"cards written to {args.out_dir}/  (the knowledge base was not touched)", file=sys.stderr
    )
    for key, value in summary.items():
        print(f"  {key}: {value}", file=sys.stderr)
    thin = [c.title for c in cards if len(c.sources) == 1][:8]
    if thin:
        print(f"  cards resting on ONE document: {', '.join(thin)}", file=sys.stderr)


if __name__ == "__main__":
    main()
