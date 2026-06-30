# Plan — #328 Findability probe modal (interactive prompt-tuning playground)

> Grill-locked. Supersedes the old #333 "auto batch per-doc scoring" framing —
> that whole persistence/coordinator/list-sort stack is **deferred**. We build the
> interactive, **read-only** probe the user actually wants. Filed under **#328**.

## What the user wants (the real pain)

> "We struggle on how to give the AI a *good prompt* so it generates *good chunks*."

A KB doc (e.g. a PPT) is turned into chunks by an **AI parser** (the VLM describer:
slide/image/pdf → described Markdown → chunks). The user authors a per-collection
**guidance prompt** appended to every prompt-driven parser's base prompt (e.g.
"if you see a fishbone diagram → emit JSON; a table → emit Markdown"), and needs a
tool to **iterate** on that guidance: type a representative question, see where this
doc's chunks rank in the real retriever, tweak the guidance, re-parse **this one doc**,
and see whether the ranks improved.

## Locked design

- **Read-only modal, zero DB writes.** Editing the question or the guidance recomputes
  ranks live and shows them; it **never** persists. The only write is a separate
  **Apply** action (save guidance to the collection) — plain specstar `PATCH /collection/{id}`,
  not the modal's exploration path.
- **Per-doc what-if.** Changing the guidance re-parses **only this doc** (via the #338
  `Overlay`: this doc's chunks swapped for the re-parsed virtual ones, the rest of the
  collection held fixed). The user accepts that extrapolating one doc's tuning to the
  whole collection has variance.
- **A single per-collection guidance prompt, appended to every prompt-driven parser.**
  New `Collection.parser_guidance: str` (non-indexed, blank ⇒ no append, no migration),
  the 5th sibling of `quality_rubric` / `wiki_*_guidance`.
- **No score in v1.** Just chunk **ranks** (deep, e.g. #3 / #47 / >50), before vs after.
  hit@k / MRR / list badge / sort / persisted `findability_score` / background coordinator
  are all **deferred** (the #333 batch idea).

## Deferred (explicitly out of v1)

Auto per-chunk question generation (self-retrieval), hit@k/MRR scoring, persisted
`findability_score`, Schema migration, background `FindabilityCoordinator`, documents-list
badge/sort. Revisit once the prompt-tuning loop proves useful.

## Phases (flat integers)

- **P1 — guidance append seam.** `Collection.parser_guidance: str`. `IParser.uses_guidance()`
  declaration (default `False`). `VlmDescriber.describe(..., guidance="")` appends guidance
  after the base prompt. The VLM parsers (image/slide/svg/pdf) override `uses_guidance()` →
  `True` and accept an opt-in `guidance: str = ""` kwarg threaded to the describer. The
  ingestor resolves `collection.parser_guidance` once and threads it into BOTH parse sites
  (`_index_via_pipeline`, `index_units`) via an opt-in bridge mirroring the `config` one.
- **P2 — dry-run re-parse helper.** `Ingestor.dry_run_chunks(doc_id, *, guidance) ->
  (list[DocChunk], virtual_text)` — re-parse this doc with a candidate guidance, run the
  same pipeline (split + embed), build in-memory `DocChunk`s (NOT persisted), return them +
  the re-joined virtual text. Reuses the parse + `pipeline.run` + chunk-build logic factored
  out of `_emit_packet`.
- **P3 — deep-rank retrieval.** `Retriever.search(..., depth: int | None = None)`: when set,
  widen the internal candidate / MMR caps to `depth` and return the full ranked passage list
  (`[:depth]`) instead of `[:top_k]`. Default `None` ⇒ byte-for-byte current behaviour.
- **P4 — probe service + endpoint.** `POST /kb/findability/probe` body
  `{doc_id, question, guidance?: str|null, depth?}` → `ProbeResult{ top_k, before, after? }`.
  `before` ranks the doc's current chunks for the question (deep search). `after` (only when
  `guidance` given) re-parses via P2 → `Overlay` deep search → ranks. Each side reports the
  doc's chunks (seq, text, rank|None, in_top_k) + best_rank. Read-only; typed pydantic models.
- **P5 — FE client + types.** `KbApi.probeFindability(...)`; types mirroring the response;
  `updateCollection({parser_guidance})` (reuse existing PATCH) for Apply.
- **P6 — FE modal.** Opened from `KbDocIde` doc viewer: question input, current-chunk ranks
  (before), guidance editor (prefilled from collection), "Re-parse this doc" (after) with a
  progress note, before/after rank columns, "Apply to collection" button. vitest + TanStack
  Query.
- **P7 — docs + live check.** Update `docs/subsystems/kb-retrieval-agent.md`; Ollama live
  canned check (real VLM re-parse + retrieval); plan progress.

## Notes / invariants

- LLM/VLM calls stream (`collect`/`stream`); no non-streaming chat.
- 100% coverage gate (full local suite); CI runs unit only.
- The probe re-parse re-runs the VLM per slide → seconds–minutes for a big doc; the modal
  shows it as an explicit "running…" action (the user accepted this).

## Progress

- [x] P1  - [x] P2  - [x] P3  - [x] P4  - [x] P5  - [x] P6  - [ ] P7
