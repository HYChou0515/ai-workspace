You're the **frontend AI** for the RCA 3.0 project. The backend is already partially built and ongoing — your job is the React SPA per the design handoff.

## Read in this order

1. **`CLAUDE.md`** — project conventions. Notably: reply to me in **Traditional Taiwanese Chinese (繁體中文 / 台灣用語)**; English for code/commits.
2. **`docs/contract.md`** — the **single source of truth** for the FE↔BE wire (models, HTTP routes, SSE events, brand assets, file conventions). Don't deviate. If you need something not there, propose an update to this file (don't just hack around it).
3. **`docs/plan-frontend.md`** — your plan. §12 has the work order. §6 lists file-type renderers.
4. **`design_handoff_rca_3.0/README.md`** + the JSX files (`rca/system.jsx`, `home.jsx`, `investigation.jsx`, `views/analyses.jsx`) — the design you're recreating. **High-fidelity**: tokens, layout, spacing, copy are all intentional. The HTML/JSX prototype is a spec, not production code — recreate in our React+Vite+TS environment.

## What's in `web/` already

React 19 + Vite 6 + TypeScript 5 (strict) + pnpm + vitest are wired. Existing components (`Chat.tsx`, `WorkspaceList.tsx`, `FileBrowser.tsx`, `src/api/*`) are leftovers from the pre-pivot workspace-app — per plan-frontend §2, **scrap the layout, keep the toolchain + SSE consumption pattern + event types**. SSE generator + event type unions in `web/src/events.ts` should be reused/extended, not rewritten.

## Start here (no backend dependency)

Per plan-frontend §12, the first two steps have zero BE blockers:

1. **§12.1 Design tokens + brand assets**
   - Copy SVGs from `design_handoff_rca_3.0/assets/` → `web/public/` (don't redraw — the orange dot at the mark's apex is canonical brand)
   - Create `web/src/styles/tokens.css` with the colors / type scale / spacing / radii listed in `design_handoff_rca_3.0/README.md` § "Design tokens". CSS custom properties (`var(--accent)`, etc.). Load Inter Tight + Inter + JetBrains Mono.
2. **§12.2 Router + status chips**
   - `react-router-dom` v6 with `/` (Home) and `/investigations/:id` (Workspace).
   - `<SeverityChip level="P0".."P4"/>` and `<StatusChip status="triaging"|"awaiting_review"|"resolved"|"abandoned"/>` matching the design's tone mapping.

Anything after §12.2 needs the BE to finish renaming `Workspace` → `Investigation` (in progress) — but you can build layouts against mocks meanwhile.

## Rules

- **TDD via vitest**: write tests as you go in `src/**/*.test.ts(x)`. `pnpm run build` (= `tsc --noEmit && vite build`) must pass before any commit.
- **Don't touch backend** files (anything under `src/workspace_app/`, `tests/`, `pyproject.toml`, etc.). If you need a route/event the BE hasn't shipped, **add a row to `docs/contract.md`** with a `⏳` status and tell me — I'll route it to the BE agent.
- **`git add` specific files only**. Never `git add -A` / `git add .` — it sweeps up files from parallel work.
- **No new heavy deps without a clear reason**. Allowed adds when you reach them: `react-markdown` + `remark-gfm` (F10), `dompurify` (F9 HTML sanitization), `@monaco-editor/react` (F8 cell editor). Forbidden: `axios`, `redux`/`zustand`, UI kits, `styled-components`.
- **No emojis in production UI** (design explicit on this).
- Commit messages in English; reply to me in **繁體中文**.

Start by reading the four docs above and then doing §12.1. Show me your work when §12.1 is done before moving to §12.2.
