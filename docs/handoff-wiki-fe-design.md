# Design handoff — Knowledge Wiki (frontend)

> Copy this whole file into a fresh Claude session (with the frontend-design
> skill) to design the **Knowledge Wiki** views. It's self-contained — you
> don't need the codebase. Produce working React/HTML+CSS mockups.

---

## 0 · The one-paragraph brief

We're adding a **Knowledge Wiki** to an existing RCA / knowledge-base web app.
A "collection" is a folder of uploaded documents. Today the assistant answers by
retrieving passages (chunk search). We're adding a **second, parallel** way: an
AI quietly maintains a **wiki** for the collection — a set of interlinked
markdown pages (an index, entity pages, concept pages) that it writes and
updates every time a document is added. At query time the assistant can read
this wiki to answer. **You are designing how a person BROWSES that wiki, and the
small controls that turn it on and use it.** The wiki is **AI-owned and
read-only** to the user — they explore it, they don't edit it.

Think: a calm, living, internal Wikipedia that writes itself — read-only,
trustworthy, easy to wander.

---

## 1 · Match the existing design language (don't invent a new one)

This drops into a mature product. **Harmonize** — refine within this system, do
not bolt on a different aesthetic. The vibe is **warm editorial / paper**, calm,
confident, a single hot accent used sparingly.

**Color tokens (light theme only):**
```
--accent      #F0502E   /* the ONE orange — one primary action/highlight per screen */
--accent-h    #D8431F   /* accent hover */
--accent-soft #FCE4DC   /* accent tint background */
--ink         #16181D   /* near-black, primary text / dark surfaces */
--paper       #F1ECE0   /* warm cream — page background */
--paper-2     #E5E0D2   /* cards / secondary surface */
--paper-3     #D8D2C2   /* hairline borders / dividers */
--white       #FBF9F4   /* raised cards, inputs */
--text-paper    #1A1B1F /* body text on paper */
--text-paper-d  #5C5F66 /* muted text */
--text-paper-d2 #8A8C90 /* faint text / metadata */
--ok #3A8A4A  --warn #C68A2E  --err #C44A3A  --info #2D6CC9
```
**Type:**
```
display: "Inter Tight"     (headings)
body:    "Inter"           (UI + prose)
mono:    "JetBrains Mono"  (ids, paths, code, small caps labels)
```
Sizes: display 22–40px; body 14px; small 12–13px; xs 11px. Generous line-height
(~1.5). Radii are gentle (6–10px). Borders are 1px hairlines in `--paper-3`.
Shadows are soft and rare. **The accent appears once per screen** — usually the
primary button or one highlighted state.

Existing furniture you're extending: a left nav with tabs, a topbar with a
title, collection cards in a grid, a documents table, and a right-hand **drawer**
for viewing a document (header eyebrow + title + a meta line + an actions row +
rendered markdown body). The wiki views should feel like siblings of these.

---

## 2 · What to design

### A. Wiki browser (the main piece) — a read-only, navigable knowledge base

Lives as a **"Wiki" view of one collection** (a tab/section alongside the
existing "Documents" view). Two-pane, read-only.

**Layout (suggested, improve on it):**
```
┌ Collection: "Reflow process"  ·  Wiki ─────────────────────────────────┐
│  [📖 AI-maintained]   last updated 4 min ago            [ Rebuild ]      │
├───────────────┬─────────────────────────────────────────────────────────┤
│  PAGES        │   # Reflow Zone 3                                         │
│  ▸ index      │                                                          │
│  ▾ entities   │   Zone 3 of the reflow oven runs hottest…                │
│     reflow-…  │   Linked to [[oven-profile]] and [[voiding]].            │
│     oven-…    │                                                          │
│  ▾ concepts   │   ## Key facts                                           │
│     voiding   │   - setpoint 245 °C …                                    │
│     …         │                                                          │
│               │   ───────────────────────────────────────────────       │
│               │   Sources:  reflow-spec.pdf · qual-report-25w14.md       │
└───────────────┴─────────────────────────────────────────────────────────┘
```

Requirements & details:
- **Left pane = page tree** (read-only). Pages live under folders like
  `index.md`, `entities/*.md`, `concepts/*.md`. Show it as a friendly tree or
  grouped list — "Index" pinned at top, then "Entities", "Concepts". Paths are
  mono. **No new/rename/delete/edit affordances** — this is AI-owned.
- **Right pane = rendered markdown** of the selected page (read-only prose, nice
  typography — this is the moment to make reading feel good).
- **`[[wikilinks]]`** inside the prose are **clickable** and navigate to that
  page within the wiki. Style them as quiet internal links (accent-h underline,
  not loud). This interlinking is the soul of the feature — make wandering feel
  effortless.
- **"Sources:" footer** on each page lists the source documents it was
  synthesized from — each is a **clickable chip/link** that opens that source
  document (auditability: wiki → real source). Visually distinct from wikilinks
  (these leave the wiki, into the documents).
- **Header**: collection name + a small **"AI-maintained" badge** (so the user
  knows they shouldn't expect to edit it, and that it self-updates), a **last
  updated** timestamp, and **one** primary action: **Rebuild** (re-runs the AI
  over the whole collection). Rebuild is the only write the user can trigger.
- **States** (design all four):
  1. **Empty / not built** — collection has the wiki enabled but nothing yet:
     a calm empty state ("The wiki for this collection hasn't been built yet")
     + a build CTA. Encouraging, not an error.
  2. **Building / maintaining** — the AI is writing pages right now (after an
     upload or a Rebuild): a live, low-key "Updating the wiki…" indicator.
     Reading the existing pages should still work while it updates.
  3. **Ready** — the normal browsing state above.
  4. **Disabled** — collection doesn't have the wiki turned on: this view
     shouldn't appear, OR shows a gentle "Turn on the wiki for this collection"
     prompt. (Your call which is cleaner.)

Make it feel like a place you'd *want* to read — entity pages and concept pages
that cross-reference each other, an index that orients you. Calm, trustworthy,
a little bit magical that it wrote itself. Read-only is a feature here, not a
limitation — convey "curated, settled knowledge," not "you can't touch this."

### B. Turn-it-on toggles (small)

When creating or editing a collection, the user picks which retrieval the
collection uses — **two independent toggles, both can be on**. Keep copy plain
(no internal jargon like "RAG / chunks / embeddings / vector"):
- **Document search** — "Find passages from your documents. (Recommended)"  → default ON
- **Knowledge wiki** — "An AI-built, cross-linked summary the assistant reads
  to answer. Updates as you upload."  → default OFF

Design this as a clean pair of toggles/cards in the collection create modal and
in collection settings. If both are on, hint that answers draw on both.

### C. "Search the wiki" query toggle (tiny)

In the chat composer there's a small "search depth" advanced popover (model +
effort + depth controls). When the chat's collection(s) have the wiki enabled,
add one more **advanced checkbox: "Search the wiki"** (per-message, off by
default unless the user opts in). Just design the one extra row so it sits
naturally among the existing advanced toggles. Copy: "Search the wiki" /
sub-line "Let the assistant read the collection's wiki for this question."

### D. Collection card badge (tiny, optional)

On the collection cards grid, a collection that has a wiki could show a small
**"Wiki" badge/glyph** so users can tell at a glance. One small idea, your call.

---

## 3 · Data the views work with (so interactions are concrete)

You're designing UI; here are the shapes so states/links are real:

- **Collection**: `{ name, description, icon, use_rag: bool, use_wiki: bool,
  doc_count, updated_at, … }`
- **Wiki page list**: `[{ path: "/index.md" }, { path: "/entities/reflow.md" },
  …]` (one collection → many pages; paths are the tree).
- **Wiki page content**: markdown text for a `path`. Contains `[[wikilinks]]`
  (relative to other pages) and a trailing `Sources:` list referencing source
  documents by name.
- **Source document** (the link target of `Sources:`): `{ filename, … }` —
  opening one reuses the existing document viewer.
- **Wiki status**: `building | ready | empty` + a `last_built_at` timestamp.
- **Rebuild**: a button → kicks an async rebuild → status goes `building` →
  `ready`. Poll/refresh while building.

Citations in the assistant's answers (when it used the wiki) point at the
**source documents** (same citation cards that already exist) — you don't need
to redesign citations, just know wiki answers are auditable back to real docs.

---

## 4 · Hard constraints

- **Read-only wiki.** No user editing/creating/deleting wiki pages anywhere. The
  only user-triggered write is the **Rebuild** button. Don't add edit pencils,
  context menus, drag-reorder, etc. on wiki pages.
- **Plain language.** User-facing copy must not expose internal/technical nouns
  (no "RAG", "chunks", "embeddings", "vector", "agent", "LLM", file formats,
  resource ids). Describe the *action/outcome* in human terms.
- **Match the existing aesthetic** (Section 1). Light theme. One accent per
  screen. Warm paper, ink, Inter / JetBrains Mono.
- **Calm and legible.** This is a reading surface — prioritize typography,
  whitespace, and effortless navigation over flourishes.

---

## 5 · Deliverables

1. The **Wiki browser** (Section A) in all four states (empty / building / ready
   / disabled), with the page tree, rendered page, clickable `[[wikilinks]]`, and
   the `Sources:` footer links. This is the main deliverable — make it sing.
2. The **two collection toggles** (Section B) in the create/edit context.
3. The **"Search the wiki"** advanced row (Section C).
4. (Optional) the collection-card **Wiki badge** (Section D).

Working React or HTML/CSS, using the tokens above. Show the states. Annotate any
interaction (hover, navigate, rebuild, building→ready) briefly. Bias toward
something a process engineer would trust and enjoy reading.
