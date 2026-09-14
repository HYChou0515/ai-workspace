# RAG context — neighbouring text, scoped search, reading the original

Design of record. Resolved through `/grill-me`; this file is the canonical spec.
Delivered as four PRs, one per Phase (see *Rollout*).

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
  "what did the model actually see" be reconstructed. `snippet` unchanged.

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
  per-turn budget, symmetric to the kb / wiki budgets, charged even on a
  no-match.
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
- Image delivery reuses `read_image`'s branch verbatim: a vision-capable main
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
span is `base + relative offset`, a verbatim slice of the canonical text (the
splitter's pieces are verbatim with relative offsets — verified), with the
breadcrumb folded in like every Markdown chunk. Applied to a whole section
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

## Out of scope — and findings logged for separate work

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

One PR per phase, in order; each carries its own tests, `migrations.md` entry
and (where a knob is added) example yaml. Phase 1 first — everything else is
built on chunk boundaries. Phase 3's `source-doc` migrate (the `path` index behind the folder
scope) is part of its deploy order, not a follow-up. The frontend sort change and the golden fixture land
with Phase 2 (the backend rule is not "well-defined" for users until the tree
shows it).
