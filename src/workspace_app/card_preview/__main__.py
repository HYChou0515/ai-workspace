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

    if not args.samples:
        raise SystemExit("give --samples <folder>, or --dump-prompts <dir>")

    settings = load(config_path=args.config)
    llm = get_kb_llm(settings)
    if llm is None:
        # Loudly, and before anything runs: extraction IS the model call, so a
        # preview without one would write empty files and read like a corpus
        # that defines nothing.
        raise SystemExit("no retrieval LLM is configured (kb.retrieval_llm)")

    cards = preview_samples(
        llm,
        args.samples,
        out_dir=args.out_dir,
        extract_prompt=args.extract_prompt.read_text() if args.extract_prompt else None,
        synthesis_prompt=args.synthesis_prompt.read_text() if args.synthesis_prompt else None,
    )
    summary = json.loads((args.out_dir / "summary.json").read_text())
    print(f"wrote {args.out_dir}/ — nothing was stored", file=sys.stderr)
    for key, value in summary.items():
        print(f"  {key}: {value}", file=sys.stderr)
    thin = [c.title for c in cards if len(c.sources) == 1][:8]
    if thin:
        print(f"  cards resting on ONE document: {', '.join(thin)}", file=sys.stderr)


if __name__ == "__main__":
    main()
