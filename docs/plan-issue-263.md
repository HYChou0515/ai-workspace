# Plan — #263 KB provenance 精確查詢（結構化過濾，非語意檢索）

Follow-up split from #254. #254 stored `page` / `section` / `sheet` / `line` /
`slide` provenance on `DocChunk` (display + section-fold-into-embedding only).
#263 adds **deterministic structural filtering** so a question like
"分析某檔第 30 頁" or "為什麼 XXX，根據第 30-90 頁的內容" narrows the chunk
candidate set by location, then ranks.

## Locked decisions (via /grill-me)

1. **Trigger — agent-driven.** The KB agent recognises the location intent from
   the user's natural-language message and calls the filtered path. No UI page/
   file picker (chat-first, agentic architecture; FE untouched).

2. **Shape — extend `kb_search`, not a new tool.** The structural filter is an
   optional **scope applied to vector retrieval**, not a separate path. Proven by
   `retriever._dense_order`, which already combines an index filter
   (`collection_id`) with a vector-distance sort in ONE specstar query. So
   "範圍內的語意檢索" (filter + vector rank) is exactly what `kb_search` should do.
   Three query shapes all served by one tool:
   - ① pure semantic (no filter) — today's behaviour.
   - ② **scoped semantic** ("為什麼 XXX，據 30-90 頁") — filter + vector rank.
   - ③ pure positional ("看 Summary 分頁") — tight filter, top_k of the scope.

3. **Index — per-locator typed `IndexableField` on `provenance.<key>`.**
   No model-body change, no promoting `page` to a top-level field.
   Verified against specstar 0.11.9:
   - `_extract_by_path` (`resource_manager/core.py`) walks INTO a `dict`
     (`elif isinstance(current, dict) and part in current`), so
     `field_path="provenance.page"` extracts the dict subkey natively.
   - Range queries are correct on every backend: `gt/gte/lt/lte/between` cast to
     `(indexed_data->>'field')::numeric` (Postgres) / `CAST(json_extract(...) AS
     REAL)` (SQLite) / native Python compare (in-memory). The cast is driven by
     the **operator**, not `field_type`. `==`/`in_` use text compare (correct for
     ints). So a single `list[str]` token field was REJECTED — it can't express
     the 30-90 range.
   - Production backend is Postgres (`factories.py`).

4. **Document axis — `source_doc_id` + indexed `SourceDoc.path`.**
   `source_doc_id` is `encode_doc_id(collection_id, path)` — the path-derived key
   already living on every chunk and already indexed. So filtering
   `WHERE source_doc_id == X AND page BETWEEN lo AND hi` is one indexed query with
   path and page co-located. The only new piece is resolving the agent's
   **filename** → `X`:
   - The AI's interface currency is the **filename only**. The `∕` (U+2215)
     separator and the opaque id stay entirely server-side (a footgun for the LLM
     to ever construct). We read the resolved `SourceDoc`'s existing
     `.info.resource_id` — never hand-build the id.
   - Resolution is an **indexed** `SourceDoc.path` query within `collection_ids`
     (exact or basename via `.contains`/LIKE); not-found / ambiguous → a
     recoverable error string (mirrors `resolve_collection`).
   - We do NOT denormalise a readable `path` onto each chunk: old chunks have no
     `path` in their provenance, so migrate could not synthesise it → it would
     force a full re-index (re-chunk + re-embed), which #263 forbids.
     `source_doc_id` is already on every old chunk, so no re-index is needed.

5. **Params + v1 scope.** `kb_search` gains optional:
   - `document: str` — filename (required whenever any location filter is set).
   - `page_from: int` / `page_to: int` — page range (single page = `page_from`
     only, or both equal). Two typed ints, NOT a `"30-90"` string (LLM-friendly).
   - `sheet: str` — exact sheet name.
   v1 covers **page range + sheet exact** (the issue's two concrete examples:
   PDF page / Excel sheet). `slide` / `row` / `jsonl_line` indexes are built now
   (generic, migrate-backfilled, data ready) but their tool params are deferred
   to a later phase (smaller LLM surface = fewer mis-fills).

6. **Output / ranking.** The filter is pushed into BOTH `_dense_order` (specstar
   query predicates) AND `_load_chunks` (so the BM25 / MMR corpus is scoped too).
   The rest of the pipeline (RRF → MMR → parent-doc merge → top_k) is unchanged,
   and the `[n]` citation registry + `format_location` header are reused as-is.
   top_k unchanged for v1.

7. **Budget.** A filtered search is still one `kb_search` call → consumes one
   `kb_search_max_calls` unit; `expand`/`hyde`/`rerank` still apply.

8. **Surfaces.** Zero new wiring — every surface with `kb_search` (KB chat; RCA
   via `ask_knowledge_base`; topic-hub) gets it for free. The tool docstring
   teaches the LLM when to fill `document` / `page_from` / `page_to` / `sheet`.

9. **Backfill — bump schema to `v3`, re-extract via migrate (no re-parse).**
   Production data is mostly version `None` with **some already at `v2`**. Adding
   an index to an already-`v2` model would NOT re-extract the existing-`v2` rows
   (they think they are current), so the new index would miss them. Bumping to
   `v3` with steps from BOTH `None` and `v2` forces every row to re-extract
   `indexed_data`.

   ```python
   # SourceDoc: v2 → v3, re-extract None (bulk) + v2 (some), add path index
   spec.add_model(
       Schema(SourceDoc, "v3")
           .step(None, _reindex_only, to="v3", source_type=SourceDoc)
           .step("v2", _reindex_only, to="v3", source_type=SourceDoc),
       indexed_fields=[
           "collection_id",
           IndexableField("content.size", index_key="content_size"),
           IndexableField("path", str),
       ],
   )

   # DocChunk: no prior Schema (all None) → adopt v3 directly; cover v2 defensively
   spec.add_model(
       Schema(DocChunk, "v3")
           .step(None, _reindex_only, to="v3", source_type=DocChunk)
           .step("v2", _reindex_only, to="v3", source_type=DocChunk),
       indexed_fields=[
           "source_doc_id", "collection_id",
           IndexableField("provenance.page",  int, index_key="page"),
           IndexableField("provenance.slide", int, index_key="slide"),
           IndexableField("provenance.sheet", str, index_key="sheet"),
           IndexableField("provenance.row",   int, index_key="row"),
           IndexableField("provenance.jsonl_line", int, index_key="line"),
       ],
   )
   ```

   A declared `v2→v3` edge is harmless if no `v2` row exists. After deploy the
   operator runs `POST /doc-chunk/migrate/execute` and
   `POST /source-doc/migrate/execute` (specstar `MigrateRouteTemplate`, mounted
   globally). Never hand-roll a reindex loop.

## Phases (flat integer, /tdd red-green; backend-only, FE untouched)

- **P1 — Index + migration.** DocChunk 5× `IndexableField` (Schema v3 +
  `_reindex_only` covering None + v2) + SourceDoc `path` (Schema v2→v3). Tests:
  dict-subkey extraction; range (`between`) + exact (`==`/`in_`) DocChunk queries;
  `SourceDoc.path` exact + basename (`.contains`) queries.
- **P2 — Retriever location filter.** A `LocationFilter` value object threaded
  into `Retriever.search(..., location=...)`, pushed into `_dense_order`
  (specstar predicates) and `_load_chunks` (corpus). Tests: scoped dense + sparse
  + merge stays within scope.
- **P3 — Filename → doc resolution.** Helper over indexed `SourceDoc.path`
  (exact + basename), within `collection_ids`; recoverable not-found / ambiguous
  errors. Tests.
- **P4 — `kb_search` params.** `document` / `page_from` / `page_to` / `sheet` +
  docstring + budget unit + required-`document`-when-filtered validation (→
  recoverable error). Tests with `ScriptedAgentRunner`.
- **P5 — Live canned check + docs.** Small-Qwen live check that the agent fills
  the params for a "第 30-90 頁" style query (fake-LLM tests ≠ feature works).
  Document the operator migrate step in the manual.

## Gate

`uv run coverage run -m pytest && uv run coverage combine && uv run coverage
report --fail-under=100` (full local suite). Iterate with targeted tests + ruff +
ty (whole-project) first; full gate once at the end.
