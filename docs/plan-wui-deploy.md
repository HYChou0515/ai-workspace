# Plan — WUI "Deploy": a URL that lands on the page

## Problem

A WUI already has its own address — `/w/{slug}/{itemId}/{path to the view
file}` (`web/src/App.tsx:46`, WUI P17, shipped in PR #788) — and the decision
that made it small is written on the route: **the URL is a shortcut, not a
grant**; whoever opens it must already be able to see the item.

Nobody can find it. `git grep '/w/' web/src` matches two comments and the
`WuiPage` docstring; **no component builds or shows the link**, and
`docs/wui.md` never mentions the address. From the author's chair the feature
does not exist — the "merged but invisible" shape.

Worse, what the address opens is wrong for its reader. `WuiPage` renders the
full `WuiView`, whose toolbar is unconditional (only the Rebuild block is gated,
on `canBuild`), so a colleague who followed the link is handed **Rebuild,
Auto-rebuild, Pick and Tell the agent** — with no agent to tell — and opening
the page fires the auto-rebuild effect (`WuiView.tsx` ≈451, keyed on
`[canBuild, autoBuild, slug, folder]`), spinning a sandbox up for someone who
only came to look.

## Locked decisions (from /grill-me, 2026-09-12)

| Question | Decision |
|---|---|
| Does "deploy" snapshot the page? | **No.** The link always shows the folder's current built output. A frozen copy is a second file tree (storage, quota, GC, "which version is this" UI, a re-publish action) for a need not yet shown; the free equivalent is a folder-level convention — copy `pages/report/` to `pages/report-v2/` and edit that. |
| Where is the action? | **`WuiView`'s workspace toolbar only**, beside Rebuild / Auto-rebuild / Pick / Tell the agent. Not the file tree — two entrances for one action drift apart. |
| Does the standalone page (`/w/`) carry a toolbar? | **No toolbar at all.** A reader has nothing to rebuild, nobody to tell, nothing to pick. (This also fixes the P17 defect above.) |
| Does opening the link build? | **Never.** The reader's path serves what is already built; the only failure it can show is "not published yet". `docs/wui.md:133` already accepts "source changed, `dist/` stale" as this road's one silent failure. |
| Do tool calls / workflow runs still work on the deployed page? | **Yes — that is the point of a WUI** ("engineers publish capabilities, domain people compose them"). The sandbox comes up lazily on the first tool press, as it does everywhere; plain viewing never touches it. |
| What does the Deploy button do? | **Rebuild, then hand over the link.** The publisher builds; the reader does not. The address appears only after a successful build, so what it points at is fresh by construction — no "did you rebuild?" reminder needed. A page with nothing to build (`canBuild` false) shows the link at once. |
| Name? | **Deploy.** Simple underneath, but the experience says "I shipped this". Honest because it *does* build. |
| Keep the Rebuild button? | **Yes.** Rebuild = build and look here (iterating); Deploy = build and give me the link (shipping). Same `runBuild`, two intents. |

## Design

### Viewer chrome — a prop, not a context

```ts
export function WuiView({ path, spec, chrome = "workspace" }: {
  path: string;
  spec: ViewSpec;
  /** `viewer`: the standalone `/w/` page — no toolbar, no auto-build. */
  chrome?: "workspace" | "viewer";
})
```

`WuiPage` passes `chrome="viewer"`. An explicit prop, deliberately **not** a
context: `WuiPage`'s own comment records what a missing provider does here
(auto-rebuild silently never fires, `callTool` is silently null) — the same
shape must not be built a second time. Nor a second component: the build /
iframe / postMessage plumbing has to exist exactly once.

In `viewer`:

- the toolbar is not rendered — none of Rebuild, Auto-rebuild, Pick, Tell the
  agent, the build-log toggle, Reports;
- the auto-build effect is not registered (early return on `chrome`, before
  the `autoBuild` preference is consulted);
- `buildWuiDoc(fs, folder, entry)` is still the ONE read that produces the
  page — it is already a pure file read, no sandbox;
- a missing `entry` (a buildable page nobody has built) renders `WuiPage`'s
  `Problem` sentence: *"This page has not been published yet."* — not a blank
  frame, which reads as a broken page rather than an unpublished one;
- the postMessage bridge (`callTool`, `itemRun`) is untouched — `WuiPage`
  already provides `WorkspaceSlugProvider`, keep it.

### Deploy — build, then the link

A `Deploy` button in the workspace toolbar. On press:

1. `canBuild` → `runBuild()` (the existing one); the button reads `Deploying…`
   and is disabled through the existing `building` state.
2. Success (or `canBuild` false) → a panel under the toolbar:

   ```
   ✓ Deployed
   https://…/w/{slug}/{itemId}{path}          [Copy]  [Open]
   Anyone who can open this item can use this link.
   ```

   The URL is `${window.location.origin}/w/${slug}/${fs.scopeId}${path}` —
   `slug` from `useWorkspaceSlug()`, `fs.scopeId` is the item id, `path` is
   the view file's path (the route's `*`). All three are already in
   `WuiView`'s hands; the string must match the `App.tsx:46` contract and a
   test pins it verbatim.
3. Build failure → no "Deployed" line; the panel says *"Deploy failed — see
   the build output"* and the existing log opens. The reader never sees this
   state; it is the publisher's.

`Copy` uses the clipboard API; where that fails (non-secure context) the URL
sits in a selectable read-only field instead — never silent. `Open` is a plain
`target="_blank"` anchor to the same URL.

### Permission

Unchanged from P17: the API refuses on the deployed page exactly what it would
refuse in the workspace. The panel's last line is that decision spoken to the
publisher, so nobody hands the link to someone outside the item expecting it
to work.

## Deliberately not doing

- **No snapshot / version / re-publish.** See the first locked decision.
- **No build on the reader's path**, and no "stale `dist/`" detection (mtime
  comparison) — Deploy building first makes the link fresh at the moment it
  is handed over; anything after that is the author editing a live page,
  which `docs/wui.md:133` already documents.
- **No file-tree context-menu entry.** One entrance.
- **No hiding of tool buttons for a read-only viewer.** The `caps` ≠
  `canWrite` gap (#698 notes) exists in the workspace today too and is not
  this plan's to close.

## Phases (one commit each)

1. **Viewer chrome** — `chrome` prop on `WuiView`; `WuiPage` passes `viewer`;
   toolbar gone, auto-build effect not registered, missing-entry sentence.
   Fixes the P17 leak of Pick / Tell the agent to readers as a side effect.
2. **Deploy** — the toolbar button, `runBuild` gating, the panel, the URL,
   Copy with fallback, Open, the failure branch.
3. **Docs** — `docs/wui.md` gains a "發布（Deploy）" section beside
   「誰負責重建」: the `/w/` address and its contract, "the link is a shortcut,
   not a grant", Deploy = rebuild + link, readers never build. The address is
   currently absent from the document entirely.

## Test plan (red first, targeted only)

Phase 1 (`WuiPage.test.tsx`, `WuiView.test.tsx`):

- Rendering `WuiPage` (viewer) shows **none** of Rebuild / Auto-rebuild /
  Pick / Tell the agent / build-output toggle / Deploy — assert each by role
  or label, not "toolbar absent" (one missing assertion is a leak).
- The auto-build effect does not fire in `viewer` even with the preference on
  — a build spy stays at zero; positive control: same fixture with
  `chrome="workspace"` fires it.
- The tool bridge still answers in `viewer`: a `callTool` postMessage reaches
  the seam.
- Missing `entry` in `viewer` renders the not-published sentence; the same
  state in `workspace` keeps today's behaviour.

Phase 2 (`WuiView.test.tsx`):

- Pressing Deploy on a buildable page calls `runBuild` **before** any panel
  appears; on a non-buildable page the panel appears with no build call.
- The panel URL equals `${origin}/w/${slug}/${itemId}${path}` verbatim, for a
  path with a nested folder.
- Build failure: no "Deployed" text, failure sentence present, log opened.
- Copy: clipboard called with the URL; with the clipboard rejecting, the
  read-only field carries the URL.
- Button disabled and labelled `Deploying…` while `building`.

Phase 3: `mkdocs build --strict` stays green (plan-* is `not_in_nav`; the
`wui.md` edit is a section, not a nav change).

## Verified ground truth (file pointers, origin/master `d844a651`)

- Route: `web/src/App.tsx:46` — `/w/:slug/:itemId/*` → `WuiPage`, outside
  `GlobalLayout`; brought in by PR #788 (WUI P17).
- `web/src/pages/WuiPage.tsx` — reads the view file, `parseViewSpec`, refuses
  non-`wui` kinds with a `Problem` sentence, renders `<WuiView path spec />`
  inside `WorkspaceSlugProvider` + `FileServiceProvider`. Its comment names
  what a missing slug provider does silently.
- `web/src/renderers/wui/WuiView.tsx` — `WuiView({ path, spec })`, no chrome
  prop; toolbar unconditional except the `canBuild` block and
  `reports.length`; `runBuild` ≈370; `building` state; auto-build effect
  ≈451–456 via `useWuiAutoBuild(autoBuildScope(fs.scopeId, folder))`; has
  `slug` (`useWorkspaceSlug`), `fs.scopeId`, `path`.
- `buildWuiDoc(fs, folder, entry)` is the page read; `DEFAULT_ENTRY =
  "index.html"`; buildable pages set `entry: dist/index.html`
  (`docs/wui.md:94,111`).
- `docs/wui.md:133` — "src changed, dist not rebuilt → page silently stays on
  the old version" is already the documented trade-off.
- Link producers: `git grep -n '"/w/\|`/w/' web/src` → only
  `GlobalLayout.tsx:40` (comment) and the `WuiPage` docstring.
- `mkdocs.yml:134` — `not_in_nav` covers `/plan-*.md`.
