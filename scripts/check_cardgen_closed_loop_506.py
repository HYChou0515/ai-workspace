#!/usr/bin/env python
"""Live check (#506): does the reconcile net actually cluster + suppress with a
REAL embedding model?

The whole #506 closed loop hinges on one thing a fake embedder can NEVER prove:
that the production embedding model places two *different surface forms of the
same concept* close enough that the ``kb.cluster`` thresholds (τ) collapse them
into one review row (⑤) and auto-suppress a candidate the collection already
explains (⑥). ``HashEmbedder`` / the tests' ``_TagEmb`` are hashes — semantic
nearness is exactly what they cannot model — so this probe drives the SAME
reconcile code path the finalize step uses (``assign_cluster_key`` /
``grade_candidate``) against the real embedder resolved from your config, and
reports the OBSERVED cosine similarities so you can calibrate the shipped τ
knobs (``kb.cluster.cluster_tau`` / ``suppress_tau`` / ``update_tau``) to YOUR
model.

It stands up NOTHING else — no app, no index, no LLM, no collection on disk —
just a fresh in-memory ``make_spec`` + the embedder, so it is a focused probe you
can run against any reachable Ollama / hosted embedder.

Expectations (with well-separated domain pairs and the default conservative τ):

    same-concept, different words   -> SAME cluster   (sim >= cluster_tau)
    unrelated concept               -> NEW  cluster    (sim <  cluster_tau)
    candidate ≈ an existing card     -> grade "suppress" (sim >= suppress_tau)
    (the wiki axis below is a TERM-QUESTION verdict only — a card proposal is
     never graded against the wiki, #537; this script does not exercise
     reconcile_proposals, which is why it never caught that bug)
    candidate related-but-adds       -> grade "update"   (suppress_tau > sim >= update_tau)
    candidate unrelated to any card  -> grade "new"
    term documented in the wiki      -> grade "suppress" reason=wiki (deterministic)

A pair that lands on the WRONG side of τ is not necessarily a bug — it tells you
this embedder wants a different threshold; retune ``kb.cluster`` and re-run.

Usage (needs a reachable embedder; config resolves the model + base url):
    uv run python scripts/check_cardgen_closed_loop_506.py
    uv run python scripts/check_cardgen_closed_loop_506.py -c config.yaml

Verifying the OTHER half — the agentic drafter (P5) — is best done live in the
app, because it needs the real tool-calling model in the loop. #506/#577
follow-up changed what "correct" means here:

    The drafter's ``ask_knowledge_base`` now consults the GLOSSARY of existing
    cards ONLY — not RAG, not the wiki. Grading a card against the same corpus it
    was extracted from suppressed nearly everything (a big wiki ⇒ almost every
    term is "already explained" ⇒ ~0 cards drafted). So the drafter must draft a
    card whenever the document DEFINES a term, skipping only an EXACT
    already-carded duplicate.

Live procedure (needs your Ollama / hosted model, a wiki-heavy collection):

  1. Pick a collection with a wiki + ~hundreds of documents (the regime where the
     bug bit: many terms are written up in the wiki).
  2. Run card-gen over a batch of its documents.
  3. On the 待審核 tab, read the new "最近一次生成：讀 N 來源 → 抽 D 草稿 → 留 K 提案"
     summary (the persisted finalize funnel). BEFORE this fix D was ~0 (the
     drafter suppressed its own cards); AFTER it, D should be many, and terms that
     are explained in the wiki but had no card should now appear as proposals.
  4. Sanity: a term that ALREADY has a card should NOT be re-proposed (the
     glossary dedup), and card-vs-card near-duplicates are still collapsed by
     reconcile (the probe above). The "已自動略過" tab shows the KIND of each
     suppressed item, so a "reason: wiki" row is a QUESTION, never a card.

The funnel (D → K) is the signal: D jumping from ~0 to many is the drafter fix
taking effect; K is what survives reconcile. That path streams through the real
runner, so the tool-calling model is exercised end to end.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from specstar.util.vector_distance import cosine_distance

from workspace_app.config.loader import load_with_provenance
from workspace_app.factories import get_embedder
from workspace_app.kb.context_cards import norm
from workspace_app.kb.reconcile import (
    _card_text,
    _put_member,
    assign_cluster_key,
    grade_candidate,
)
from workspace_app.resources import ClusterMember, make_spec

_CID = "live-506"


@dataclass(frozen=True)
class _Pair:
    """One (existing concept, new candidate) probe. ``same`` is what we EXPECT of
    a well-calibrated embedder — printed alongside the observed verdict so a
    mismatch reads as 'retune τ', not a crash."""

    label: str
    existing_key: str
    existing_title: str
    candidate_key: str
    candidate_title: str
    expect_same: bool


# Domain pairs (SMT / electronics, matching the repo's other live probes). Each
# "same" pair is the same concept in different words; each "diff" pair is clearly
# unrelated. Swap in your own collection's vocabulary to calibrate for it.
_PAIRS: tuple[_Pair, ...] = (
    _Pair(
        "same-concept (void rate)",
        "solder void rate",
        "Solder void rate",
        "voiding in solder joints",
        "Voiding in solder joints",
        expect_same=True,
    ),
    _Pair(
        "same-concept (reflow profile)",
        "reflow profile",
        "Reflow thermal profile",
        "reflow oven temperature curve",
        "Reflow oven temperature curve",
        expect_same=True,
    ),
    _Pair(
        "unrelated (void vs BOM)",
        "solder void rate",
        "Solder void rate",
        "bill of materials",
        "Bill of materials (BOM)",
        expect_same=False,
    ),
)


def _seed_card(spec, embedder, key: str, title: str) -> None:
    """Project one existing CARD member (what a candidate is graded against)."""
    rm = spec.get_resource_manager(ClusterMember)
    nk = norm(key)
    vec = embedder.embed_documents([_card_text(nk, title)])[0]
    _put_member(
        rm,
        f"card:{nk}",
        collection_id=_CID,
        kind="card",
        ref_id=f"card-{nk}",
        run_id="",
        norm_key=nk,
        cluster_key=nk,
        state="active",
        embedding=vec,
        label=title,
    )


def _sim(embedder, a: str, b: str) -> float:
    va, vb = embedder.embed_documents([a, b])
    return 1.0 - cosine_distance(va, vb)


def _run(embedder, cluster_tau: float, suppress_tau: float, update_tau: float) -> bool:
    ok = True
    print(f"\n== clustering (cluster_tau={cluster_tau}) ==")
    for p in _PAIRS:
        spec = make_spec(default_user="u")
        _seed_card(spec, embedder, p.existing_key, p.existing_title)
        nk = norm(p.candidate_key)
        vec = embedder.embed_documents([_card_text(nk, p.candidate_title)])[0]
        cluster = assign_cluster_key(
            spec, collection_id=_CID, norm_key=nk, embedding=vec, tau=cluster_tau
        )
        joined = cluster == norm(p.existing_key)
        sim = _sim(
            embedder,
            _card_text(norm(p.existing_key), p.existing_title),
            _card_text(nk, p.candidate_title),
        )
        verdict = "SAME" if joined else "NEW"
        passed = joined == p.expect_same
        ok = ok and passed
        want = "SAME" if p.expect_same else "NEW"
        print(
            f"  [{'PASS' if passed else 'FAIL'}] {p.label:<32} sim={sim:.3f} "
            f"-> {verdict} (want {want})"
        )

    print(f"\n== grading (suppress_tau={suppress_tau}, update_tau={update_tau}) ==")
    # wiki-hit is deterministic — a cheap sanity that the net still fires.
    spec = make_spec(default_user="u")
    g = grade_candidate(
        spec,
        collection_id=_CID,
        embedding=[0.0],
        tau_high=suppress_tau,
        tau_update=update_tau,
        wiki_hit=True,
    )
    wiki_ok = g.action == "suppress" and g.reason == "wiki"
    ok = ok and wiki_ok
    tag = "PASS" if wiki_ok else "FAIL"
    print(f"  [{tag}] wiki-hit -> {g.action} reason={g.reason!r} (want suppress/wiki)")

    # near-card: grade a same-concept candidate against a seeded card.
    spec = make_spec(default_user="u")
    _seed_card(spec, embedder, "solder void rate", "Solder void rate")
    nk = norm("voiding in solder joints")
    vec = embedder.embed_documents([_card_text(nk, "Voiding in solder joints")])[0]
    g = grade_candidate(
        spec,
        collection_id=_CID,
        embedding=vec,
        tau_high=suppress_tau,
        tau_update=update_tau,
    )
    near_ok = g.action in ("suppress", "update")
    ok = ok and near_ok
    print(
        f"  [{'PASS' if near_ok else 'FAIL'}] near-card (same concept) -> {g.action} "
        f"reason={g.reason!r} (want suppress or update)"
    )

    # unrelated: grade a clearly-different candidate against the same seeded card.
    nk2 = norm("bill of materials")
    vec2 = embedder.embed_documents([_card_text(nk2, "Bill of materials (BOM)")])[0]
    g = grade_candidate(
        spec,
        collection_id=_CID,
        embedding=vec2,
        tau_high=suppress_tau,
        tau_update=update_tau,
    )
    new_ok = g.action == "new"
    ok = ok and new_ok
    print(f"  [{'PASS' if new_ok else 'FAIL'}] unrelated -> {g.action} (want new)")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="config.yaml (else $WORKSPACE_APP_CONFIG / ./config.yaml)",
    )
    args = ap.parse_args()

    settings, _prov = load_with_provenance(config_path=args.config)
    embedder = get_embedder(settings)
    c = settings.kb.cluster
    print(
        f"embedder={type(embedder).__name__}  "
        f"cluster_tau={c.cluster_tau} suppress_tau={c.suppress_tau} update_tau={c.update_tau}"
    )
    if type(embedder).__name__ == "HashEmbedder":
        print(
            "\nWARNING: the resolved embedder is HashEmbedder (no real model configured) —\n"
            "semantic nearness is meaningless; configure a real kb embedder to make this\n"
            "probe meaningful.\n"
        )
    ok = _run(embedder, c.cluster_tau, c.suppress_tau, c.update_tau)
    print(
        "\nAll PASS."
        if ok
        else "\nSome FAIL — a wrong-side-of-τ pair usually means retune "
        "kb.cluster for this embedder, not a bug."
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
