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

## Phase 1 record (2026-10-09, measured)

Chromium 1223 (playwright-core), the app from this branch on `127.0.0.1:8241`,
the reader route `/w/playground/<item>/…`. Two folders: this repo's `docs/`
built with `uvx --with mkdocs-material mkdocs build` (266 files, 24 MB,
`search/search_index.json` alone 6 MB) under `entry: site/index.html`, and a
hand-written two-page folder (`index.html` ↔ `report.html`, `fetch("./data.json")`).

| What | Result |
|---|---|
| mkdocs home renders, styled | works (header colour applied) |
| click a nav link | the frame navigates to **the platform's own SPA** at `/w/…/docs/architecture/` (a srcdoc document's base URL is the PARENT's), which then fails to load its scripts (CORS from `null`) — a broken app inside the frame |
| `#anchor` link | **the same** — resolves against the parent's URL, so the frame navigates away. A pre-existing defect of EVERY WUI, not only multi-page ones |
| `fetch("./data.json")` | refused by `default-src 'none'` |
| a link out | `frame-src` refuses it and the frame becomes Chrome's error page |
| Material's JavaScript | **dead on its first line**: `Failed to construct 'URL': Invalid URL` (`new URL(".", location)` with `location` = `about:srcdoc`), then `__md_get is not defined`; search, palette toggle and code-copy never start |
| `uv` in the sandbox (D5) | present: `sandbox-host/Dockerfile` copies `/uv` `/uvx` into `/usr/local/bin` |

**The finding that changes the mechanism:** a document in `srcdoc` has
`location` = `about:srcdoc`, which is not hierarchical, so every script that
derives a URL from `location` throws. Shimming `fetch`/clicks from the outside
cannot reach that. A page must be loaded from a real `http(s)` URL for an
unchanged generator site to run.

Second measurement, a throwaway server (`Sec-Fetch-Site` + `Cookie` logged): a
frame with `sandbox="allow-scripts"` (no `allow-same-origin`) sends **no
`SameSite=Lax` cookie** on any request it makes — scripts, stylesheets, images,
`fetch` (even `credentials: "include"`), and its own link navigations are all
`cross-site`. Only the parent-initiated load of the frame's first document
carried the cookie. A third: in such a frame served with
`Content-Security-Policy: sandbox allow-scripts; … 'self' …`, `'self'` matches
the URL's origin (own script + `fetch` load, `https://example.com` refused),
`localStorage` throws `SecurityError`, `new Worker("w.js")` is refused
("cannot be accessed from origin 'null'") but a `blob:` worker under
`worker-src blob:` starts, and inside it `importScripts` / `fetch` of ABSOLUTE
URLs work.

## Revised mechanism (supersedes the "how" of D6, D7, D9)

The decisions D1–D14 stand; how D6/D7/D9 are delivered changes, and D8 gets
simpler. Decided by me after Phase 1 while the user was away — **the user may
overturn it**; it is the cheapest of the three routes that meets D3 (the others:
a separate content origin — the GitHub/Google "usercontent" shape, strongest
isolation, a new hostname + TLS + gateway route; or keeping `srcdoc` and
rewriting every inline script inside `with (fakeScope)` to forge `location` —
breaks top-level `let`/`const` sharing across scripts, cannot reach
`window.location` or modules, no mature product does it).

- **A capability URL on the same host.** The pane asks
  `POST /api/a/{slug}/items/{item}/wui/pass {folder}` (gated `read_content`,
  cookie-authenticated like every route) and gets a random, unguessable token
  stored as a specstar row (`WuiPass`: slug, item, folder, user, expiry — shared
  across pods). The frame then loads
  `/api/wui-content/{token}/{workspace path}`. That route needs **no cookie** —
  the measurement above says the frame cannot send one — and authorises by the
  token: the row must exist and be unexpired, the path must be inside the
  pass's folder, and the item is re-checked for `read_content` **for the pass's
  user** on every request, so revoking access takes effect within the access
  window rather than at expiry.
- **Every response carries the envelope as a header**:
  `Content-Security-Policy: sandbox allow-scripts; default-src 'none';
  script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; font-src 'self' data:; media-src 'self' data:;
  connect-src 'self'; worker-src blob:; form-action 'none'; base-uri 'self'`.
  `sandbox` makes the document opaque-origin even if someone opens the URL as a
  top-level page; `'self'` keeps it to this host; the CSP is the server's, so a
  page's own `<meta>` can only tighten it. `Access-Control-Allow-Origin: *`
  because an opaque origin's `fetch` is cross-origin — the token, not the
  origin, is the credential.
- **The runtime is injected by the server** as the first element of every HTML
  response's `<head>`. Its source stays `runtime.ts`; the Vite build emits it as
  `web/dist/wui-runtime.js` (`web/vite-plugins/wuiRuntime.ts`), and the route
  reads that file. Without it the route serves no page (`503`), and the probe
  below sends the pane to the fallback.
- **Navigation, fragments, relative `fetch`, Material's `location` arithmetic —
  all native** now; nothing to intercept. The runtime adds only what the browser
  will not do in an opaque origin: a `Worker` that loads its script into a
  `blob:` (with the script's real URL as the base for its own `importScripts` /
  `fetch`), an in-memory `localStorage`/`sessionStorage` (so a page's storage
  call stops throwing — nothing persists, which is what a page in an opaque
  origin gets anyway), and an announcement of each page it lands on.
- **D4 links out**: the runtime catches a click on a link whose resolved URL is
  on another origin (and `window.open`), cancels it, and asks the parent; the
  parent draws the dialog and opens the URL with `noopener,noreferrer`.
- **D10**: a same-origin link that leaves the pass's folder is caught the same
  way and becomes `openFile` (the existing verb) in the workspace, a sentence on
  the reader page.
- **D8**: each page announces itself; the pane keeps the sub-path. The reader
  page writes it to `?page=` with `history.replaceState` (the frame's own
  navigation already made the history entry, so Back/Forward are the browser's);
  the workspace pane keeps it in `sessionStorage`, keyed by the view file, so a
  reload stays on the page.
- **Fallback, decided by measurement rather than config.** Before pointing the
  frame at the URL, the pane fetches `…/{token}/__wui/ping` **with
  `credentials: "omit"`** — the same cookie-less request the frame will make.
  Anything but the expected answer (a gateway's login redirect, an old server, a
  missing runtime) and the pane renders the folder the old way (`srcdoc`,
  single page, unchanged code) and tells the author, once, that multi-page
  needs the operator to let `/api/wui-content/` through. The runtime also fixes
  the `#anchor` defect for the fallback (it scrolls instead of navigating).
- **Residual risks, accepted:** a token in a URL can leak (through D4, a reader
  who opens a URL carrying it) and then reads that folder until expiry, only
  while the minting user can still read the item; the frame may send
  unauthenticated requests to its own host (no cookie travels with them).

## Phases

Phase numbering is flat (`CLAUDE.md`). Every phase is red-first (`/tdd`).

- **Phase 1 — measure, don't infer.** Done — the record above.
- **Phase 2 — the content route.** `WuiPass` store, the mint route, the serving
  route (folder bound, directory → `index.html`, a slash-less directory
  redirected, CSP + ACAO headers, runtime injection, expiry, per-request access
  re-check), `__wui/ping`.
- **Phase 3 — the runtime.** Emitted as a file by the build; per-page request
  ids (a late answer from the previous page must not resolve the next page's
  call); page announcement; links out and out-of-folder links; `Worker` and
  storage shims; `#anchor` in the fallback.
- **Phase 4 — the pane.** Mint + probe, frame `src` vs `srcDoc` fallback,
  sub-page tracking (reader `?page=`, workspace `sessionStorage`), the D4
  dialog, Refresh reloads the frame.
- **Phase 5 — what people read.** `docs/wui.md`, the `wui` skill + an
  `examples/docs/` mkdocs page, `docs/migrations.md` (D4 changes every WUI with
  no knob; the gateway note).

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
