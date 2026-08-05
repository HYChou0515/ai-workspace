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

from ..config.loader import load
from ..factories import get_kb_llm, get_spec
from ..kb.graph.preview import preview_collection


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.graph_preview",
        description="Build one collection's knowledge graph in memory and write it as JSON. "
        "Reads only — nothing is stored.",
    )
    p.add_argument("collection_id", help="the collection to preview")
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
        "--as-user",
        default=None,
        help="read as this user (default: the configured default user). Access scopes apply, "
        "so a preview shows the corpus THIS user can see — say so rather than quietly "
        "previewing a subset",
    )
    p.add_argument(
        "--propose-merges",
        action="store_true",
        help="also run the merge-proposal pass (off by default: it only adds review work, "
        "and when the question is what the extractor did it is noise on top of the answer)",
    )
    return p.parse_args(argv)


def main() -> None:
    args = _parse_args()
    settings = load(config_path=args.config)
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

    graph = preview_collection(
        spec,
        llm,
        args.collection_id,
        out_dir=args.out_dir,
        guidance=guidance,
        propose_with=llm if args.propose_merges else None,
    )
    summary = json.loads((args.out_dir / "summary.json").read_text())
    print(f"wrote {args.out_dir}/ — nothing was stored", file=sys.stderr)
    for key, value in summary.items():
        if key != "kinds":
            print(f"  {key}: {value}", file=sys.stderr)
    top = list(summary["kinds"].items())[:8]
    print(f"  kinds (top {len(top)}): {', '.join(f'{k}×{n}' for k, n in top)}", file=sys.stderr)
    print(f"  identities: {len(graph.entities)}", file=sys.stderr)
    print(
        "  note: identities are grouped over THIS collection only; production "
        "groups across the whole corpus",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
