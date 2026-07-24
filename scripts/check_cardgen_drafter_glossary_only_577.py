#!/usr/bin/env python
"""Live check (#506/#577 follow-up): does the agentic drafter, driven by a REAL
tool-calling model, actually DRAFT cards for the terms a document defines —
instead of suppressing its own cards against the corpus/wiki?

Why this exists
---------------
The bug: the drafter's ``ask_knowledge_base`` used to search RAG + the wiki over
the SAME collection it extracts cards from, so nearly every term it drafted was
"already explained" and it declined to draft the card. Over a wiki-heavy
collection that collapsed ~1000 documents to ~5 proposals. The fix makes the
drafter's consultation GLOSSARY-ONLY (existing cards), so "already known" means
"already carded", never "the wiki/another doc mentions it".

The unit tests prove the STRUCTURE (the spec grants only ``lookup_glossary``; the
composition root can't re-enable corpus/wiki). What a fake LLM can NEVER prove is
that the real tool-calling model, driving the real agent loop with that spec,
actually emits cards. This probe drives the SAME wiring the app uses
(``wire_agentic_card_drafter``) against the model resolved from your config, over
a fresh in-memory spec (it stands up NO app, touches NO real collection), and:

  1. seeds ONE existing context card ("Reflow Zone 3") — the glossary;
  2. runs the real drafter over a document that DEFINES several domain terms,
     one of which ("Reflow Zone 3") is already carded;
  3. reports the drafted cards + questions, and checks:
       - it drafts at least one card (the anti-bug: it no longer suppresses all);
       - a term the document defines but has NO card (e.g. "SP-7") IS drafted;
       - the already-carded "Reflow Zone 3" is NOT re-proposed (glossary dedup).

A model that drafts nothing here is the bug reproducing (or an unreachable /
non-tool-calling model — the probe says which). LLM output varies, so the checks
are lenient: they assert the drafter PRODUCES cards, not an exact set.

The complementary before/after over a POPULATED corpus + wiki (proposals going
from ~0 to many) is the in-app procedure documented in
``scripts/check_cardgen_closed_loop_506.py`` — read the 待審核 tab's funnel.

Usage (needs a reachable, tool-calling model; config resolves it):
    uv run python scripts/check_cardgen_drafter_glossary_only_577.py
    uv run python scripts/check_cardgen_drafter_glossary_only_577.py -c config.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.config.loader import load_with_provenance
from workspace_app.factories import get_agent_config_catalog, get_runner
from workspace_app.kb.card_drafter import NullCardDrafter
from workspace_app.kb.card_gen import CardDrafter, DocDigest
from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.retriever import Retriever
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, ContextCard

_CID = "live-577"

# A document that DEFINES three domain terms. "Reflow Zone 3" is pre-carded below
# (glossary) → should be skipped; the other two are novel → should be drafted.
_DOC = (
    "Reflow Zone 3 (RZ3) is the third heating zone of the reflow oven, held near "
    "245 C to reach peak reflow. Solder Paste SP-7 is a no-clean Type-4 solder "
    "paste used for fine-pitch stencil printing. The MSL rating (Moisture "
    "Sensitivity Level) classifies how long a component may sit out of dry-pack "
    "before it must be baked prior to reflow."
)


def _seed_glossary_card(spec) -> None:
    """Project one EXISTING card so the drafter's glossary lookup can find it."""
    rm = spec.get_resource_manager(ContextCard)
    keys = ["Reflow Zone 3", "RZ3"]
    rm.create(
        ContextCard(
            collection_id=_CID,
            keys=keys,
            norm_keys=derive_norm_keys(keys),
            title="Reflow Zone 3",
            body="The third heating zone of the reflow oven.",
        )
    )


def _capture_drafter(spec, runner, catalog: AgentConfigCatalog, kb_cfg) -> CardDrafter:
    """Wire the drafter EXACTLY as the app does, and capture what it swaps in — so
    this probe runs the shipped glossary-only spec, not a hand-rolled one."""
    from workspace_app.api.card_drafter_agent import wire_agentic_card_drafter

    captured: dict[str, CardDrafter] = {}

    class _Capture(CardGenCoordinator):
        def set_drafter(self, drafter: CardDrafter) -> None:
            captured["drafter"] = drafter

    wire_agentic_card_drafter(
        _Capture(spec, NullCardDrafter()),
        spec=spec,
        runner=runner,
        retriever=Retriever(spec, embedder=get_embedder_or_hash(spec)),
        catalog=catalog,
        kb_agent_config=kb_cfg,
        max_searches=3,  # a non-zero budget that must NOT leak into the drafter
    )
    return captured["drafter"]


def get_embedder_or_hash(spec):  # noqa: ANN001, ANN201 — probe helper
    """The retriever is only a bridge dependency here (glossary-only never searches
    it), so any embedder of the right width is fine."""
    from workspace_app.kb.embedder import HashEmbedder

    return HashEmbedder(dim=EMBED_DIM)


def _report(digest: DocDigest) -> bool:
    cards = digest.cards
    questions = digest.term_questions + digest.description_questions
    print(f"\n== drafter output: {len(cards)} card(s), {len(questions)} question(s) ==")
    for c in cards:
        print(f"  card: keys={c.keys} title={c.title!r}")
    for q in digest.term_questions:
        print(f"  term-question: {q.term!r} — {q.question}")

    all_keys = " ".join(k for c in cards for k in c.keys).lower()
    drafted_any = len(cards) >= 1
    drafted_novel = "sp-7" in all_keys or "sp7" in all_keys or "msl" in all_keys
    # Soft: the model MAY still re-draft a carded term; warn rather than fail.
    re_drafted_carded = "rz3" in all_keys or "reflow zone 3" in all_keys

    print()
    print(
        f"  [{'PASS' if drafted_any else 'FAIL'}] drafts at least one card "
        "(no longer suppresses everything)"
    )
    print(
        f"  [{'PASS' if drafted_novel else 'WARN'}] drafts a defined-but-uncarded term (SP-7 / MSL)"
    )
    print(
        f"  [{'WARN' if re_drafted_carded else 'PASS'}] the already-carded "
        "'Reflow Zone 3' was "
        f"{'RE-PROPOSED (glossary dedup is soft)' if re_drafted_carded else 'skipped'}"
    )
    return drafted_any


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", "-c", type=Path, default=None, help="config.yaml")
    args = ap.parse_args()

    settings, _prov = load_with_provenance(config_path=args.config)
    runner = get_runner(settings)
    catalog = get_agent_config_catalog(settings)
    kb_chats = catalog.kb_chats()
    if not kb_chats:
        from workspace_app.config.catalog_build import build_catalog
        from workspace_app.config.schema import Settings as _Bundled

        kb_chats = build_catalog(_Bundled(), config_dir=None).kb_chats()
    kb_cfg = kb_chats[0]
    print(f"model={kb_cfg.model!r}  base_url={kb_cfg.llm_base_url or '(default)'!r}")

    spec = make_spec(default_user="probe")
    _seed_glossary_card(spec)
    drafter = _capture_drafter(spec, runner, catalog, kb_cfg)

    try:
        digest = drafter.digest(doc_path="probe.md", doc_text=_DOC, collection_id=_CID)
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not crash
        print(f"\nFAIL: the drafter run errored — {type(exc).__name__}: {exc}")
        print(
            "If this is a connection error, the model in your config is not "
            "reachable; if it is a tool-call/parse error, the model may not "
            "support tool calling well enough for the agent loop."
        )
        raise SystemExit(1) from exc

    ok = _report(digest)
    print(
        "\nAll PASS."
        if ok
        else "\nFAIL — the drafter produced NO cards for a "
        "document that clearly defines terms; the self-suppression may have "
        "regressed, or the model is not tool-calling."
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
