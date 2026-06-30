# Plan — #328 follow-up: "Tune parsing" (per-doc prompt escape hatch + answer preview)

Tracking issue: **#356** (https://github.com/HYChou0515/ai-workspace/issues/356).

Grill-locked scope for the follow-up to #328. The trigger was "描述與現實不符":
the Findability-probe modal's copy describes a collection-level prompt editor, but
the only entry is a per-document button named "Findability", and a per-doc escape
hatch field exists in the data model with **no UI** (and a comment that lies about
the modal writing it).

This work turns that modal into a coherent **per-document parsing-tuning** surface:
edit the parse prompt (per-doc OR collection), preview the re-parse, AND see the
actual answer a question would get — all coupled so you never tune blind.

## Locked decisions (grill)

- **D1 — Scope: free-text only.** Only the free-text `Collection.parser_guidance`
  gets a per-doc escape hatch. The structured `parser_configs` /
  `SourceDoc.parser_config_overrides` path stays **dormant** — confirmed *no*
  concrete parser overrides `IParser.config_fields()` today (`grep "def
  config_fields"` hits only the `protocol.py` base returning `[]`), so
  `effective_config` resolves `{}` and `_parse_config_for` returns `None` for
  every parser. Wiring UI for zero knobs is pointless. We only **fix the
  misleading comment** on `SourceDoc.parser_config_overrides`
  (`resources/kb.py:280` claims "the findability-probe modal writes" it — it never
  did).

- **D2 — New field `SourceDoc.parser_guidance_override: str = ""`.** Semantics =
  **REPLACE** (presence-based): non-empty ⇒ it *replaces* the collection's
  `parser_guidance` for this doc at index time; empty (default) ⇒ the doc inherits
  the collection guidance (today's behaviour for every doc). Non-indexed → no
  migration. **Carried forward across re-index** (mirror `parser_config_overrides`
  at `ingest.py:478`: `parser_guidance_override=(existing... if existing else "")`).

- **D3 — Merge point.** `Ingestor._parse_guidance_for(collection_id)` →
  `_parse_guidance_for(collection_id, doc_id)`: return `doc.parser_guidance_override`
  when non-empty, else `coll.parser_guidance`. `dry_run_chunks(doc_id, guidance=...)`
  already takes an explicit candidate, so the modal preview is unaffected; only the
  REAL index path changes.

- **D4 — Apply UX: three explicit buttons (no scope toggle).**
  - `Save for this document` *(primary — you opened on a doc)* → PATCH
    `SourceDoc.parser_guidance_override = <textarea>`.
  - `Apply to whole collection` *(secondary — confirm dialog, blast radius)* →
    PATCH `Collection.parser_guidance = <textarea>`.
  - `Clear document override` *(only shown when the doc has a non-empty override)* →
    PATCH override `""` so the doc re-inherits the collection guidance.
  - Textarea prefill = the doc's **effective** guidance (override if set, else
    collection). A status line states the source: *"This document: custom override"*
    vs *"This document: inherited from collection"*.

- **D5 — Apply persists only; no auto re-index** (consistent with today's "Apply
  to collection"). After any apply/clear, show *"Saved — not in effect yet;
  re-index to apply"* with a convenience **`Re-index this document`** button
  (explicit click; reuses the existing `POST /kb/documents/reindex`). The
  whole-collection re-index stays the deliberate, separate "Re-index all".

- **D6 — No AI draft, no static templates** this round (separable; future issue).

- **D7 — Answer preview ("doc ∩ top-k").**
  - **Context = the doc's passages whose GLOBAL rank ≤ k.** e.g. doc passages rank
    1, 4, 6, 12 and k=5 ⇒ only ranks 1 & 4 enter the context window; the agent
    answers from just those two.
  - **k** = a single **shared** slider, range **1–100**, **exponential** mapping
    `k = round(100 ** p)` for slider position `p ∈ [0,1]` (so midpoint p=0.5 ⇒
    k≈10, ends 1 and 100). k drives **both** the rank-list highlight (`in_top_k`)
    **and** the answer context. Consequence: the global ranking depth must cover k —
    rank to `depth = max(DEFAULT_DEPTH, k)` (≤ the 200 clamp).
  - **Agent = the kb_chat agent, reused wholesale** (same `kb/prompts/system.md`
    prompt + same resolved model / `AgentConfig`), but it does **NOT** self-search:
    we inject the fixed "doc ∩ top-k" passages as the numbered `[n]` retrieval set
    and disable `kb_search`. Streaming (per house rule: every LLM call streams).
    General-knowledge answers stay allowed (the prompt labels them "(General
    knowledge…)"); an **empty intersection** is handled by the prompt itself
    (internal Q ⇒ "the knowledge base doesn't appear to cover it"), no special case.
  - **Per-column answer**: each rank box owns its own answer (Before answer from
    the currently-indexed chunks ∩ top-k; After answer from the re-parsed overlay
    chunks ∩ top-k).
  - **Trigger**: an explicit **`Try answer`** button per box (NOT auto on "Check
    ranks") — so we never fire an LLM call the user didn't ask for.

- **D8 — Layout: stacked, not side-by-side.** Two vertical boxes; **Before
  collapsed by default**, After expanded. Shared k slider on top. Wider = easier
  to read a streamed answer.

- **D9 — Entry point + naming.** Rename the per-doc button "Findability" →
  **`Tune parsing`** and **move it from the bottom status bar
  (`KbDocIde.tsx` `KbStatusBar`, the hardcoded `Findability` button) to the
  document header next to the edit toggle** (in/around `KbEditorPane`). **No
  collection-level entry** — tuning is always something a user does on a specific
  document they care about.

- **D10 — i18n.** All new/changed user-facing strings go through `web/src/lib/i18n`
  keys (en + zh-TW). The current `Findability` button is hardcoded — i18n it as
  part of the move. Rewrite the modal's top description to match the new reality
  (per-doc/collection prompt + answer + k) — this closes the original "描述與現實
  不符".

- **D11 — TDD + gates.** Red→green→refactor (backend pytest, FE vitest). Iterate
  on changed-behaviour tests; run the **full suite + 100% coverage gate** +
  ruff/format + whole-project `ty` at the end. Commit locally only (never push).

## Backend design notes

- **`answer_question` is agentic** (`kb_chat_routes.py:59` — runs the kb_search
  loop), so it can't be reused as-is for fixed-context answering. Add a focused
  **`answer_from_passages(...)`** path that reuses the kb_chat **prompt + model**
  but presents the injected passages as the numbered results and runs a single
  **streaming** completion with `kb_search` disabled. The probe route today has
  `spec/retriever/ingestor` but **no runner/AgentConfig** — thread the kb_chat
  runner + resolved config in (same source `answer_question`'s callers use).
- **New streaming endpoint** `POST /kb/findability/answer` (SSE) — body:
  `{ doc_id, question, k, guidance? }`. `guidance is None` ⇒ Before (current
  indexed chunks); a string ⇒ After (re-parse via `dry_run_chunks`, overlay).
  Rank globally to `max(DEFAULT_DEPTH, k)`, take the doc's passages with rank ≤ k,
  pull their **full** chunk text (NOT the 600-char `_SNIPPET` preview), stream the
  answer using the same SSE event schema as the chats.
- `findability.py` / `ProbeResult`: make `top_k` adjustable per request (k), feed
  through `depth = max(DEFAULT_DEPTH, k)`.

## Phases (flat integer; TDD each)

- **Phase 1** — Backend per-doc guidance escape hatch: add
  `SourceDoc.parser_guidance_override`; `_parse_guidance_for(collection_id, doc_id)`
  REPLACE-merge; carry-forward on re-create; fix the lying
  `parser_config_overrides` comment. Unit tests: override replaces / empty inherits
  / survives re-index.
- **Phase 2** — Backend answer endpoint: `answer_from_passages` (reuse kb_chat
  prompt+model, no self-search, streaming) + `POST /kb/findability/answer` (SSE,
  before/after via guidance, doc ∩ top-k with full text, depth follows k). Tests
  with a scripted runner: correct passage set per k; empty-intersection path;
  streaming events.
- **Phase 3** — Probe `k` knob: `probe_findability` / route accept k, rank to
  `max(DEFAULT_DEPTH, k)`, `in_top_k` uses k. Tests for the rank/k coupling.
- **Phase 4** — FE modal rebuild: rename to **Tune parsing**; three apply buttons
  (D4) + collection confirm + clear-override + "Saved, re-index" nudge + per-doc
  reindex button (D5); stacked layout, Before collapsed (D8); shared exponential k
  slider (D7); per-box `Try answer` streaming via SSE (reuse `AgentEntryView`);
  rewrite the description (D10). vitest for each affordance.
- **Phase 5** — FE entry move + i18n: move/rename the trigger from `KbStatusBar`
  into the document header next to edit (D9); route all new + the old hardcoded
  `Findability` strings through i18n (en + zh-TW) (D10). vitest.
- **Phase 6** — Gates: full suite + 100% coverage + `coverage combine`; `ruff
  check` + `ruff format --check`; whole-project `ty check`; FE `pnpm typecheck` +
  `vitest` + `build`. Live canned check of the answer path against local Ollama
  (kb_chat model). Commit locally.

## Out of scope (explicit)

- Structured `parser_configs` / `parser_config_overrides` editor UI (dormant; no
  parser declares knobs — separate future issue).
- AI-drafted / templated prompts (D6 — future issue).
- Before-vs-after **answer** auto-comparison beyond the per-box `Try answer`
  buttons (each box answers on demand; no forced dual call).
- Collection-level entry point (D9).
- Pushing to remote (commit locally only).
