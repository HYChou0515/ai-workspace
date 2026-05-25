# RCA 3.0 — Design ideas

A snapshot of the thinking behind the current prototype. Not a spec — read `README.md` for that. Read this when you want to know **why** something is shaped the way it is, or what we considered and rejected.

---

## 1 · What the product is

An AI agent for **defect root-cause analysis** in manufacturing. The agent doesn't replace the engineer — it pairs with one, pulls SPC / AOI / log data, draws charts, drafts hypotheses, and co-writes the final 8D report. The engineer drives, the agent does the legwork and shows its work.

The single most important verb: **find the root cause**. Everything in the UI should make that feel close, not far.

---

## 2 · Design principles (the ones we keep returning to)

**Quiet by default.** The brand orange (`#F0502E`) appears once or twice per screen — for the answer, the action, or the agent's current focus. If you see orange in three places it's a bug.

**Mono speaks data.** Tag IDs, sensor names, timestamps, code — anything machine-shaped — uses JetBrains Mono. Human prose uses Inter. That contrast does a lot of the heavy lifting; we don't need badges or icons to say "this is a code thing."

**No drop shadows.** Cards are hairlines on cream paper. Elevation comes from contrast and dark surfaces (status bar, banners), not depth. It keeps the visual language flat and serious — closer to a printed report than a SaaS dashboard.

**Show the agent's hands.** Tool calls, plan steps, citations are visible by default. We don't hide the agent behind a friendly chat bubble; we show what it's doing, and we cite. If the agent can't show its source, it shouldn't be making the claim.

**Defects are real.** Real photos, real waveforms, real logs. The product is about physical things going wrong. UI should leave room for the artifact, not just numbers.

**One investigation per screen.** Resist tabs of unrelated incidents in the chrome. Split when comparing; otherwise focus. The cognitive cost of one investigation is already high.

---

## 3 · Direction choices we considered and rejected

### 3.1 · Home layout
- **Considered**: Notion-style empty canvas with pinned cards.
- **Picked**: Dense table.
- **Why**: Investigators triage. A spreadsheet shape supports skim + sort + filter much better than freeform cards. The flashy version made it feel like a marketing dashboard; engineers wanted to see 14 things at once.

### 3.2 · Investigation workspace shell
- **Considered**: Notebook-only (Jupyter-shaped) or a doc editor with sidebar.
- **Picked**: VSCode-shaped.
- **Why**: Engineers already live in IDEs and notebooks. The activity bar / sidebar / tabbed editor / bottom panel / status bar pattern is muscle memory. We get scrubbable file tree, multi-view tabs (notebook / chart / canvas / md), terminal-shaped agent log, and a status bar for "what's the agent doing right now" — all without inventing a new metaphor.

### 3.3 · Final report
- **Considered**: A living doc that updates in place; like Notion.
- **Picked**: Versioned reports — each generation supersedes the last.
- **Why**: RCA reports are signed-off artifacts. Quality and ops need to know which version was actually submitted. Versioning makes the supersedence explicit (you literally see `v2 superseded` with a tilted stamp), and gives the agent room to re-generate without overwriting human-reviewed sections silently.

### 3.4 · Status taxonomy
We landed on: `draft → triaging → awaiting review → resolved`, with `abandoned` as a terminal failure (closed without root cause).
- "Root cause found" was rejected because the actual moment users care about isn't finding the cause — it's getting sign-off on the corresponding report. So the chip points to the *next* state, not the previous milestone.
- `abandoned` is deliberately heavier than "closed" — these are cases that left without an answer; they should haunt the org.

### 3.5 · Knowledge base
- **Considered**: KB as a flat list of documents; or as a set of connectors to external systems (Confluence / Notion / SharePoint).
- **Picked**: **Collections** as the unit. Users curate, upload files or folders, and pick which collections to use as context when chatting.
- **Why**: The agent's quality depends on the user *trusting that the right context is in the prompt*. A collection picker (the orange chips in the KB drawer) makes that picking explicit. Connectors hide it; flat lists are unwieldy past a few hundred files. Collections also map cleanly to how teams actually organize: "reflow notes", "SOPs", "supplier reports — Q1".
- One collection is auto-managed: **Past investigations**. Closed cases land there. This is the bridge that makes the agent get smarter over time.

### 3.6 · Citation visibility
- **Considered**: Citations only when asked ("show sources").
- **Picked**: Citations inline on every agent answer, AND per-doc / per-collection cite counts visible in management.
- **Why**: Two reasons. (a) Every agent answer should be auditable; quality engineers will want to see the source before trusting the claim. (b) Cite counts at the collection and doc level let the KB owner spot stale or useless docs — if a doc has been sitting in a collection for a year with 0 citations, archive it.

### 3.7 · KB chat: drawer vs page
- **Considered**: Open the KB chat as a separate page.
- **Picked**: Right-side drawer over the current view.
- **Why**: Asking the KB shouldn't require leaving the current investigation. The drawer keeps the context visible behind it. The drawer is a *peek*, not a *trip*. Long-form research moves to the Chats page.

### 3.8 · Collection details: drawer vs page
- **Considered**: Drawer (consistent with KB chat drawer).
- **Picked**: Full page.
- **Why**: A collection is an *object* with its own life — meta, docs, activity, permissions. It deserves a URL and breadcrumb. Drawer-inside-drawer would be cognitively expensive. The downside (extra nav step) is worth it.

### 3.9 · Doc preview: chunks vs file
- **Considered**: Lead with the indexed chunks (so the user knows what the agent will actually see).
- **Picked**: Lead with the file. Chunks are a debug view.
- **Why**: When a user opens a doc, they want to read the doc, not see how we tokenized it. Chunks are useful for KB tuning, but that's a small audience. Default to the human experience; expose chunks behind an explicit click.

### 3.10 · Topic vs Line vs Lot
- **Considered**: Line and Lot as first-class metadata (factory floor convention).
- **Picked**: Topic (a free-form tag, usually one but optionally many). Lot was dropped.
- **Why**: An investigation spans whatever the engineer says it spans. Production-line-as-primary-axis was too restrictive for cross-line or supplier issues. Lot was noisy — most investigations touch multiple lots, and a single `lot` chip would lie.

---

## 4 · Patterns that recur

These are reusable across the surface:

| Pattern | Where it shows up |
|---|---|
| **Caps mono label** (`CAPS_LABEL`) | Section headers, meta blocks, sources |
| **Tinted chip with status dot** | Severity, status, kind |
| **Orange chip for one thing per row** | "report · v3", "agent running", "pinned" |
| **Inline citation `[N]` + source card** | Every agent answer with evidence |
| **Auto-managed badge (sparkle icon)** | Past investigations collection; agent-drafted report sections |
| **Right-side drawer for "peek"** | Doc preview, collection detail (older version), agent panel composer |
| **Full page for "object"** | Investigation, collection (current), report (versioned) |
| **Cited-count metric** | Collection KPI; per-doc table column; doc preview meta line |

---

## 5 · Things we deliberately don't have (yet)

- **Mobile** — desktop only. Investigators sit at workstations.
- **Drop shadows** — see principles.
- **Onboarding tour** — the chrome is dense but learnable; an "intro" overlay would just delay the engineer from doing their job. We'll add empty-state copy where it matters.
- **Light/dark toggle** — the palette is the brand. Cream paper + ink. A dark mode would dilute it.
- **Inline doc editing** — KB documents are uploaded artifacts. Editing happens in the source tool, then re-uploaded. We can revisit later if it hurts.
- **Notifications panel** — `bell` icon is a placeholder. We have no notification model yet; build the agent + KB + reports first.
- **Per-cell collaboration cursors in the notebook** — single-editor at a time was the constraint user gave us. Multi-cursor is doable but not a priority.

---

## 6 · Notifications model

The bell icon in every top bar is a placeholder. The notifications dropdown should cover:

- **@mentions** — you were tagged in an investigation chat, agent message, or report review comment.
- **Status changes** — an investigation you watch flipped status (`triaging → awaiting review`, `→ resolved`, `→ abandoned`), or a new report version was generated.
- **Agent completion** — a long-running agent task you queued finished (e.g. "analyze the last 30 days of wirebond pull strength").
- **Assignments** — a new investigation lists you as owner or reviewer.
- **Sharing** — someone shared a collection with you, or invited you to watch an investigation.
- **System** — a KB document failed to index; permission changes; org-level announcements.

The dropdown should show the most recent N (probably 20), with "Mark all read" and a link to a full notifications page (settings + filters). Notifications respect the visibility rules — chats are private to you so chat-based notifications can only come from things you're a participant in.

---

## 7 · Open questions

1. **How does the agent know which collections to use for an investigation chat?** Today the drawer is global — when you start a chat from an investigation, should the collections be pre-filtered (e.g. "Past investigations" + the topic-tag-matching ones)?

2. **Can two investigations share a single chat thread?** When two cases turn out to be the same root cause, do we merge their chats?

3. **What does "Awaiting review" mean in practice?** Who is the reviewer? Is it 1:1 with the report's `Submit for review` button, or a separate workflow?

4. **Abandoned cases** — should the agent still index them into the KB? Or skip them since there's no resolved root cause to learn from?

5. **Re-index UX** — what does the user see while a doc is being re-chunked? Right now we show `Re-index` as a button but no progress state.

6. **Multi-version diff** — when a user views v2 vs v3 of a report, would they want a side-by-side diff? We currently show "What changed in vN" as a one-line summary; a full diff might be cheap to add.

---

## 8 · Visual language sources of inspiration

- **Linear** — for the dense table, status chips, command palette feel.
- **Stripe docs / Vercel** — for the mono caps labels and the restrained accent.
- **A printed engineering report** — for the cream paper, hairlines, and "no shadows" rule.
- **IDE chrome (VSCode)** — for the investigation shell.

We deliberately avoided:
- Notion's "everything is a block" feel — too soft for the seriousness of the domain.
- Tailwind UI default look — too generic, no point of view.
- Anything with gradient cards or glassmorphism — would clash with the typographic restraint.

---

## 9 · If you're a developer reading this

The Figma equivalent is this prototype. Don't try to ship the HTML — recreate it in your stack using the tokens in `README.md`. The two files are companions:

- `README.md` answers "what does this look like, exactly?"
- `design.md` (this file) answers "why does it look like that?"

When you hit a decision the README doesn't cover, prefer the principle in §2 over inventing something new.
