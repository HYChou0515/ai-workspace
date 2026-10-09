# Plan — WUI multi-page (MPA): a folder with many pages, e.g. a mkdocs site

## Problem

A WUI is **one page**. `web/src/renderers/wui/assemble.ts` takes ONE entry
document, inlines every sibling it references (scripts, stylesheets, images,
fonts) and hands the result to an iframe as `srcDoc`
(`WuiView.tsx`, `sandbox="allow-scripts"`, null origin, `WUI_CSP` =
`default-src 'none'` …). That fits a single-page app — one HTML, the JS swaps
views — which is what a Vite/React build produces.

Most of the static web is not that shape. A static-site generator (mkdocs,
Sphinx, TypeDoc …) emits a **multi-page site**: one HTML file per page, joined
by relative links (`../setup/`), with JavaScript that reads sibling files at
runtime (mkdocs' search `fetch`es `search/search_index.json` and runs lunr in a
Web Worker). The output is pure front-end — exactly what a WUI is meant to
host — but inside today's envelope, read from the code (NOT yet measured — that
is Phase 1):

- **a link to another page goes nowhere.** Nothing assembles the target; the
  click is a self-navigation of a `srcDoc` frame, which `SPA_CSP`'s
  `frame-src 'self' blob: data:` (`api/spa.py`) exists to constrain;
- **runtime reads of sibling files fail.** `default-src 'none'` with no
  `connect-src` refuses `fetch` / XHR — so search returns nothing;
- **workers do not start.** No `worker-src` / `child-src`, so the fallback is
  `script-src 'unsafe-inline'`, which admits no worker URL;
- **links out are dead.** No `allow-popups`, and self-navigation is closed.

The goal is **MPA support for every WUI**. A docs site is the first acceptance
case, not a mode: the platform cannot tell a docs folder from any other WUI
folder, and should not try.

## Locked decisions (from /grill-me, 2026-10-09)

User-confirmed:

| # | Decision |
|---|---|
| D1 | **A WUI may be a multi-page site.** What it hosts is a real generator's OUTPUT (mkdocs, Sphinx, hand-written HTML …); the platform does not learn markdown. A platform-rendered markdown site (a `docs.ai.yaml` view) was the rejected alternative: re-implementing a docs site that loses to mkdocs. |
| D2 | **Same permissions as every WUI.** It IS a WUI — `view: wui`, `entry:`, Deploy, the overview, unlisting, the reader page, the bridge. No second, wider read scope (`plan-wui.md` decision 1 and the Deploy section stand). |
| D3 | **The platform bridges one-page → many-page**, so an existing `mkdocs.yml` runs unchanged. Rejected: requiring authors to switch on Material's `offline` plugin / `use_directory_urls: false` — less platform work, but it helps one generator only, and an unconverted site fails silently, the failure `wui.md` exists to avoid. |
| D4 | **Links out open behind a platform-drawn confirmation.** The dialog is drawn OUTSIDE the frame (as `openLogin`'s is), shows the domain large and the full URL beneath, and opens a new tab only on the reader's press. **Applies to every WUI** — it cannot be docs-only. Accepted residual risk: a reader who presses Open without reading lets a hostile page carry data out in the URL; the gate is the reader's attention, not a guarantee like `openLogin`'s "credentials never pass the page". |

Converged by me, not argued (say so to change):

| # | Decision |
|---|---|
| D5 | **Build stays `package.json` `scripts.build`.** A mkdocs page writes `"build": "uvx --with mkdocs-material mkdocs build"`: `.venv` and the per-sandbox `.home` do not survive a recycle, and `uvx` re-grows the environment the way `pnpm install` re-grows `node_modules`. **Unverified: that `uv` is on the sandbox's PATH** — Phase 1 checks it. |
| D6 | **Still no network at runtime.** A `fetch` / XHR for a file INSIDE the page's folder is answered by the platform (read through the same `load` the assembler uses); anything else is refused as today. |
| D7 | **A folder-relative link switches page.** `setup/` resolves to `setup/index.html`; `#anchor` scrolls as usual; `page.html#x` switches then scrolls. |
| D8 | **The address follows the page.** The reader URL names the sub-page — shareable, Back/Forward work; a workspace reload stays on the sub-page. |
| D9 | **Workers may start**, still under the page's CSP — no network inside a worker either. |
| D10 | **A relative link that leaves the site folder but stays in the item**: in the workspace, `openFile`; on the reader page, one sentence saying the link points at a workspace file. |
| D11 | **`sample-skills/wui/examples/docs/`** — a copyable mkdocs page, so the AI has something to copy. |
| D12 | **Deploy still checks only the entry page opens.** Other pages are read when someone clicks through; a broken one shows in that page's error panel. Crawling every page on Deploy is not done. |
| D13 | **JS memory does not survive a page switch** — that is what an MPA is. State that must persist goes to files (`writeFile`), the existing WUI rule; no cross-page scratch store. |
| D14 | **Not doing:** indexing a site for the KB / the AI (separate); any read scope wider than the item. |

## Acceptance

1. **This repo's own `docs/`, `mkdocs.yml` unchanged**, built in the sandbox and
   opened as a WUI: page-to-page navigation, search returns results, the
   dark-mode toggle works, a sub-page URL can be shared and reopened.
2. **A hand-written two-page WUI** — `index.html` ↔ `report.html` by `<a href>`,
   one page reading `./data.json` with `fetch` — works in the workspace and on
   the reader page.
3. **A link out** shows the platform dialog; Cancel opens nothing; Open opens a
   new tab.

## Phases

Phase numbering is flat (`CLAUDE.md`). Every phase is red-first (`/tdd`).

- **Phase 1 — measure, don't infer.** Build this repo's `docs/` (and the
  two-page hand-written WUI) into a WUI folder; open it in a real browser
  through the real entry point; record what works and what breaks, per item of
  D6–D9 and D4. Check `uv` in the sandbox (D5). The record goes in this plan;
  every later phase starts from a failing test that reproduces one recorded
  break. The problem list above is a prediction until this lands.
- Later phases are cut from Phase 1's record (navigation, sibling reads,
  workers, links out, the address, the example + `wui.md`), one commit each.

`docs/migrations.md` gets an entry in the implementing PR: D4 changes behaviour
for every existing WUI with no knob (a link out goes from dead to a dialog).

## Verified ground truth (origin/master `958e011e`)

- `web/src/renderers/wui/assemble.ts` — one entry, siblings inlined; `WUI_CSP`
  has no `connect-src` / `worker-src` / `child-src`; `form-action 'none'`,
  `base-uri 'none'`.
- `web/src/renderers/wui/WuiView.tsx` — `<iframe sandbox="allow-scripts"
  srcDoc={built.data.doc}>`.
- `src/workspace_app/api/spa.py` — `SPA_CSP = "frame-src 'self' blob: data:"`.
- `src/workspace_app/api/wui_routes.py` — the build runs
  `pnpm install … && pnpm run build` in the page's folder.
- `mkdocs.yml` — Material theme, `search.suggest` / `search.highlight`,
  directory URLs (the default).
