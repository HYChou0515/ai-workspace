"""CLI glue for the read-only graph preview (#697).

    uv run python -m workspace_app.graph_preview <collection-id> -o ./graph-preview

Writes ``summary.json`` plus one file per layer and touches nothing in the
store. ``--guidance-file`` runs a candidate extraction criterion WITHOUT
committing it to the collection, which is the loop the tool exists for: write a
criterion, preview, diff against the previous run, adjust.

Settings-driven composition, omitted from coverage like the other CLI roots; the
seams it calls are unit-tested in ``kb.graph.preview``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config.loader import _resolve_config_path, load
from ..factories import get_kb_llm, get_spec
from ..kb.graph.preview import preview_collection
from . import unusable_config


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.graph_preview",
        description="Build one collection's knowledge graph in memory and write it as JSON. "
        "Reads only — nothing is stored.",
    )
    p.add_argument(
        "collection_id",
        nargs="?",
        help="the collection to preview or sample from (omit with --samples)",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("./graph-preview"),
        help="where the JSON goes (default: ./graph-preview)",
    )
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    p.add_argument(
        "--guidance-file",
        type=Path,
        default=None,
        help="a candidate extraction criterion to run INSTEAD of the collection's own, "
        "so it can be tried before it is committed anywhere",
    )
    p.add_argument(
        "--samples",
        type=Path,
        default=None,
        help="a folder of .txt files to run against INSTEAD of a collection. No store is "
        "opened at all — this is the tuning half of the loop, and it is meant to be fast",
    )
    p.add_argument(
        "--dump-samples",
        type=int,
        default=0,
        metavar="N",
        help="draw N documents at random from the collection and write them to <out>/tune/ "
        "as text. One read-only read of the real corpus; everything after it runs offline",
    )
    p.add_argument(
        "--holdout",
        type=int,
        default=0,
        metavar="N",
        help="draw a FURTHER N documents to <out>/holdout/, disjoint from the tuning set. "
        "A criterion tuned on the passages you looked at works on the passages you looked "
        "at; this is what tells the difference",
    )
    p.add_argument("--seed", type=int, default=0, help="fixes the sample draw (default 0)")
    p.add_argument(
        "--tune-round",
        type=Path,
        default=None,
        metavar="DIR",
        help="run ONE prompt-improvement round in DIR: score the newest unscored prompt on "
        "DIR/tune and DIR/holdout, ask the model for a revision, and file it as the next "
        "version. Trigger it as often as you like — every version is kept and scored on "
        "both sets, so a later reader can see where tuning became overfitting and walk back",
    )
    p.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="the WHOLE extraction prompt, replacing the built-in one. Must contain {text}; "
        "{guidance} is optional. Start from --dump-prompt",
    )
    p.add_argument(
        "--dump-prompt",
        type=Path,
        default=None,
        metavar="PATH",
        help="write the built-in extraction prompt to PATH and exit, as a starting point to edit",
    )
    p.add_argument(
        "--chunk-tokens",
        type=int,
        default=256,
        help="how large a passage the model is asked about, in whitespace tokens, when "
        "running against --samples. CJK is not written with word spaces, so a Chinese "
        "corpus gets passages several times this (default 256)",
    )
    p.add_argument(
        "--as-user",
        default=None,
        help="read AS this user — only what they may READ (not merely discover), so the "
        "preview shows the corpus THEY can see. Omitted, the reads are unscoped — the "
        "operator's own view of the whole collection",
    )
    p.add_argument(
        "--propose-merges",
        action="store_true",
        help="also run the merge-proposal pass (off by default: it only adds review work, "
        "and when the question is what the extractor did it is noise on top of the answer)",
    )
    return p.parse_args(argv)


def _report(out_dir, graph, *, file) -> None:
    """The numbers, on stderr, so the JSON on disk stays the artefact."""
    summary = json.loads((out_dir / "summary.json").read_text())
    print(f"wrote {out_dir}/ — nothing was stored", file=file)
    for key, value in summary.items():
        if key != "kinds":
            print(f"  {key}: {value}", file=file)
    top = list(summary["kinds"].items())[:8]
    print(f"  kinds (top {len(top)}): {', '.join(f'{k}×{n}' for k, n in top)}", file=file)
    print(f"  identities: {len(graph.entities)}", file=file)


def main() -> None:
    args = _parse_args()

    # The two paths that need no store at all, handled before anything opens one.
    if args.dump_prompt:
        from ..kb.graph.entity_extract import built_in_prompt

        args.dump_prompt.parent.mkdir(parents=True, exist_ok=True)
        args.dump_prompt.write_text(built_in_prompt())
        print(f"wrote {args.dump_prompt} — edit it, then pass --prompt-file", file=sys.stderr)
        return

    prompt = args.prompt_file.read_text() if args.prompt_file else None
    if args.tune_round:
        from ..kb.graph.tune import run_round

        settings = load(config_path=args.config)
        llm = get_kb_llm(settings)
        if llm is None:
            raise SystemExit("no retrieval LLM is configured (kb.retrieval_llm)")
        version = run_round(
            llm,
            rounds_dir=args.tune_round,
            tune_dir=args.tune_round / "tune",
            holdout_dir=args.tune_round / "holdout",
            chunk_tokens=args.chunk_tokens,
        )
        print(f"scored v{version}, wrote v{version + 1}", file=sys.stderr)
        for row in json.loads((args.tune_round / "index.json").read_text()):
            print(
                f"  v{row['version']}: tune {row['tune']} | holdout {row['holdout']}",
                file=sys.stderr,
            )
        return

    if args.samples:
        from ..factories import get_kb_llm as _llm_for_samples
        from ..kb.graph.preview import preview_samples

        settings = load(config_path=args.config)
        llm = _llm_for_samples(settings)
        if llm is None:
            raise SystemExit("no retrieval LLM is configured (kb.retrieval_llm)")
        graph = preview_samples(
            llm,
            args.samples,
            out_dir=args.out_dir,
            prompt=prompt,
            max_tokens=args.chunk_tokens,
        )
        _report(args.out_dir, graph, file=sys.stderr)
        return

    if not args.collection_id:
        raise SystemExit("give a collection id, or --samples <folder>")

    # Say which file is in force BEFORE anything reads it. `load()` falls back
    # to `./config.yaml` in the CURRENT directory and then to bundled defaults,
    # so the same command run from two directories reads two different corpora
    # with nothing on screen to say so.
    import os

    resolved = _resolve_config_path(args.config, os.environ)
    print(f"config: {resolved if resolved else '(none — bundled defaults)'}", file=sys.stderr)
    settings = load(config_path=args.config)
    refusal = unusable_config(settings, resolved)
    if refusal:
        raise SystemExit(f"graph_preview: {refusal}")
    spec = get_spec(settings, (lambda: args.as_user) if args.as_user else None)
    llm = get_kb_llm(settings)
    if llm is None:
        # Loudly, and before anything runs: extraction IS the model call, so a
        # preview without one would write empty files and look like a corpus
        # nothing could be found in.
        raise SystemExit(
            "no retrieval LLM is configured (kb.retrieval_llm), and the preview "
            "cannot extract without one"
        )
    guidance = args.guidance_file.read_text() if args.guidance_file else None

    if args.dump_samples or args.holdout:
        from ..kb.graph.preview import dump_samples

        tune, held = dump_samples(
            spec,
            args.collection_id,
            out_dir=args.out_dir,
            tune=args.dump_samples,
            holdout=args.holdout,
            as_user=args.as_user,
            seed=args.seed,
        )
        print(
            f"wrote {tune} tuning and {held} holdout documents to {args.out_dir}/ — "
            "nothing was stored. Iterate with --samples, and keep the holdout unread "
            "until you think you are done",
            file=sys.stderr,
        )
        return

    graph = preview_collection(
        spec,
        llm,
        args.collection_id,
        out_dir=args.out_dir,
        guidance=guidance,
        propose_with=llm if args.propose_merges else None,
        as_user=args.as_user,
        prompt=prompt,
    )
    _report(args.out_dir, graph, file=sys.stderr)


if __name__ == "__main__":
    main()
