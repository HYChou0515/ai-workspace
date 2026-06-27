# Plan — #105 評分 docs (document quality scoring)

AI judges each doc's quality **as a knowledge source**, per collection; the web shows
good/bad; `kb_search` down-weights bad docs. Grill-locked design (see the issue's
`/grill-me` thread). Flat integer phases, TDD red-green-refactor.

## Locked design

- **Rubric is user-authored, per collection.** A `quality_rubric` prompt field on the
  `Collection` resource (non-indexed, edited like the wiki guidance, #90). Empty ⇒ the
  collection is **not scored** and search is unaffected (opt-in). Two-layer prompt: the
  user's criteria (what "good" means + the named dimensions to assess) + a **system-fixed
  output format** (overall `score` 0–100 + per-dimension `breakdown` + `rationale`).
- **AI returns a holistic overall `score`** (0–100); the system uses it directly for the
  search weight. `breakdown` (dynamic, user-named dims) + `rationale` are display-only.
- **Scoring runs automatically at index time** (KB core, not a workflow), only when the
  collection has a rubric. It runs **async, AFTER `status="ready"`** — the doc is usable /
  searchable immediately at neutral weight, the score lands later. **A judge failure leaves
  the doc un-scored; it never fails indexing.** One scoring pass per doc, at fan-out
  finalize (#227).
- **Method = chunk-based windowed map-reduce.** Pack as many of the doc's existing chunks
  as fit the model context into a window; assess each window against the rubric (map);
  one final LLM call synthesises the windows into the doc-level `{score, breakdown,
  rationale}` (reduce). Reads every chunk; call count bounded by window size. All judge
  calls **stream** (`feedback_always_stream_llm`).
- **Search weight = second-phase additive document prior.** Literature: Craswell,
  Robertson, Zaragoza & Taylor, *Relevance Weighting for Query-Independent Evidence*
  (SIGIR 2005); Kraaij, Westerveld & Hiemstra, *The Importance of Prior Probabilities for
  Entry Page Search* (SIGIR 2002); Zhou & Croft, *Document Quality Models for Web Ad Hoc
  Retrieval* (CIKM 2005); Vespa/Azure phased ranking. **Keep RRF as the first-phase
  candidate generator**; re-rank candidates by `final = R + w · (sat(score/100) − 0.5)`
  where `R` is the **normalized cosine** of the candidate chunk to the query (qv + chunk
  vec already in hand for MMR), `sat` is a saturating transform, the prior is **centered**
  so an un-scored doc contributes **+0 = neutral midpoint** (not "worst"), `w` is **small**
  relative to the spread of `R`, and it is **soft — never a hard filter** (an absolute
  floor is available but **off by default**). These citations go in the code comment at the
  re-rank site AND the PR body (`feedback_cite_literature`).
- **Un-scored = neutral default; no mandatory backfill.** Re-indexing re-scores; a manual
  "re-score" button is **out of v1**. A rubric change does not auto-clear or auto-rescore
  in v1 (stale-but-usable signal accepted).
- **UI (v1)**: per-doc quality badge (score + good/ok/bad label) + click-to-expand
  `rationale`; **sort the document list by quality**; edit the rubric in the collection
  settings (like the wiki guidance empty-state). Un-scored ⇒ "—". Breakdown radar +
  collection-level summary are future.
- **Storage**: `SourceDoc` Schema v4→v5 adds `quality_score: int | None` (None = un-scored,
  **indexed** for sort + retriever batch-load), `quality_breakdown: dict[str, Any]`
  (non-indexed, dynamic dims), `quality_rationale: str`. The migrate step only registers
  the new index (no LLM); pre-existing rows stay un-scored (None) until re-indexed.
- **Model/config**: a new pluggable `kb.quality_judge` model role (local small Qwen via
  LiteLLM, `feedback_llm_choice`). `w`, the saturating curve, the window budget, and the
  (off-by-default) hard floor are conservative defaults in `config.example.yaml` — never
  touch `config.yaml` (`feedback_config_yaml_offlimits`).

**Explicitly deferred**: human override of the score, per-chunk quality weighting,
auto-rescore on rubric change, score-but-don't-weight toggle, breakdown radar, collection
summary.

## Phases (flat)

- **P1 — Storage**: add `quality_score | quality_breakdown | quality_rationale` to
  `SourceDoc`; Schema v4→v5 with a reindex-only step registering the `quality_score` index.
  Tests: model defaults, index registration, migrate leaves old rows un-scored.
- **P2 — Rubric field**: add `quality_rubric` to `Collection` (non-indexed). CRUD/read +
  write path. Tests: round-trip, empty default.
- **P3 — Judge model role + scoring engine**: `kb.quality_judge` wiring; a pure
  `QualityScorer` doing windowed map-reduce over a doc's chunks with a scripted LLM
  (system format + injected rubric dims). Tests: window packing, map→reduce synthesis,
  output parse/clamp, empty-rubric skip, judge-error ⇒ un-scored.
- **P4 — Index wiring**: run the scorer async after `status="ready"` at finalize, only if
  the collection has a rubric; persist score/breakdown/rationale; failure-safe. Tests:
  scored after ready, no-rubric skip, judge failure keeps doc ready + un-scored, scored
  once per doc.
- **P5 — Retrieval prior**: second-phase additive prior in `retriever.py` (normalized
  cosine `R` + centered saturating quality, batch-load candidate doc scores, small `w`,
  soft). Config knobs in `config.example.yaml` + literature comment. Tests: bad doc
  demoted, un-scored neutral (+0), strong relevance still beats a quality gap, no hard
  exclusion, no-score collection unchanged.
- **P6 — API**: `DocumentRow` gains `quality_score/label/rationale`; `list_documents`
  gains a quality sort; rubric read/write endpoint (typed pydantic). Tests: fields
  surfaced, sort order, rubric round-trip.
- **P7 — FE**: quality badge + label + rationale expander on the document list/IDE; sort
  control; rubric editor in collection settings; `KbDocument` type. vitest
  (`feedback_fe_tdd`) + tsc + build.
- **P8 — DoD**: live canned check with a real qwen (`feedback_llm_features_need_live_checks`)
  where the environment allows; full suite + 100% coverage gate; ruff/ty/format; PR body
  with the literature citations.
