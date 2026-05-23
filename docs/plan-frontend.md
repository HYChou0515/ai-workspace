# RCA 3.0 — Frontend Plan

You're the frontend agent. This brief is self-contained — but if you
want context on the BE items you depend on, see
[`plan-backend.md`](./plan-backend.md).

**This plan supersedes the generic workspace-app frontend plan.** The
project pivoted to a vertical **Root-Cause Analysis** app for SMT /
AOI / yield engineers, with a fully-specified design at
`design_handoff_rca_3.0/`. The existing React shell will be rebuilt to
the design.

The BE-FE wire (events + routes) is in `plan-backend.md` §10. If you
change FE-side of the wire, mention it there in the same commit.

---

## 1. Design source of truth

`design_handoff_rca_3.0/` in repo root. Read its `README.md`
end-to-end before touching FE code — it has tokens, layout specs,
state model, interaction notes, copy. Highlights:

- **Two screens**: Home (investigation list) + Investigation
  workspace (VSCode-shaped shell).
- **Two routes**: `/home`, `/investigation/:id`.
- **Design tokens**: cream paper `#F1ECE0` / dark ink `#16181D` / **one
  accent orange** `#F0502E`. Inter Tight (display) + Inter (body) +
  JetBrains Mono (mono). 4px spacing grid. No drop shadows except the
  app-shell card. **Elevation = contrast, not depth.**
- **Brand**: SVGs in `design_handoff_rca_3.0/assets/` — `rca-mark.svg`,
  `rca-mark-light.svg`, `rca-logo-horizontal.svg`, `favicon.ico`. Ship
  as-is, do not redraw.

The HTML/JSX prototype is a **design reference**, not production code.
Recreate the designs in our React + Vite + TS environment, matching
tokens and component states.

---

## 2. What we keep from the prior workspace-app FE

- `package.json` toolchain (React 19 + Vite 6 + TS 5 + pnpm).
- `tsconfig.json` strictness.
- `vite.config.ts` proxy patterns (update the proxy paths once routes
  rename to `/investigation` etc.).
- SSE consumption pattern (`fetch` with streaming body + `aiter_lines`
  equivalent in JS). The existing `streamAgentEvents` generator is the
  template — extend to a sibling `streamCellEvents` for notebook
  execution.
- TypeScript type definitions for `AgentEvent`. Add `CellEvent` as a
  sibling union (see §6).
- Vitest setup (FE TDD discipline applies — see Conventions).

Everything else in `web/src/`:
- `App.tsx`, `Chat.tsx`, `WorkspaceList.tsx`, `FileBrowser.tsx`, the
  api/ wrappers, the styles — **scrap and rebuild** per the design.
  The data flows you implemented for hydration, fetch, etc. are
  educational but the layout/components are wrong shape now.

---

## 3. Design tokens — bake into the codebase

Reify the design's tokens once at top level, reuse everywhere. Two
acceptable approaches:

- **(a) CSS custom properties** in `web/src/styles/tokens.css`,
  imported once in `main.tsx`. Components use `var(--accent)` etc.
  Lean, no build-system surprises.
- **(b) JS module** in `web/src/tokens.ts` exporting `colors`,
  `space`, `type`, `radii` constants. Components import these.
  Type-safe, but harder to share with raw CSS.

Recommend **(a) CSS variables** plus a thin `tokens.ts` re-export for
components that need to read tokens in JS (e.g., inline SVG fill).

Colors, type scale, spacing, radii — all in the design README §
"Design tokens". Verbatim. Don't paraphrase.

**Fonts:** load Inter Tight (700/800), Inter (400/500/600), JetBrains
Mono (400/500/600) via `<link>` or `@fontsource/*` packages. Don't
inline.

---

## 4. Routing

Two routes, both client-side (we're a SPA against the BE behind us):

- `/` → Home
- `/investigations/:id` → Investigation workspace
- (`/investigations/:id?tab=brief|spc|pareto|fishbone|fivewhy|report`
  — optional, drives editor view selection deep-linkably.
  v1 candidate: omit, let editor tab state be ephemeral.)

Library: `react-router-dom` v6 is the boring default. Don't reach
for a heavier router unless we need data-loader features.

---

## 5. Brand & layout chrome

### 5.1 Brand assets

Copy SVGs from `design_handoff_rca_3.0/assets/` into `web/public/`:
- `rca-mark.svg`
- `rca-mark-light.svg`
- `rca-logo-horizontal.svg`
- `favicon.ico`

Referenced via `<img src="/rca-mark.svg">` or imported as JSX
components for inline color overrides. **The orange dot at the apex
is part of the mark — never strip it.**

### 5.2 Home screen layout

Per design README "1. Home — Investigation list":
- Two-column. Sidebar 240px (cream bg, right hairline). Main flex.
- Sidebar:
  - Header (mark + `RCA · 3.0` + subtitle + `+ New investigation`
    primary button).
  - Nav list (All open / Pinned / Owned by me / Watching / Recently
    viewed / Resolved (30d) / Abandoned (30d) / Templates). Counts
    come from `GET /investigation` filtered client-side.
  - Topics section (groups by `topics: string[]` from each
    investigation — count by topic + dot status).
  - Footer: user avatar + name + role + settings.
- Main:
  - Top bar (64px): 420px search input with ⌘K, spacer, bell,
    `Ask agent` button.
  - Page header: `INVESTIGATIONS` caps + H1 "N open · M critical" +
    3 metrics + filter strip.
  - Table of investigations with sticky header. Columns per design
    README. Click row → `/investigations/{id}`.

### 5.3 Investigation workspace layout

Per design README "2. Investigation workspace" — VSCode-shaped:
- Top bar (52px): back + mark + breadcrumb + severity/status chips +
  spacer + ⌘P command palette + model selector + members + bell +
  avatar.
- Activity bar (50px wide, left): Evidence (active) / Search /
  Source / Agent / Defect map / History / Reviewers + Settings.
- Sidebar (260px): EVIDENCE section + collapsible tree + Outline +
  Footer meta block. Files come from `GET
  /investigations/{id}/files`.
- Editor area:
  - Tab strip (38px): one tab per open file. Active = accent top
    border + white bg. Modified = warn dot in place of close x.
  - Breadcrumb strip (28px) + autosave indicator.
  - Report banner (conditional, when current view ≠ report).
  - Main content (scrollable) — file-type renderer (see §6).
  - Bottom panel (200px): Problems / Output / Terminal / Agent log /
    Run history tabs.
  - Status bar (28px ink bg): git stats / err / warn / agent status /
    watchers / spacer / cursor / encoding / language / kernel status
    / user. All mono 11.
- Agent panel (380px right column, border-left, cream bg) — see §7.

---

## 6. File renderers — one per file type

The "views" in the design (brief / SPC / Pareto / fishbone / 5-why /
report) are **just file-type renderers** picked by extension. The
editor area renders whichever file is the active tab.

**Architectural posture**: the BE is RCA-agnostic — it stores and
serves files but doesn't model 5-Why structure, fishbone schema,
hypotheses, corrective actions, or report versions. **All RCA
structure is conventions the agent follows when writing files, and
the FE renders by recognising those conventions.** A new investigation
type would not require any BE change — just new template files, an
updated agent prompt, and new FE renderers.

v1 needs the renderers below.

### F8. Notebook viewer `.ipynb` *(biggest item; depends on BE §7)*

The flagship feature.

- **Cell list**: render the notebook JSON (parse client-side). Each
  cell:
  - Run gutter (28×28 circle with play icon, `[N]` exec count below,
    accent ring if active/running).
  - Cell card with header chip (`python` / `markdown`) + status pill
    (`● ran in 0.34s` ok or `● running…` accent) + Explain button
    (sparkle icon) + `···` menu.
  - Code body in monospace (Monaco recommended, see §8).
  - Output area below (rendered per output type, see F9).
- **Run cell**: click play → `POST
  /investigations/{id}/notebooks/{path}/cells/{idx}/execute` body
  `{code: <current cell source>}` → SSE stream of `CellEvent`s.
  Render `CellStream` as terminal-style append; `CellDisplayData`
  per mime type; `CellError` as red traceback; `CellDone` finalizes
  the execution_count + duration. Stream closes after `CellDone`.
- **Cell interrupt**: `DELETE
  /investigations/{id}/notebooks/{path}/cells/{idx}/execute` — same
  pattern as chat interrupt.
- **Kernel status indicator**: `kernel py3.11 idle` (or `busy`,
  `dead`) in status bar. Restart Kernel button in tab strip area:
  `POST /investigations/{id}/notebooks/{path}/kernel/restart`.
- **Save**: on `CellDone`, FE PUTs the updated notebook JSON to
  `PUT /investigations/{id}/files/{path}` (the whole file — backend
  is nbformat-agnostic). Debounce to 1 save per cell-complete.
- **Empty state**: if file has 0 cells, show "+ Add cell" button +
  a single empty code cell in edit mode.

### F9. Output renderer

`CellDisplayData` carries a `data` dict keyed by mime type. Render
priority (first matching wins for a single output):
1. `image/png` → `<img src="data:image/png;base64,...">`
2. `text/html` → sanitized HTML (use `dompurify` or similar) inside a
   contained div. **Required for pandas DataFrame display.**
3. `text/plain` → `<pre>` mono.

ANSI escape codes in `CellError.traceback` → render with `ansi-to-html`
or hand-roll for the small subset (color codes only; no cursor
control). Match the design's red traceback aesthetic.

Skip for v1: `application/vnd.jupyter.widget-view+json` (ipywidgets),
`application/javascript`, `image/svg+xml`. Document as not-supported.

### F10. Markdown renderer `.md`

`brief.md`, `5-why.md`, `report.md` are all markdown.

- Use `react-markdown` + `remark-gfm`. Apply design typography
  (Inter Tight for headings, Inter for body) via CSS.
- **Edit mode toggle**: pencil icon in the tab area; click → swap to a
  textarea (or Monaco) for editing; Save → PUT to FileStore.
- v1: standalone markdown rendering is enough. The design's
  **8-section 8D report** with `D1 · Define team` etc. is just
  markdown headings — render as-is.

### F11. Report view (`report.v*.md` file-naming convention)

The backend has no `ReportVersion` resource — versioning is a file
naming convention. Reports live at `/report.v1.md`, `/report.v2.md`,
`/report.v3.md`, …; the highest N is current.

- On entering the report view: `GET
  /investigations/{id}/files?prefix=/report.v` → list of versions
  → derive { v: N, isCurrent: N === maxN } per file.
- Version pills (inline): `v1 · superseded`, `v2 · superseded`,
  `v3 · current`. Active = orange filled; inactive = ink-4 border.
  Click switches `selectedV` state → `GET /investigations/{id}/files/report.v{N}.md`.
- Superseded notice (cream-2 callout + clock icon) when selected
  version ≠ current.
- "Generate new version" button → ask the agent in chat: "Generate
  a new report version summarising current findings." The agent
  writes `/report.v{maxN+1}.md` via `write_file`. FE refreshes the
  file list after the agent's turn completes; new pill appears.
  *No dedicated POST /reports/generate endpoint.*
- Diagonal SUPERSEDED stamp (CSS `transform: rotate(-6deg)` + border)
  overlaid on body when viewing non-current.

If the agent wants to include version metadata ("what changed in
vN", author), it writes a sibling `/report.v{N}.meta.json` or uses
markdown frontmatter — the renderer's call.

### F12. Fishbone canvas `.canvas` *(read-only for v1)*

`.canvas` is a JSON file the agent writes; the FE renders it as the
6M fishbone SVG. The agent's system prompt teaches it the schema —
the BE has zero awareness of this format. Recommended schema for
the agent to use (the FE renderer follows the same convention):

```ts
{
  effect: string,
  branches: Array<{
    label: "Machine"|"Method"|"Material"|"Man"|"Measurement"|"Environment",
    side: "top"|"bot",
    items: Array<{ t: string, strong?: boolean }>,
  }>
}
```

v1: render via SVG (spine + 6 categories + branches; `strong: true`
in accent orange + bold). No editing — display only. If the JSON
doesn't match this shape, fall back to the raw `.json` renderer.

### F13. 5-Why structured view (`5-why.md` for v1)

The agent writes `5-why.md` with conventional structure under
`## Why #N` headings (or a structured sibling `.json` — see below).
The FE markdown renderer (F10) handles `.md` directly. The design's
confidence bars + corrective-actions chain are v1.5 — at that point
either:
- the agent learns to write `5-why.json` with `{ steps: [{q, a,
  confidence, root?}], actions: [{kind, title, owner, due}] }` and
  the FE adds a structured renderer for that, or
- we agree on a markdown extension (HTML-in-md, `<!-- meta: ... -->`
  comments) that the renderer parses.

Either way, **the BE doesn't model 5-Why structure** — it's a file
the agent writes and the FE renders.

---

## 7. Agent panel (right column, 380px) — the new "chat"

Per design README "Investigation workspace" → "Agent panel":

- Header: mark icon + "RCA Agent" + sub-line (current step) + status
  chip.
- Progress bar: 6 segments showing investigation plan progress.
  Static for v1 (just renders a status); driven by Conversation /
  agent-run state.
- Conversation list (scrollable):
  - User message: avatar + name + timestamp + body.
  - Agent message: 20×20 ink-bg square with mark + "Agent" + body.
  - Tool call: white card with check/play + `name(args)` mono +
    `→ result` mono + chevron.
- Suggestion chips above composer (3, with sparkle icons; mapping
  per current editor view per the design's `SUGGESTIONS` map).
- Composer: card + textarea + attach + send. `⌘↵` to send.

Data flows are the existing ones:
- `GET /conversation` → hydrate on mount (already wired in prior FE
  code; reuse).
- `POST /investigations/{id}/messages` → SSE → render incoming events
  in conversation.
- `DELETE /investigations/{id}/messages/current` → stop.

---

## 8. Cell editor — Monaco

For `.ipynb` cells (and optionally `.md` edit mode), use **Monaco**.
It's the editor VSCode runs and ships with full Python highlighting,
multi-cursor, command palette, etc. Tree-shaken bundle is ~1 MB —
acceptable for our app.

- Package: `@monaco-editor/react`.
- Configure with the Inter / JetBrains Mono fonts and a custom theme
  matching our cream/ink palette.
- One Monaco instance per code cell. Adopt the design's cell card
  (border, padding, header) as the chrome around Monaco.

Alternative: CodeMirror 6 (smaller, ~300 KB) — acceptable if Monaco
bundle is a problem. Decide later; behind a `web/src/components/CellEditor.tsx`
abstraction.

---

## 9. NewInvestigation modal

Triggered by `+ New investigation` in Home sidebar.

Per Q11-final + grill-me reconciliation:
- Backdrop + 620px centered modal (cream, radius 12).
- Header: "New investigation" + close.
- Body — **simplified from design**:
  - **title** (required, accent border on focus)
  - **description** (textarea, replaces design's "initial brief")
  - **topics** (chip-input — type-and-enter to add tag chips)
  - **severity** (segmented picker P0–P4)
  - **product** (text)
  - *Dropped vs design's original modal: `lot`, `line` (replaced by
    topics), `owner` picker, `status` picker, template picker,
    auto-agent ribbon.* `owner` is auto-set to current user, `status`
    auto-set to `triaging`.
- Footer: Cancel + `Create & ask agent` primary.
- Submit: `POST /investigation` with the fields → server seeds the
  default template → navigate to `/investigations/{newId}`.

---

## 10. Status flow widgets

Severity / Status chips appear all over the design (table rows,
breadcrumbs, report header). Single component:

```tsx
<SeverityChip level="P1" />          // P0/P1 → err tone; P2 → warn; P3/P4 → ok
<StatusChip status="triaging" />     // triaging → warn; awaiting_review → info; resolved → ok; abandoned → text-paper-d
```

Live in `web/src/components/StatusChip.tsx`.

---

## 11. Convention reminders

- **TypeScript strict** stays.
- **`pnpm run build` must pass** before commit.
- **No new heavy deps without a clear reason.** Monaco is the one
  meaningful add (§8). Optional: `dompurify`, `react-markdown`,
  `remark-gfm`. Avoid: `axios`, full UI kits, redux/zustand for a
  v1 with small state.
- **CSS via tokens** — see §3. Avoid styled-components for v1.
- **Don't touch backend.** If a route/event is missing, mention it in
  `plan-backend.md` §10 contracts table.
- **FE tests via vitest** (already configured). Match the existing
  test file patterns (`*.test.ts(x)` in src/).
- **No emojis in production UI** (the design says so explicitly).

---

## 12. Order of work

Land in this rough order:

1. **Tokens + brand assets** (§3, §5.1) — once they exist, every
   subsequent component renders correctly.
2. **Router shell + status chips** (§4, §10) — minimal Home + empty
   Investigation shell. Routes + breadcrumb + severity/status
   components shared everywhere.
3. **Home screen** (§5.2) — sidebar nav + investigation table. Hits
   `GET /investigation`; table click navigates.
4. **NewInvestigation modal** (§9) — depends on BE's seeded-template
   create endpoint.
5. **Investigation workspace chrome** (§5.3) — full VSCode layout
   without renderers (everything empty / placeholders).
6. **Agent panel** (§7) — port the chat over.
7. **Markdown renderer** (§F10) — gets brief.md / 5-why.md / report.md
   showing.
8. **Notebook viewer** (§F8, F9) — depends on BE §7 (kernel + cell
   SSE). Biggest single FE chunk.
9. **Report view + version selector** (§F11) — pure FE; iterates
   `/report.v*.md` files from existing files API.
10. **Fishbone read-only renderer** (§F12) — minor.
11. **5-Why structured editor** (§F13) — v1.5.

## 13. Things you DO NOT worry about

- LLM choice / model selector — backend.
- Sandbox lifecycle / kernel ports / FS sync — backend.
- Authentication beyond default-user — backend.
- Real data integrations (MES / SPC / AOI) — backend has them all
  mocked for v1; you just render what the wire returns.
- specstar admin UI — separate URL space (`/docs`, `/investigation/data`
  etc.), out of scope.
