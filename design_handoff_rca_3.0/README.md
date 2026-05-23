# Handoff: RCA 3.0

> Defect root-cause analysis AI agent. Investigators (process engineers, QE, yield engineers) work with a chat-driven agent that pulls SPC data, AOI defects, logs, and photos to find root causes and co-draft 8D reports.

## About the design files

The HTML files in this bundle are **design references**, not production code. They're React prototypes built with inline Babel + CDN React for fast iteration in a design environment. Your task is to **recreate these designs in the target codebase's existing environment** (React + your component library / framework of choice) — using its established patterns, design tokens, and routing. Do not ship the HTML; treat it as a spec.

## Fidelity

**High-fidelity.** All colors, typography, spacing, and component states are intentional. Match them.

## How to read this bundle

- `RCA 3.0 prototype.html` — the live clickable prototype. Open this first to understand flow.
- `rca/system.jsx` — design tokens (colors, type, spacing) + atomic components (Btn, RcaChip, Card, Avatar, I icons). Treat this as the source of truth for tokens.
- `rca/home.jsx` — Home (investigation list) view.
- `rca/investigation.jsx` — Main investigation workspace (VSCode-style shell).
- `rca/views/analyses.jsx` — Pareto, Fishbone, 5-Why, Report (with versioning), New Investigation modal.
- `rca/app.jsx` — Top-level router/state machine. Two routes: `home` and `investigation`.
- `assets/` — Brand mark SVGs and favicon. **Ship the SVGs as-is**; they are the canonical brand.

---

## Design tokens

### Colors

```
--ink:         #16181D   /* primary dark surface, status bar, banner */
--ink-2:       #1A1B1F   /* stroke on light */
--ink-3:       #23262E   /* elevated dark surface */
--ink-4:       #2E323B   /* border on dark */

--paper:       #F1ECE0   /* primary cream — page bg */
--paper-2:     #E5E0D2   /* alt cream / hover */
--paper-3:     #D8D2C2   /* hairline border on paper */
--white:       #FBF9F4   /* card surface */

--accent:      #F0502E   /* THE ONE ORANGE — use sparingly */
--accent-h:    #D8431F   /* hover */
--accent-soft: #FCE4DC   /* wash, callout bg */

--text-paper:    #1A1B1F   /* body on paper */
--text-paper-d:  #5C5F66   /* dim */
--text-paper-d2: #8A8C90   /* dimmer */
--text-dark:     #F1ECE0   /* body on dark */
--text-dark-d:   #9CA0AB   /* dim */

--ok:    #3A8A4A
--warn:  #C68A2E
--err:   #C44A3A
--info:  #2D6CC9
```

**Accent rule**: orange is for ONE primary action / answer per screen. Don't sprinkle it. The dot in the logo and the highlight in `RCA . 3.0` are exceptions (part of the brand mark).

### Typography

- **Display**: Inter Tight 700/800 — page titles, section headings, big numerics. Letter-spacing -0.025em to -0.035em on the largest sizes.
- **Body**: Inter 400/500/600 — paragraphs, buttons, labels.
- **Mono**: JetBrains Mono 400/500/600 — IDs, timestamps, code, sensor names, uppercase caps labels.

**Scale**
```
display-xl: 56 / 1.05  /* hero */
display-lg: 40 / 1.10  /* page H1 */
display-md: 28 / 1.15  /* section H2 */
display-sm: 22 / 1.20  /* card title */
body-lg:    18 / 1.55
body:       14 / 1.55  /* default */
body-sm:    13 / 1.5
small:      12 / 1.5
xs:         11 / 1.5
mono-caps:  11   /* uppercase, letter-spacing 0.12em — section labels */
```

### Geometry

- **Spacing**: 4px base. Scale 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64.
- **Radii**: 4 (chip), 6 (btn / input), 8 (card), 12 (modal), 50% (avatar).
- **Borders**: 1px hairlines. No drop shadows anywhere except the prototype's app-shell card (`box-shadow: 0 6px 40px rgba(20,22,28,.08)`). Elevation = contrast, not depth.

---

## Screens

### 1. Home — Investigation list

**Path**: `/`
**Layout**: Two-column. Left sidebar 240px, main content flex.

**Sidebar (240px, paper bg, right hairline)**
- **Header block** (padding 20/18/16, border-bottom):
  - Mark (40px) + `RCA · 3.0` (24px Inter Tight 800) horizontally with small 5×5 orange square between letters
  - `Analysis · AI · Agent` — Inter Tight Mono 8.5px, letter-spacing 0.08em, uppercase, orange period separators
  - `+ New investigation` — full-width primary button (orange)
- **Nav list** (padding 8): icon + label + badge. Active = accent-soft bg, accent-h text.
  - All open · Pinned · Owned by me · Watching · Recently viewed · Resolved (30d) · Abandoned (30d) · Templates
- **Topics section** (caps label "TOPICS"): line items with status dot (`●` accent if has open inc, gray if none) + count. e.g., `SMT 1 · 2`.
- **Footer** (border-top, paper-2 bg): user avatar + name + role + settings icon.

**Main**
- **Top bar (64px)**: 420px search input (with `⌘K` shortcut chip) + spacer + bell (3 unread) + `Ask agent` button.
- **Page header (padding 28)**: `INVESTIGATIONS` caps label + H1 "14 open · 4 critical" (40px) + subtitle + 3 metrics (Resolution time, Open P1, Agent runs).
- **Tabs strip**: All · My open · Watching · Triaging · Awaiting review · Resolved · Abandoned — active tab has 2px accent underline + count in accent mono.
- **Filter strip**: Filter / Severity / Line / Owner / Updated buttons + sort selector + table/grid toggle.
- **Table**: Sticky header row + N data rows. Columns: pin (32) · ID (100) · Investigation (2.6fr) · Severity (0.9fr) · Line · Product (1.3fr) · Owner (1fr) · Updated (1fr) · Agent (1.1fr — sparkline) · `···` (60). First row has accent-soft tinted bg.
- **Row click** → navigate to that investigation.

**Investigation card content** (the "Investigation" column):
- Bold title (14px 600)
- Summary (12px paper-d)
- Chip row: status (dot chip) · `agent` (accentSolid, sparkle icon) if running · `report · v3` (accent, file icon) if reportV set · `lot 25-W14` (outline)

### 2. Investigation workspace

**Path**: `/investigation/:id`
**Layout**: 1440×900 default. VSCode-shaped.

**Top bar (52px)**:
- Back button "← All" (if onBack — i.e., not a deep link)
- Mark (22px compact) — no subtitle in this size
- Vertical divider
- Breadcrumb: acme › SMT process › **INC-2026-0142** (bold) + severity chip (P1 err) + status chip (triaging warn)
- Spacer
- 320px command palette search with `⌘P`
- Model selector dropdown (`claude-opus-4 ▾`)
- Members count (4) · bell · avatar

**Activity bar (50px wide, left, paper bg, right hairline)**
- Icons stacked: Evidence (folder, active) · Search · Source (git, badge 3) · Agent (sparkle) · Defect map (bug) · History (clock) · Reviewers (users)
- Active item has 2px accent left border + accent-soft bg.
- Bottom: settings.

**Sidebar (260px, paper bg)**
- Section header "EVIDENCE" + `+`.
- Collapsible sections (chevron + caps label):
  - **Open**: files currently open in tabs (active = accent-soft tint, 2px accent left border)
  - **Investigation files**: tree — `📁 data` (csv/parquet), `📁 photos / x-rays`, `📁 analyses` (notebooks/canvas/md files), then `.rca` config folder
    - Files can have SCM badges on the right: `M` (modified, warn), `A` (added, ok), `U` (untracked, accent)
  - **Outline**: H1/H2 nav within the current view (Context / Hypotheses / Corrective actions)
- **Footer block** (margin-top auto, border-top, paper-2 bg): investigation meta — Severity / Status / Owner / Line / Lot / Opened.

**Editor area (flex, right of sidebar)**
- **Tab strip (38px)**: 6 file tabs corresponding to views. Click → switches `view` state. Active tab has accent top border + white bg. Modified files have a 7px warn dot instead of close ×. Right side: split / layers / Run-all buttons. `whitespace: nowrap` on all tabs.
  - Tabs: `brief.md` · `drift.ipynb` · `pareto.ipynb` · `fishbone.canvas` · `5-why.md` · `report.md`
- **Breadcrumb strip (28px)**: folder › file › section navigation. Updates per view. Right side: "autosaved Xs ago" (mono, paper-d2).
- **Report banner (conditional)**: When `view !== "report"`, dark `#16181D` ribbon with:
  - File icon (accent)
  - "Final report"
  - `v3 · current` (accent mono)
  - "v2 superseded 16:08 · v1 superseded 14:48" (dark-d mono)
  - Buttons: `Generate new version` (ghost on dark) · `Open` (solid on dark, arrow icon)
  - Click `Open` → switches view to `report`.
- **Main content** (scrollable, padding 20/22): the view body. See *Views* below.
- **Bottom panel (200px, border-top)**:
  - **Tab strip (32px)**: Problems (badge 2) · Output · Terminal (badge 1) · Agent log (active) · Run history. Active has 2px accent underline.
  - **Body**: agent log lines — timestamp (paper-d2, 64px) · kind (info/paper-d/accent/warn, 60px) · message. All mono 12px.
- **Status bar (28px, ink bg, text-dark)**: git branch (with icon) · ↑ ↓ counts · err count · warn count · agent status · watchers · spacer · cursor pos · UTF-8 · language · kernel status · user. All mono 11.

**Agent panel (380px right column, border-left, paper bg)**
- **Header**: 18px mark icon + "RCA Agent" + sub-line "investigating · 4/6 steps" + status chip (accentSolid + sparkle icon).
- **Progress bar (border-bottom)**: 6 horizontal segments — green if done, half-fill if running, paper-3 if pending. Caption "step 4 · finding correlations".
- **Conversation (scrollable, padding 14/16, gap 14)**: alternating user messages, agent messages, and tool-call cards.
  - **User message**: avatar (20) + name + timestamp; body indented 28.
  - **Agent message**: 20×20 ink-bg rounded square with mark inside + "Agent" + ●running indicator; body indented 28. Supports compact mode + tentative ("thinking…").
  - **Tool call**: white card (margin-left 28, border 1px paper-3, padding 8/10) — check icon (ok) or play icon (accent, running) + `name(args)` (mono ink) + `→ result` (mono paper-d) + chevron right.
- **Suggestion chips (top of composer)**: 3 rounded chips with sparkle icon + label. Click → injects user message + canned agent response, optionally calls `onView(targetView)` to navigate.
  - Suggestions adapt to current view (see `SUGGESTIONS` map in `investigation.jsx`).
- **Composer**: white card, 13px placeholder, attach button + send button (primary). `⌘↵` hint.

### 3. Views (inside investigation)

State: `view ∈ { brief, spc, pareto, fishbone, fivewhy, report }`. Driven by tabs or sidebar clicks or agent chip clicks.

#### Brief
- Heading: `INVESTIGATION · INC-...` caps label + H1 + summary paragraph (max-width 720).
- 3 hypothesis markdown cells (each with [n] gutter + play circle + cell card with "markdown" chip header).

#### SPC analysis
- Heading.
- Code cell `[1]` (python, ok status, "ran in 0.34s") with monospace code.
- Output cell — full custom SPC chart SVG (line · zone-3 set-point vs actual + void rate overlay + annotation marker at 08-14 13:30 + LSL line + set-point line + legend).
- **Callout (accent)**: agent observation about the drift.
- Code cell `[2]` (python, running status) for correlation.

#### Pareto
- Header with caps label + H1 + paragraph + 14-day filter + by-board filter + download icon.
- SVG chart: 8 bars (top 3 accent, rest ink-2) + cumulative orange line + 80% reference (warn dashed).
- Legend below.

#### Fishbone (6M)
- Header + agent-suggest button.
- Custom SVG: spine + 6 categories (Machine / Method / Material / Man / Measurement / Environment) with sub-branches. Branches marked `strong: true` rendered in accent (orange) with bold text.
- Effect box (ink bg) on the right tip.

#### 5-Why
- Numbered chain (1–5). Each step:
  - 40px circle with number (or flame icon if root) — ink bg, white text; accent bg if root.
  - Card with `Why #N` label, question (bold), answer (paragraph), confidence bar (paper-3 track, ink fill).
  - Connector line between steps (paper-3, accent if leading to root).
  - Root step uses accent-soft bg + accent border.
- **Corrective actions** section: 4 rows (Containment / Corrective / Preventive × 2) with action chip (toned by kind), title, owner avatar, due date (mono), `···` button.

#### Report
**Critical: this is the FINAL artifact. Versioned. Multiple versions can exist; latest supersedes earlier.**

- **Version selector strip (ink bg, dark)**:
  - File icon (accent)
  - "Final report"
  - Inline version pills: `v1 · superseded`, `v2 · superseded`, `v3 · current` (active = orange filled; inactive = ink-4 border, dark-d text). Clicking switches `selectedV` state.
  - Timestamp + author (mono, dark-d2)
  - Right: `Export PDF` (ghost on dark) + `Generate new version` (primary)
- **Superseded notice** (if `!isCurrent`): paper-2 bg, paper-d2 left-border, clock icon + "Viewing v2 — superseded by v3. Read-only." + `Go to current` button.
- **Report body** (white card, padding 32/40):
  - If superseded, body opacity 0.85 + diagonal "SUPERSEDED" stamp top-right (transform: rotate(-6deg), border 2px paper-d2, mono 13 bold uppercase, letter-spacing 0.18em).
  - Report header: caps label `RCA REPORT · INC-... · vN`, H2 title + dot accent, meta row (Owner / Severity / Generated / status chip). `Submit for review` button only on current.
  - **What changed in vN** callout (accent-soft + accent left border).
  - 8 sections (D1–D8) — caps label + paragraph. D2 and D4 are `emphasize: true` (accent-soft bg + accent left border, padded).
  - Footer: "generated by RCA 3.0 · {author}" + version stamp (mono).
- **Version history** (below the report body): rows for each version — `vN` mono + status chip (current/superseded) + summary text + timestamp/author + chevron right. Selected version row gets accent border. Click → switches selectedV.

### 4. New Investigation modal

Triggered by `+ New investigation` in Home sidebar.

- **Backdrop**: rgba(20,22,28,0.55) + 4px backdrop-blur.
- **Modal**: 620px wide, paper bg, radius 12, border paper-3, max-height 90%.
- **Header**: caps label "New investigation" + H2 "Start an RCA" + close ×.
- **Body** (scroll):
  - **Title** (required, focused state with 1.5px accent border)
  - 2×3 grid: Severity (segmented Picker) / Status (Picker) / Production line (Select) / Product / Lot (mono) / Owner (avatar + name)
  - **Template** picker: 3 cards (`Solder defect` active by default · `Yield drop` · `Blank`) — active has accent border + accent-soft bg + accent-h text.
  - **Initial brief**: 90px min-height textarea-like card.
  - **Auto-agent ribbon**: accent-soft bg + sparkle icon + checkbox — "Agent will start the first 3 plan steps automatically once created."
- **Footer** (paper-2 bg, border-top): Cancel + `Create & ask agent` (primary, sparkle icon).
- Click `Create` → close modal + navigate to the new investigation.

---

## State & data model

```ts
type Investigation = {
  id: string;            // "INC-2026-0142"
  title: string;
  summary: string;
  severity: "P0" | "P1" | "P2" | "P3" | "P4";
  status: "draft" | "triaging" | "awaiting review" | "resolved" | "abandoned";
  line: string;
  product: string;
  lot: string;            // mono
  owner: { name: string; initials: string };
  members: string[];      // initials
  updated: string;        // human-readable
  agent: "running" | "idle";
  reportV?: string;       // "v3" — present iff a report has been generated
  reportProgress?: { drafted: number; total: number };  // legacy in-flight indicator (not used post-v3)
  pinned?: boolean;
};

type ReportVersion = {
  v: number;
  current: boolean;       // exactly one current per investigation
  ts: string;             // "08-16 17:42"
  author: string;         // "agent + Alice"
  summary: string;        // "What changed in vN"
  sections?: Section[];   // only fully present on current version in this prototype
};
```

**Status flow**: `draft → triaging → awaiting review → resolved` (happy path) or `→ abandoned` (terminal failure — investigation closed without root cause).

**Report rule**: a `resolved` investigation must have at least one `ReportVersion`. `abandoned` must NOT have one. `awaiting review` typically has one being reviewed (the current one).

---

## Interactions & behavior

### Routing
Two routes in the prototype:
- `home` — Home view.
- `investigation` (with id) — Investigation workspace.

Recommend a real router (Next.js / TanStack Router / RR) with `/investigations` and `/investigations/[id]` plus `?tab=brief|spc|pareto|fishbone|fivewhy|report`.

### Home → Investigation
Click any row → navigate to that investigation's workspace.

### Investigation tab switching
- File tab strip: click any tab → updates `view` state.
- Sidebar file rows (in `analyses` folder): clicking matches a known view → updates `view` state.
- Agent suggestion chips: clicking → adds user msg + agent reply + may call `onView(targetView)`.

### Report banner
Visible on all non-report views. Click `Open` → switches to report view.

### Agent suggestion chips
Map by current view:
```
brief    → [Show SPC analysis, Run Pareto, Sketch a fishbone]
spc      → [Run Pareto, Sketch a fishbone, Draft 5-Why]
pareto   → [Sketch a fishbone, Draft 5-Why, Draft report]
fishbone → [Draft 5-Why, Draft report, Re-check correlations]
fivewhy  → [Draft report, Propose containment, Add preventive action]
report   → [Submit for review, Export PDF, Open new investigation]
```

On click:
1. Append user message to conversation.
2. Show `pending` state (700ms) with "thinking" agent bubble.
3. Append canned agent reply.
4. If chip has a target view, call `onView(target)`.

### Report version actions
- Switch versions by clicking pill or version-history row.
- `Generate new version` → appends new version with `current: true`; previous current flips to `superseded`. (Prototype currently shows static data.)
- `Submit for review` shows only on `current` version.

### Animations & transitions
Minimal. Tab/view changes are instant. The accent pulse, progress bars, and chips are static in the prototype but should animate subtly:
- Status chip color changes: 150ms ease
- Tab underline: 120ms ease
- Modal open: 180ms ease (fade + 4px y-translate)
- Backdrop blur: 200ms

---

## Iconography

Custom 24×24 line icons defined in `system.jsx` (`I` component). All strokes 1.6px, round caps/joins. Names: `search · plus · minus · x · chev_d · chev_r · chev_l · folder · file · chat · play · term · user · users · settings · bell · grid · table · chart · pareto · fishbone · spc · photo · bug · branch · lock · globe · sparkle · arrow_r/u/d · git · star · dots_h/v · eye · pin · clock · check · split · layers · download · upload · filter · tag · flame`.

You can swap to Lucide / Heroicons (1.5px / 1.75px stroke equivalents) — just keep the visual weight light.

---

## Brand assets

In `assets/`:
- `rca-mark.svg` — primary mark (dark stroke #1A1B1F + accent dot #F0502E). Use this on light backgrounds.
- `rca-mark-light.svg` — light stroke variant for dark backgrounds.
- `rca-logo-horizontal.svg` — full horizontal lockup with "RCA · 3.0 · ANALYSIS · AI · AGENT".
- `favicon.ico`.

Use the SVGs directly — do not redraw. The mark must always include the orange dot at the apex.

---

## Notes for implementation

1. **State is light**. There's no persistent backend in the prototype. The real implementation needs:
   - Investigations API (list, get, create, update status, add member)
   - Reports API (versions: list, generate-new, mark-current, submit-for-review)
   - Agent runs API (start, stream, list tool calls)
   - Auth + org membership (all org members can view all investigations — visibility is org-wide).

2. **Streaming agent responses**. The prototype uses canned 700ms delays. In production this should be a proper streamed SSE/WebSocket pipe so plan steps and tool calls appear progressively.

3. **Tool calls**. The agent's tool surface in the prototype includes `spc.read`, `defects.aoi`, `correlate.find`, `pareto.build`, `fishbone.draft`, `5why.draft`, `report.generate`. Map these to your backend tool inventory.

4. **Responsive**. The prototype is desktop-only (1440 design width). Mobile/tablet not designed yet. If needed, the Home table → cards, and the Investigation workspace likely needs a different shape (collapsible sidebar, agent in bottom sheet).

5. **Don't reach for emoji** in the production UI. The prototype uses a few inside folder labels for legibility (`📁 data`) — replace with the `folder` icon component.

6. **Match copy**. The strings — INC IDs, status labels, agent messages, button text — are the design's voice. Don't rephrase casually.

---

## File index

- `RCA 3.0 prototype.html` — prototype entrypoint
- `index.html` — design canvas with the design system page + all screens shown isolated (reference for tokens / spacing)
- `design-canvas.jsx` — design canvas tool (not part of the product)
- `rca/system.jsx` — tokens + atomic components
- `rca/home.jsx` — Home view
- `rca/investigation.jsx` — Investigation workspace shell
- `rca/views/analyses.jsx` — Pareto / Fishbone / 5-Why / Report / NewInvestigation modal
- `rca/app.jsx` — top-level router/state machine
- `rca/design-system-page.jsx` — the design system showcase page
- `assets/*.svg` — brand mark + favicon
