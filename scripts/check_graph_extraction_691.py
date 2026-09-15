"""Live check (#691): what does TODAY's extractor produce from a real slide?

The SQL probe beside this reads what is already STORED, which answers a different
question — those rows were written by whatever prompt was live when the job last
ran, and the extraction prompt last changed on 2026-07-24 (#630 P4/P7). A stored
row therefore cannot tell you whether the current code still does the thing, and
it cannot tell you WHY it did it: the input that produced it is not on the row.

So this samples real chunks, runs the real `extract_entities` against the
configured KB model, and prints the slide beside what came out of it. Input and
output together is the only form in which "the model treats 98.7% as a thing" is
a claim you can check rather than infer.

Deterministic: the sample is `kb.eval.sample.select_sample` (hash(seed, id)), so
the same seed re-derives the same slides after a prompt change — which is what
makes a before/after comparison mean anything. Nothing is written.

    uv run python scripts/check_graph_extraction_691.py --collection <id> -n 10
    uv run python scripts/check_graph_extraction_691.py -n 20 --seed after-fix
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from specstar import QB

from workspace_app.config.loader import load_with_provenance
from workspace_app.factories import get_kb_llm, get_spec
from workspace_app.kb.eval.sample import select_sample
from workspace_app.kb.graph.entity_extract import extract_entities
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.resources import DocChunk

# A surface that is only digits, separators, units and currency marks. Used to
# COUNT, never to filter anything in production: the #534 B lesson is that every
# heuristic invented over spelling was wrong within a corpus or two (a digit rule
# rejects 第2型糖尿病 against 第二型糖尿病). Here it is a reporting aid whose
# mistakes a human reads on the next line.
_PURE_VALUE = re.compile(r"^[\d\s.,:%/+~×x\-–—()$¥€£]*\d[\d\s.,:%/+~×x\-–—()$¥€£]*$")


def _chunks(spec, collection_id: str, seed: str, n: int) -> list[tuple[str, str]]:
    """`(chunk_id, text)` for a deterministic sample. Ids first (metas only), then
    the text for just the sampled ones — the corpus has tens of thousands of
    chunks and this must not drag them all through the resource store."""
    rm = spec.get_resource_manager(DocChunk)
    cond = QB.all() if not collection_id else (QB["collection_id"] == collection_id)
    ids = [meta.resource_id for meta in rm.iter_all(cond.build(), batch_size=1000)]
    if not ids:
        return []
    out: list[tuple[str, str]] = []
    for cid in select_sample(ids, seed, n):
        data = rm.get(cid).data
        text = getattr(data, "text", "") or ""
        if text.strip():
            out.append((cid, text))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--collection", default="", help="restrict to one collection id")
    ap.add_argument("-n", type=int, default=10, help="how many chunks to extract")
    ap.add_argument("--seed", default="691", help="sample seed; same seed = same slides")
    ap.add_argument("--chars", type=int, default=900, help="slide text shown per chunk")
    args = ap.parse_args(argv)

    settings, _ = load_with_provenance(config_path=args.config)
    spec = get_spec(settings)
    llm = get_kb_llm(settings)
    if llm is None:
        print("no kb llm configured (kb.retrieval_llm) — nothing to ask", file=sys.stderr)
        return 2

    sample = _chunks(spec, args.collection, args.seed, args.n)
    if not sample:
        print("no chunks matched — wrong collection id, or nothing indexed", file=sys.stderr)
        return 2

    totals = {"mentions": 0, "pure_value": 0, "long": 0, "chunks": 0}
    keys: set[str] = set()
    for chunk_id, text in sample:
        extraction = extract_entities(llm, text)
        print("=" * 78)
        print(f"CHUNK {chunk_id}")
        print("-" * 78)
        print(text[: args.chars].strip())
        if len(text) > args.chars:
            print(f"… (+{len(text) - args.chars} more chars)")
        print("-" * 78)
        if not extraction.mentions:
            print("  (nothing extracted)")
        for m in extraction.mentions:
            key = norm_surface(m.surface)
            keys.add(key)
            flags = []
            if _PURE_VALUE.match(key):
                flags.append("VALUE?")
                totals["pure_value"] += 1
            if len(key) > 20:
                flags.append("LONG")
                totals["long"] += 1
            print(f"  {(m.kind or '-'):14.14s} | {m.surface}  {' '.join(flags)}")
            totals["mentions"] += 1
        totals["chunks"] += 1
        print()

    n_chunks = totals["chunks"] or 1
    print("=" * 78)
    print(f"chunks extracted      {totals['chunks']}")
    per_chunk = totals["mentions"] / n_chunks
    print(f"mentions total        {totals['mentions']}  ({per_chunk:.1f}/chunk)")
    print(f"distinct keys         {len(keys)}  (repeats across slides = a real vocabulary)")
    print(f"  looks like a value  {totals['pure_value']}")
    print(f"  longer than 20 ch   {totals['long']}  (a phrase, not a name)")
    print()
    print("Read the flags as questions, not verdicts — the slide is printed above")
    print("each list so a wrong flag is visible on the next line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
