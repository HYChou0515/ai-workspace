# RAG context — neighbouring text, scoped search, reading the original

Design of record. Resolved through `/grill-me`; this file is the canonical spec.
Delivered as one branch, one commit per phase (`P1` … `P11`; see *Rollout*).

The whole thing rests on one slogan: **the chunk is a retrieval artifact, not
the document.** Retrieval finds the spot; context is what makes the spot
readable; and the agent can always go and look at the original. Every decision
below is one of those three things.

## The problem

Three things, plus one found on the way.

**1. The answer sits next to the match, and vector search cannot reach it.**
Embedding retrieval matches the chunk that *phrases* the question. The
supporting detail — the parameter table after "we used method A", the
definition after the term — is written in different vocabulary, so it never
ranks. This is structural, not a tuning problem: the neighbour is *by
construction* the text the query does not resemble. The model reading the
matched chunk sees a coherent fragment and does not know it is missing
anything, so it will not ask for more. Only an unconditional expansion fixes
it.

**2. Named references point an unbounded distance away.** "See Fig. 1" in the
body; Figure 1 on a page after the references. Neither arm finds it: dense
retrieval sees no semantic similarity between "Fig. 1" and a figure caption;
BM25 tokenizes on `\w+` (`kb/bm25.py:19-23`), so the query becomes
`["fig", "1"]` — two terms with zero discrimination in a paper and no phrase
concept — and the trigram pre-narrowing in front of it is fuzzy by design. An
exact-string search is a hole neither arm covers.

**3. File boundaries are often artifacts of how the material was split.**
Slide screenshots, scanned pages, a report split into chapters: file 5 and
file 6 are consecutive pages of the same thing. "Next file in the folder" is
the same relationship as "next paragraph in the file".

**4. (Found on the way, then CORRECTED in review) the legacy chunker does
not chunk Chinese.** `FixedTokenChunker` counts `\S+` runs as tokens
(`kb/chunker.py`); Chinese has no spaces, so a whole paragraph is one token and
a 12,358-char Chinese document came out as **one chunk** — measured on that
chunker. **But production does not use it.** The API and the worker wire
`kb_pipeline=get_doc_pipeline(...)` (the LlamaIndex pipeline; `factories.py`
says the legacy chunker is for tests and offline runs). Measured through the
real pipeline (`build_doc_pipeline` → `Ingestor`), plain text / PDF text layer
goes through `SentenceSplitter(256/32)` (tiktoken-based):

| input (production path) | result |
| --- | --- |
| Chinese prose, 12,358 chars | **80 chunks, avg 154 chars** |
| Chinese, PDF-style, 13,599 chars | 102 chunks, avg 153 chars |
| English prose, 50,038 chars | 40 chunks, avg 1,452 chars |

So in production Chinese chunks are ~10× SMALLER than English ones, not 4×
larger. The first version of this plan measured the wrong entry point and
built Phase 1 and the rerank-cost argument on it; the numbers above replace
those. What the real entry point DID show, unrelated to CJK: the pipeline
routes Markdown — and every VLM description, which is Markdown — through
`MarkdownNodeParser`, which splits on headings only, with no size cap. A
heading-less `.md` of 50,038 English chars is **one chunk**, one vector. That
is a genuine production defect — fixed in Phase 6 (added after the review).

## Decisions that cut across phases

- **No eval gate.** Window width, defaults and the chunker change are set by
  judgment (user decision). Note for the record: the #535 retrieval eval is
  rank-only (`kb/eval/score.py`) and would report byte-identical numbers before
  and after Phase 2 — it measures the half that is not broken. If a
  completeness measure is wanted later it is a "required span is contained"
  check over a hand-labelled set, deterministic, no LLM judge (the same stance
  `skill_eval` takes).
- **RAG only.** Wiki (`search_wiki` / `ask_wiki`) and glossary injection are
  out of scope. The path under discussion is `kb_search` → `Retriever`.
- **Two layers, both needed.** An automatic layer (Phase 2) because the model
  cannot know what it is missing; an agentic layer (Phases 3–4) because
  sometimes it *can* tell, and then it should be able to look — "the way a
  person researches": search, find, read.
- **Units follow the document, not the tool.** Chars for the automatic window
  (a budget, language-neutral once chunks are sane); pages for page-shaped
  documents; lines for text-shaped ones. No synthetic units.
- **Order is one rule, stated to users, shared with the frontend.** See
  Phase 2. People can only organise files for the algorithm if the algorithm's
  order is the order they see.

## Phase 1 — Chinese-aware chunking (legacy chunker only)

Change `_TOKEN` in `kb/chunker.py` so a CJK character counts as one token and
other non-space runs count as one token each (was `\S+`), edited **in place**
in `FixedTokenChunker`, with the character class shared with `kb/tokens.py`
(`CJK_RANGES`) so the "≈ N tokens" estimate and the chunker agree.

Delivered as specified — and, per the correction above, it reaches only the
legacy path: `create_app` callers that do not wire `kb_pipeline` (tests,
offline runs). **Production chunks are untouched and no reindex is needed.**
`migrations.md` says so. Not verified either way: what the embedding endpoint
does with over-long input (our side never truncates).

Retained because the legacy chunker is still a shipped code path and the fix
is correct for it; the plan's original claim that this was the production
defect is withdrawn.

## Phase 2 — Automatic neighbouring context

### What it does

After retrieval has chosen its candidates, each passage is widened to include
**at least N characters before and at least N characters after** the hit,
taken in **whole chunks** (never cut mid-chunk), walking across file
boundaries in tree order. The widened text is what the reranker ranks and what
the agent reads; the citation still points at the hit.

"At least N, in whole chunks" is deliberate: the budget is in a language-neutral
unit, but the granularity is the retrieval unit, so a 1,452-char English chunk
and a 154-char Chinese chunk (the production splitter's sizes) both satisfy
N=2,000 with whole neighbours — about two for English, about thirteen for
Chinese, the same amount of text either way. Chunk count alone would mean different amounts of text per
language; raw chars would cut sentences.

### The knob

- `KbSettings.retrieval.context_chars: int` (final name at implementation),
  next to `quality_weight` / `sparse_corpus_cap` — a retrieval-behaviour knob,
  operator-owned.
- **Per side.** N before *and* N after, not N total.
- **`0` = off.** Not optional (see #767: a knob with no "set 0 to disable" was
  logged as a defect).
- Default **2,000** — on the production path that is ≈1.4 neighbouring
  chunks per side for English (1,452-char chunks) and ≈13 for Chinese
  (154-char chunks); the budget is chars, so both get about the same amount
  of text, which is the point of the unit.
- **Not** in this phase: per-call override by the model (that is the agentic
  layer's business — a tool argument changes the prompt surface and therefore
  model behaviour), per-collection setting (needs UI + storage + migration; add
  when a collection actually needs it, and the config value becomes the
  fallback).
- Bookkeeping: a `migrations.md` config row; the example yaml gains the field.

### Walking across files: the order

Expansion may leave the file and may leave the folder. **The collection is a
hard boundary** (it is a permission boundary). Within a collection the order
is one sentence:

> **The order is the document tree read top to bottom.**

Precisely: at each level, **directories before files**; among siblings,
**natural sort** — maximal digit runs compare by numeric value (`9` < `10`),
everything else by Unicode code point, case-insensitive, with the raw string
as a stable tiebreak. CJK numerals are **not** numeric (`第三章` sorts before
`第二章`) — making them numeric would need a growing list of exceptions and the
rule stops being explainable. **Locale-independent by construction**: no
`localeCompare` / `Intl.Collator(undefined)` / `locale.strcoll` — this repo
has already been bitten by CI's Node locale differing from local, and an order
that depends on the machine is not well-defined.

The user-facing rule is one line, shown on the upload page and the collection
view (not only in docs):

> To control order, prefix filenames with Arabic numerals — `01_intro.pdf`,
> `02_method.pdf`.

**This rule replaces the frontend's.** The file tree
(`web/src/pages/investigation/fileTree.ts:117-123`, shared by the KB doc IDE)
sorts with `new Intl.Collator(undefined, { usage: "sort" })` — no `numeric`, so
`10.png` precedes `9.png` (exactly the case the user rule promises), and
locale-dependent. It must move to the same rule. To keep the two
implementations (Python, TypeScript) from drifting, **one golden fixture** —
a JSON file of `[input names] → expected order` covering digits, leading
zeros, case, CJK, mixed — is read by both test suites. A change on one side
reddens the other.

Finding "the next document" (as implemented): the collection's documents are
listed ONCE per search — `collection_id ==`, `path` projected from row data,
attachments and denied / out-of-scope documents dropped, sorted with the rule —
and only when a walk actually reaches a document edge; a hit in the middle of
a long document never lists anything. Crossing out of the folder needs the
whole collection's order anyway, so a per-folder `starts_with` listing would
not have been simpler, and this way the walk does not depend on the `path`
index at all.

### Permission — expansion is a new arm

Expansion fetches documents that retrieval did *not* return, bypassing the
ranked path where scoping lives. Two things enforce access, and they behave
differently:

- Collection-level and per-document `permission` are enforced at the storage
  layer (`SourceDoc` carries a mirror of its collection's access fields so the
  `source_doc` access_scope hides rows without a join — `kb/doc_permission.py`).
  Querying through the same resource manager with the same actor inherits this.
- `exclude_doc_ids` (#308, the per-speaker override) is **caller-supplied**.
  `retriever.py:617-619` records the exact defect: the code / image / HyDE arms
  once omitted it and "let a denied doc's chunk into the fusion pool".

Expansion **must see the same scope as every other arm, bound in one place** the
way `dense()` binds it — not "remember to pass it". A neighbour the speaker
cannot read is **skipped and the walk continues** until N is satisfied. The
alternative (stop at the first unreadable neighbour) would let one person's
private file silently truncate everyone else's context after it, with no
message saying why. Existence-inference from a gap is an already-considered
area (retrieval's disclosure design) and is not a new problem.

### Where in the pipeline

Today: dense + BM25 → RRF → MMR → `merge_passages` → rerank → `[:top_k]` →
`_augment_with_parents`.

Phase 2: … → `merge_passages` → **expand** → rerank → `[:top_k]` → …

Expansion runs **before rerank, on every merged candidate (~20)**, not on the
final five. The reranker's job is to order what will be delivered; ranking
fragments while delivering expansions ranks a different object — and the
bias has a direction: the passages whose fragment lacks the answer but whose
expansion has it are *exactly* the ones Phase 2 exists to rescue, and a
fragment-level rerank systematically buries them. (`_augment_with_parents`
sits after `top_k` for the opposite reason — it must *not* affect ranking — so
its position is not a precedent here.)

Cost — RESTATED with the corrected production numbers, because the figures
the decision was taken on were wrong: the rerank listing (20 candidates) goes
from ≈29k chars to ≈110k for English (1,452 + 2 × 2,000 per passage, ~4×)
and from ≈3k to ≈84k for Chinese (154 + 2 × 2,000, **~27×**). Nothing caps
the listing before `llm.collect`; a rerank model whose window is smaller
than that truncates from the front — the question is at the front — and the
reply's numbers are then noise that `rerank_passages` applies silently. The
deployment's rerank model is stated to have a 1M-token window; that is taken
on trust (prod config is not visible here, and #767 — the real window behind
the proxy — is still open), and window size does not remove listwise
position bias. This is flagged for the user to re-decide with the real
numbers: keep as is, cap the per-passage context the reranker sees, or skip
rerank (with a logged warning) past a size budget.

After expansion, passages whose context ranges overlap are **left as they
are** (corrected during implementation: the plan first said "merge again").
A merge would have to widen the HIT span to the union — with N=2,000 two
matches up to ~4,000 chars apart would become one "hit" whose citation
snippet and highlight cover thousands of characters that never matched —
which contradicts the invariant this section promises. Duplicated context
costs tokens; a widened citation costs trust.

### Data shape

`RetrievedPassage` has 3 writers (`merge.py:64`, `retriever.py:812`,
`tools.py:733`) and 4 readers of `.text` (`rerank.py:33`, `citations.py:50`,
`tools.py:1036`, `tools.py:1037`). It is **not** a specstar model — a
transient value object — so adding a field costs nothing durable.

- `text` / `start` / `end` **stay the hit span.** Unchanged for every existing
  reader; in particular `citations.py:50` keeps `snippet = p.text`, so the
  frontend reference card still shows the matched sentence, not two thousand
  characters.
- New `context_text: str = ""` — the widened text; `""` means "not expanded".
  Read by exactly two places: the rerank listing and the `kb_search` tool
  output line (`context_text or text`). The two writers that are not the
  expansion path (`_augment_with_parents`, `read_source`) leave the default
  and are correct by construction.
- `Citation` (persisted, embedded in `Message`) gains `context_start` /
  `context_end: int` — **offsets, not text**. Near-zero storage; the same kind
  of thing as the existing `start` / `end` (and the same staleness if the
  document is re-indexed or edited — not a new weakness); lets #748-style
  "what did the model actually see" be reconstructed for the same-document
  part of the context (the cross-document spill has no offsets — it is
  recomputable from the tree order, not stored). `snippet` unchanged.

Context is for the model, not the user: someone who opens the document sees
the surroundings anyway, and highlighting two thousand characters is the same
as highlighting nothing.

### No deployment dependency (corrected during implementation)

The walk lists the collection (`collection_id ==`, indexed since the model
existed) and reads each row's `path` from its DATA via a projection, then sorts
in Python — it never issues a `path` predicate, so it does not depend on the
`path` index and needs no migrate. The plan first assumed a `starts_with`
lookup here; that dependency is real for **Phase 3's folder scope** (which does
resolve `path.starts_with(folder)`), and the `SourceDoc` v10 identity step +
§5 ledger row ship for that.

## Phase 3 — Scoped search: semantic and exact

### The scope parameter

Both search tools take the same scope: **a document** (filename, as today) **or
a folder** (a path prefix). Folder matching is on `prefix + "/"`, so `a/b` does
not match `a/bc`. Resolution is an indexed `SourceDoc.path.starts_with` →
document ids → `restrict_to_doc_ids` (#518), which is already pushed into both
the dense and the sparse arm. Three levels compose: collection → folder →
document.

This also settles "files in the same folder are context" without an automatic
injection: the folder is a scope the model can *choose* (it sees the hit's
path), so the blast radius is the model's decision, not 200 files by default.

### Exact keyword search — a separate tool

Semantic search is asking a librarian; exact search is Ctrl+F. They return
different shapes (a few ranked passages vs. every location), so they are two
tools, not one flag. Modelled on `search_wiki`, which is already the house
Ctrl+F:

- Returns `filename (p.N):line: matching line` — page shown when the document
  has pages, line always — in **document (tree) order, not ranked**. "Should
  keywords participate in ranking" dissolves: this does not rank.
- Output capped at `exec_output_max_chars` with middle truncation; its own
  per-turn budget type (`KbGrepBudget`), threaded through the same doors as
  the kb / wiki budgets and charged even on a no-match — but with no operator
  knob and no per-message picker in this plan (only `AskKbSpec.kb_grep_max`);
  off with the documents.
- Hits print the document's `path` (as the file tree shows it), not the bare
  filename — the read tools take a path, and two files can share a basename.
- **Not citable.** It locates; citing means reading (Phase 4). Same as
  `search_wiki`, same as a person.
- Query semantics follow `api/search.compile_query`.
- Output names documents in the same dialect the Phase 4 readers accept, so a
  hit can be handed straight to `read_page` / `read_lines`.

**Do not copy `search_wiki`'s loop.** It reads every page in scope and greps
in Python — fine for a wiki, a full scan for a collection of thousands of
documents. The store is pre-narrowed through the pg_trgm index on
`DocChunk.text`, then the exact match is verified on the canonical text to
produce line and page.

Two things settled while implementing (`Retriever.grep`, `kb/grep.py`):

- **Pre-narrow with `icontains`, never `.fuzzy`, and on ONE token.** `.fuzzy`
  is trigram *similarity* — a short query inside a long chunk scores low and
  the chunk is dropped, which is wrong for an exact search (BM25 accepts that
  loss; grep cannot). `icontains` is an exact, case-insensitive substring
  (`ILIKE`, index-accelerated). And it is applied to the query's **longest
  token**, not the whole phrase: a phrase longer than the chunker's overlap can
  straddle two chunks, so a whole-phrase pre-filter never sees it (a test pins
  this with a three-token phrase over three-token windows). Any occurrence's
  longest token lies whole inside some chunk; the exact phrase is then verified
  on the canonical text with the chunk span widened by the query's length.
- **The exact search is a DOCUMENT tool.** #537's allowance is per *source*
  (documents / wiki / glossary), so "documents off" (`kb_search_max == 0`)
  withholds `kb_grep` too — a user who set "0 document searches" must not find
  the agent still grepping the documents. It has its own switch as well
  (`kb_grep_max == 0`) and its own counter (`KbGrepBudget`, unlimited by
  default — deterministic, no LLM, `max_turns` is the structural bound), and it
  never draws from the semantic budget. Threaded through every door the other
  two budgets go through (KB turn, `answer_question`, the sub-agent bridge,
  `chat_send`, `AskKbSpec`).

The `folder` scope composes with the #518 card anchor by intersection, and the
"widen" pass widens to the folder, never past it: an anchor with nothing inside
the folder yields nothing so the caller widens — passing an empty
`restrict_to_doc_ids` would have meant *unscoped*, a leak outside the folder.

Solves the `Fig. 1` case (and part numbers, error codes, section numbers)
without a figure-label parser — that idea is withdrawn.

## Phase 4 — Reading the original

"The original" means the page as it actually looks, not the parsed text: a
figure's caption is second-hand; the figure is first-hand. Pages are the unit
for page-shaped documents; lines for text-shaped ones. **Two tools, two
units** — a unit is offered where it exists and refused where it does not.

| tool | unit | returns | applies to |
| --- | --- | --- | --- |
| `read_page(document, page)` | page | the page **as an image + its text layer** | PDF, PPTX, image files |
| `read_lines(document, offset, limit)` | line | text, `read_file` dialect | anything with canonical text (PDF included, via its text layer) |

- Calling the inapplicable one returns a plain "this document has no pages /
  no lines". No synthetic pages for Markdown, no synthetic lines for a
  screenshot.
- Both coordinates appear in the grep output, so the model knows which to call.
- Image delivery copies `read_image`'s branch (the same gate and shapes, not
  shared code): a vision-capable main
  model receives a `ToolOutputImage` and sees the pixels; a text-only main
  model goes through the `kb.vlm_llm` describer; neither configured → the same
  "not available, do not retry" error.
- Getting the page image: PDF via `render_page_png` (`kb/parsers/pdf.py`,
  the ingest-time rasteriser made public); image files are the image (page
  1); a slide deck's LibreOffice-converted PDF turned out to be **already
  persisted** — `PptxParser` hands it back through `on_preview` and the
  Ingestor stores it on `SourceDoc.preview` for the browser viewer — so
  `read_page` rasterises from that blob and no new persistence was needed
  (the plan's "persist it" item was already true).
- Text layer for a page comes from the chunks whose `provenance.page` matches
  (indexed), sliced from the canonical text (`kb/pages.py`).
- **Reads are citable.** "What you read is what you cite from" has to be
  true in code, not only in the prompt: both tools register what they showed
  as a passage in the turn's registry — the same `(document, span)` dedup
  `kb_search` uses — and prefix the output with its `[n]`. `read_lines`
  registers the window's char span; `read_page` registers the page's
  text-layer span with `{"page": N}` provenance, so the reference card can
  say "p.N". `kb_grep` stays locate-only.
- Both are document tools under #537's per-source switch: withheld with the
  documents (`kb_search_max == 0`), granted with them otherwise, no budget of
  their own (same as `read_image`); `max_turns` and the output caps bound them.
- A vision main model receives `[ToolOutputText, ToolOutputImage]` (the SDK
  accepts a list of parts); a text-only main model receives the text layer
  plus the `kb.vlm_llm` description of the page image.

## Phase 5 — review round 1

Fifteen findings on P1–P4; thirteen fixed here, two became the Phase 6
re-decisions. The ones that changed behaviour on paths that existed before
this plan:

- `read_page`'s image never reached the model: the multi-part
  `[ToolOutputText, ToolOutputImage]` output was `str()`-ed by the output cap
  and by the litellm runner's tool-output rendering. Both doors now measure
  and render the text parts and pass the image through.
- The context walk leaked scope: a #518 card restriction and a #263 location
  filter confined the HIT but not the neighbours it pulled in. The seams now
  share one `_within` rule with `dense()`.
- `resolve_document` applies the #308 exclusions before deciding "exact /
  ambiguous / missing", so a denied document answers like a missing one
  (`kb_search(document=)` included).
- `_DocJoin` prefers the in-scope holder of shared content when attributing a
  chunk (any #518 restriction), so a restricted search never names a document
  outside the restriction.
- `_restriction` batches its lookups; the numbers in this plan and the docs
  were corrected to the production splitter's (P1's premise was measured on
  the legacy chunker).

## Phase 6 — the two re-decisions after review round 1

Added after the review corrected the production numbers (user: "Ok" to both).

**The reranker's context is capped.** `kb.retrieval.rerank_context_chars`
(default 4,000; `null` = uncapped, `0` = the bare hit) bounds what each
candidate contributes to the one listwise prompt, centred on the hit so the
matched text is always inside the window. Without it `context_chars` alone
grew the prompt ~4× (English) / ~27× (Chinese), and a reranker whose window
is smaller truncates from the front — the question — and returns noise that
`rerank_passages` applied silently. The reranker still ranks what will be
delivered; the prompt is now bounded. Threaded through the same three doors
as `context_chars`.

**Long Markdown sections are windowed.** The production defect the real
entry point showed: `MarkdownNodeParser` splits on headings only, with no
size cap, so a heading-less `.md` — or a VLM description — of any length was
one chunk and one vector. `DispatchSplitter._prose_nodes` now runs the
sentence splitter over a prose region larger than its window; each piece's
span is `base + where the piece sits in the region`, with the breadcrumb
folded in like every Markdown chunk. (The first version trusted the
splitter's own offsets as "verbatim, relative — verified"; review round 3
showed they are first occurrences, not positions, and that the splitter's
phrase fallback rewrites ~5% of pieces — Phase 8 replaced that mechanism.) Applied to a whole section
AND to the prose regions between tables (one rule). A region that fits
returns the section parser's own node, byte-identical to before, so the
common case and the #390 cache keys are untouched. Measured through the
pipeline: the 50,038-char heading-less `.md` went from 1 chunk to 35
(avg 1,630 chars); Chinese from 1 to 80 (avg 154). This DOES change
production chunks: documents holding long Markdown sections need a
collection re-read — `migrations.md` says which and how.

## Phase 7 — review round 2

Five findings on P1–P6, all in this branch's own code; the first was round
1's fix. Each is pinned by a test that was red against the unfixed code.

- **The read registry's page dedup never matched.** `_register_read` keyed
  the new read on `page_of(provenance)` (an int off a chunk-shaped dict) but
  the registry stores the AGGREGATED form (`{"page": [N]}`), which `page_of`
  reads as `None` — so every re-read of a page minted a new `[n]`, and the
  test that "kept pages apart" passed for the wrong reason. Both sides now
  go through `pages_of`, the accessor for the stored form.
- **A `read_lines` that read nothing minted a citation.** An offset past the
  end (or a limit below 1) registered an empty span past EOF as citable. An
  empty line range now answers with the document's line count, no marker —
  the same shape as `read_page`'s range error.
- **`kb.retrieval.context_chars: null` loaded and crashed the worker.** The
  API door mapped `None` to the default; the worker forwarded it verbatim and
  the retriever's first search raised (`None <= 0`). The rule lives where the
  value is made: the loader refuses anything but a non-negative integer
  (`0` is the off switch; there is no "uncapped" for this knob) and checks
  `rerank_context_chars` for sign and type (`null` stays legal there).
- **`kb_grep` required a stray space literally.** The anchor ignored
  surrounding whitespace (`split()`), the exact pattern did not — a query
  with a trailing space passed the pre-filter and then matched nothing.
  One stripped query feeds both.
- **A grep hit's page depended on store order.** With two chunks over the
  same line, "the first containing chunk's page" was whichever row the store
  returned first (unspecified). Chunks are walked by `start`, so the
  earliest one wins deterministically.

## Phase 8 — offsets are positions

Review round 3 (four lenses in parallel) found, independently in three of
them, that the invariant every consumer on this branch rests on — **a chunk's
`start`/`end` is where its text sits in `SourceDoc.text`** — was never
established by the pipeline path. Two defects, one class:

- **Per-Document, not per-document.** LlamaIndex stamps a split with an offset
  into the Document it came from, and a page-shaped parser emits one Document
  per page (slide, CSV row, JSONL line), joined with `"\n\n"` into the
  canonical text. Page 2's chunks pointed into page 1: `read_page(2)` showed
  page 1's text layer, `kb_grep` was blind past the first page, the context
  walk widened into the wrong text, `merge_passages` folded three pages'
  identical spans into one. Pre-existing on master — nothing on master sliced
  the text at a chunk's offset — and invisible to every fixture on this branch
  (blank pages, one fake VLM string for every page).
- **First occurrence, not position.** LlamaIndex locates each split with
  `text.find(piece)`. A document that repeats a paragraph put 36 of 38 chunks
  inside its first 1.8k chars; the walk went 18k chars back and 0 forward.

The fix is at the two places the offsets are made:

- `DispatchSplitter` (as first shipped in P8, corrected in P12 and REPLACED
  in Phase 14) located every piece by searching the text; since Phase 14 the
  sentence splitter itself reports where it cut (`OffsetSentenceSplitter`),
  Markdown sections and code chunks — disjoint and in order — sit at their
  first occurrence after the previous one's end, and every node points at
  its Document (the `SOURCE` relationship — the nodes this class builds
  itself never had one). Its transformation cache is off:
  on a hit it returned nodes bound to an earlier run's Documents (and, the
  regression lens measured, never evicted: +223 MB over 20 re-runs of 300
  rows; off, +1.7 MB — and each first run is faster).
- `Ingestor._build_chunks` adds each Document's base in the join (minus the
  join's stripped lead) and clamps to the text. The #227 fan-out is the one
  path that cannot know its base while chunking — a batch sees only its own
  units — so it records the batch-relative start on the chunk
  (`DocChunk.unit_start`) and finalize, which rejoins the batches, recomputes
  `start = unit_start + base` before publishing the text and before the #390
  cache snapshots the chunks. Recomputed from an immutable value, so a
  re-driven finalize lands on the same numbers: idempotent by construction,
  no crash window to reason about.

Pinned by `tests/kb/test_chunk_offsets.py` on every path (single job, dry-run,
fan-out finalize, the cache snapshot) with a real multi-page text-layer PDF,
a multi-row CSV and a repetitive `.txt`, plus the three consumers on page 2.
**This changes the stored offsets of every multi-Document and repetitive
document: they are wrong until re-indexed** — `migrations.md` says so.

## Phase 9 — a window carries its section's metadata

The regression lens of round 3, through the real entry point (`PdfParser` +
a VLM fake + `build_doc_pipeline` + `Ingestor`): P6's windows were built as
bare `TextNode`s with empty metadata, and `DocChunk.provenance` is collected
from node metadata — so a dense page (every real VLM description is longer
than the sentence window) lost its `page` / `section` on the way out.
`read_page(2)` found no text layer and registered an empty span as citable,
`kb_grep` printed no `(p.2)`, the reference card lost its page, and the #254
section fold (which reads `metadata["section"]`) never ran. The base already
lost the metadata on the narrower table path; P6 widened it to every long
section. Every node built FROM a section — a window, a small table, a row,
the prose beside a table — now carries the section's metadata (one place,
`_split_markdown`). Pinned through the real entry point: a three-page PDF
whose VLM description windows, every chunk with its page and section, and
`read_page` / `kb_grep` on page 2. Covered by the same collection re-read as
P8.

## Phase 10 — every guard has a test that reddens

Round 3's veracity lens deleted each enforcement on a snapshot: eleven
mechanisms stayed green without their guard. Each now has a test run against
the mutant (folder ∩ card-anchor = ∅, expansion before rerank read off the
reranker's prompt, the read-time describer's own words, the create_app and
worker doors, the walk's per-side bound as a failure rather than a hang, the
prose beside a table, `kb_grep`'s cap / stopped-early head / too-short
message, `read_page`'s pre-bounded text part). One behaviour fix in the same
class: `read_lines` refuses a limit below 1 before the slice.

## Phase 11 — what the plan promised and the prose claimed

- The ordering rule is shown where files are added (the empty-collection
  CTA) and where they are seen (under the tree), in both languages — the
  Phase 2 promise that was silently dropped.
- The workspace breadcrumb dropdown (`dirChildren`) sorts with `treeOrder`
  too; the two lists in one UI agreed under locale order and disagreed after
  Phase 2 changed only the tree.
- The allowance block names `read_lines` / `read_page` as off when the
  documents are off (the prompt still describes them).
- The example yaml's kb presets list the three new tools; `configuration.md`
  and `migrations.md` say a custom kb preset that pins `allowed_tools` must
  add them.
- Claims trimmed to what holds: `context_chars: 0` is "off", not
  "byte-identical" (the P5 holder-naming fix applies regardless); the
  reranker cap keeps the hit in the window when it fits; the grep budget has
  no knob or picker; `Citation.context_*` reconstructs the same-document
  part; `read_page` copies `read_image`'s branch; one branch, not four PRs.

## Phase 12 — review round 4 on the offsets

Three lenses on P8–P11 (regression: 283 documents / 15,941 chunks, nothing
right on P7 wrong on HEAD, correct spans 4,367 → 14,936, non-monotonic
111 → 0, out-of-bounds 6 → 0; the three ingest paths agree on every input).
What they found in the new mechanism, fixed here:

- **A short chunk absorbed as overlap.** The sentence splitter closes a
  sentence shorter than the overlap as its own chunk and then carries it
  WHOLE into the next chunk, so two consecutive pieces share a start. P8's
  walk ("the next piece starts after the previous start") could not find
  the long piece from start+1, anchored on its head — and when that short
  sentence recurs later, pinned a 1.2k-char chunk to the recurrence;
  `kb_grep` was blind to it. P7 had it right. The walk now accepts an equal
  start.
- **Periodic text still crowded.** On the very document the "36 of 38"
  number came from, HEAD still walked 11,940 chars back: searching from the
  previous START finds the next occurrence one period on, not the cut one
  chunk on. Consecutive pieces are contiguous, so the next one starts at or
  after the previous END minus the overlap — the search starts there, with
  the overlap in the splitter's own unit (tokens × a char bound for
  sentences, exactly the last N lines for code, 0 for Markdown sections).
  That document now tiles at the splitter's cuts (chunk 17 at 17,462; the
  walk goes ~2k back). What remains is the limit of locating by text: a
  period shorter than the overlap bound can drift by one period.
- **A tail anchor with no minimum** under-covered 1 of 15,941 spans (a 3-char
  tail found early). The end is now the smallest region from the head that
  contains the piece as a subsequence — exact, and never shorter than the
  piece.
- **Cost.** Every search is bounded to the neighbourhood the next piece can
  be in (a failed unbounded `find` scans to the end: 3 MB of rewritten pieces
  took 50 s), and `as_related_node_info()` — which re-hashes the whole
  Document — runs once per Document, not once per piece (+44% on 3.5 MB).
- **A process job replayed after finalize.** #227's at-least-once delivery
  can replay a batch after the run finished. Before P8 that was an identical
  overwrite; with batch-relative offsets it rewrote the batch's chunks and
  staged a stale row, with no finalize left to rebase them. A batch whose run
  is not `running` is stale and writes nothing.
- The worker builds ONE Retriever for the card drafter and the eval handler
  (two hand-copied kwargs blocks; the door test pinned one). The boot hint
  names the tools the kb prompt describes. `read_file` (workspace) gets the
  same "limit below 1" rule as `read_lines`. The `_split_markdown` /
  `_split_code` relocations and the `_ANCHOR_MIN` floor have tests.

Known cost left as is: finalize patches every moved fan-out chunk one row at
a time (a 5,000-row CSV: 4,500 patches, ~11 s in memory) — correct and
idempotent; a bulk shape needs `patch_many` with per-row values, which
specstar does not have. Logged below with the pre-existing findings.

## Phase 14 — the splitter reports where it cut

Round 5 (two lenses on P12) settled the question the rounds had been
circling: **a chunk's position cannot be recovered by searching the text.**
The search floor must approximate an overlap the splitter computes as a
token sum over whole splits, and every char bound was wrong somewhere —
inert on CJK (256 tokens ≈ 155 chars, so the P12 rule degenerated to "next
occurrence" and the P7 defect stood at larger magnitude: 66 of 67 chunks of a
32-char Chinese sentence × 400 inside the first 2.2k of 12.8k chars), a
period too loose on English one word longer than the test's sentence (the
P12 test passed on 16 tokens × 2 = 32), compounding per chunk either way;
the code "overlap" bounded an overlap this `CodeSplitter` never has
(`chunk_lines_overlap` is declared and never read); the cover limit for
rewritten pieces collapsed every chunk of a dot-leader table of contents to
its 45-char head and blinded `kb_grep` on it (P11 was right there); after
~8 KB of drift the one unique line — the one a query hits — was anchored onto
boilerplate. Each fix had failed one step further out: the mechanism, not
the parameters, was wrong.

`OffsetSentenceSplitter` subclasses LlamaIndex's `SentenceSplitter` and
carries the char offset of every split through its own `_split` (each split
located in ITS parent — exact, since the split functions return the text's
pieces in order); `_merge` stays LlamaIndex's, and each chunk's span is
reconstructed from the run of consecutive splits it was merged from, with
the overlap rule `_merge` applies (the maximal tail whose token sizes fit in
`chunk_overlap`). Exact for verbatim chunks; for the ~5% the phrase fallback
rewrote, the run's extent — which covers the dropped punctuation. The stock
`_postprocess_parsed_nodes` (where LlamaIndex stamps the first-occurrence
offsets, after `_parse_nodes`) is overridden to put the spans back. If an
upgrade changes how chunks are formed, the reconstruction raises and the
ingest marks the document `error` — loud, not plausible-looking offsets.
`_locate`, `_relocate`, `_cover_end`, `_longest`, `_ANCHOR_MIN`,
`_NEAR_SLACK` and the overlap bounds are gone; Markdown sections and code
chunks use `_place_after` (first occurrence after the previous end — exact
for disjoint, ordered chunks). Pinned by `tests/kb/test_offset_splitter.py`
(thirteen text shapes: same chunks as the stock splitter, exact or covering
spans, tiling, metadata-aware chunk size, the loud failure) and the round-5
shapes through `Ingestor` — periodic text of any period or language tiles
the document, the unique line after 800 repeated lines is where it is, the
table of contents is fully spanned and `kb_grep` finds its entries, repeated
code sits at the splitter's cuts. 3.5 MB ingests in 11.4 s (P12: 11.2 s),
8,006 of 8,006 chunks verbatim at their span.

## Phase 15 — a batch replayed during finalize rebases itself

Round 5: the P12 guard on `_handle_process` is check-then-act. A duplicate
delivery (#227's at-least-once broker) that passed the guard while the run
was still running, and whose embedding outlived the other batches AND the
finalize, wrote its chunks batch-relative after the rebase and staged a
text row after staging was cleared — with the run `done`, nothing would
ever rebase them, and the stale row would be rejoined into the NEXT run's
text. Finalize now publishes every batch's base on the `IndexRun`
(`batch_bases`) BEFORE it rebases a single chunk; a batch whose write lands
after that point reads its base and rebases its own chunks, staging
nothing; and the split step clears staging so nothing an earlier run left
can reach a later one. Driven through the real handlers with `index_units`
interposed so finalize runs inside the duplicate's window — red on P14.

## Phase 16 — review round 6

Two lenses on P14/P15. **The span machinery came back clean**: the
regression lens found P15's chunks identical to P13's in text and count over
283 documents, every one of 16,323 sentence-split chunks at exactly the span
an independent oracle computes, the three ingest paths agreeing, cost flat,
and 352 concurrent calls on one shared splitter with 0 disagreements (the
positive control — a shared namespace instead of the thread-local — fails);
the defect lens fuzzed ~1,000 adversarial texts (CRLF, tabs, NBSP,
zero-width, combining marks, emoji, CJK, 300-char words, chunk sizes 8–256,
overlaps 0–32) against three oracles and found nothing. What it found was in
P15's edges, fixed here:

- The #390 cache could still snapshot a replayed batch's rows before that
  batch rebased itself (a few round-trips wide), and `copy_from_cache`
  restores spans verbatim — persistent, silent. A late replay whose run has
  already finished now re-snapshots after rebasing.
- The P15 re-read before staging was itself check-then-act: finalize could
  consume staging between it and the stage write, leaving a row past the
  finished run. The batch looks again after staging and removes its own row.
- "Bases go on the run FIRST" — the whole mechanism of P15 — had no test
  that reddened when the two lines were swapped. It has one now (two
  threads, the duplicate's write after the rebase and its re-read before the
  publish), and so does the cache window.
- `OffsetSentenceSplitter`'s per-call state was a plain `__dict__` entry on a
  pydantic model: `to_json()` raised, and the base component's
  `__getstate__` would strip it from the LIVE instance on copy / pickle. It
  is a `PrivateAttr` now.

## Out of scope — and findings logged for separate work

Found by the review rounds, pre-existing on master, not touched here:

- **HTML / DOCX chunking is non-deterministic**: `HTMLTagReader` /
  `DocxReader` stamp a random temp `file_path` into Document metadata, and
  `SentenceSplitter` subtracts the metadata's token count from its budget,
  so chunk boundaries move with the temp name (3 runs, 2 different chunk
  sets). Undermines the #390 cache key and "the three paths agree" for those
  types.
- A fan-out's `SourceDoc.text` can differ from the single-job text by a
  trailing space at a batch boundary (the batch `.strip()`); each path's
  offsets index its own text, so no consumer sees it today.
- A `.md` that starts with blank lines before its first heading persists an
  empty-text chunk (`start == end == 0`, embedded) from the parser's empty
  pre-heading section.
- `CsvParser` never sets the `row` provenance key `_PROVENANCE_KEYS` lists.
- The fan-out has no run epoch: a batch from run N that completes after run
  N+1's split passes both guards and stages its text into run N+1's slot —
  benign for the same content, a mixed `SourceDoc.text` if the content was
  edited between the runs. Pre-existing (#227); P15/P16 narrow it, do not
  close it.
- Finalize's per-chunk patch cost (above).

- Eval-gated tuning; per-call / per-collection context knob.
- Wiki / glossary interactions.
- Figure-label parsing (withdrawn; Phase 3 covers it).
- Long text documents beyond `read_lines` (no page semantics for them).
- **Rerank.** Two findings, separate issue: (1) `kb/rerank.py` parses the
  model's reply with `\d+`, so a reply like "Top 3: [2], [1], [5]" puts
  passage 3 first — any in-range integer in prose corrupts the order;
  (2) the file's own header says to replace the LLM listwise rerank with a
  cross-encoder when one is available. Likely a bigger win for "RAG must be
  correct first" than context is.
- Embedder behaviour on over-long input (see Phase 1).
- Context-walk cost on one-page-per-file collections: every hit sits at a
  document edge, so every search lists the collection once (partial fields,
  sorted in Python) and re-reads metadata `_DocJoin` already had. Measure
  before optimising (a per-collection order cache with an invalidation key
  is the obvious shape).
- The real context window behind the proxy (#767).

## Rollout

One commit per phase, in order, on one branch; each carries its own tests,
`migrations.md` entry and (where a knob is added) example yaml. Phase 1 first — everything else is
built on chunk boundaries. Phase 3's `source-doc` migrate (the `path` index behind the folder
scope) is part of its deploy order, not a follow-up. The frontend sort change and the golden fixture land
with Phase 2 (the backend rule is not "well-defined" for users until the tree
shows it).
