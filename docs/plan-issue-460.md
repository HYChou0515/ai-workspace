# Plan — Issue #460: FE defects batch + #105 quality-score display gap

Flat integer phases (repo convention). Each phase: red → green → refactor via `/tdd`, its own vitest/unit test, committed separately. FE tests are vitest; any backend touch keeps the `ruff`/`ty`/`coverage` gate.

Confirmed scope (with the user):
- P1 fake git bar · P2 SEARCH overflow · P3 FILES sticky header · P4 About link + copy · P5 WIKI GUIDANCE placement · P6 permission preview · P7/P8 #105 quality detail.
- App-icon blank is **already fixed in source** (`6178e2c5` added the `kanban` glyph); the screenshot was a stale gitignored `web/dist`. No code phase — verify only.

---

## P1 — Remove the fake, hardcoded git status bar

**Where:** `web/src/pages/investigation/WorkspaceShell.tsx` — `StatusBar` (2615–2655); git region 2639–2642 (`main` literal @2640, `↑ 0 ↓ 0` @2642).

**Change:** delete the branch-icon `<span>` + the ahead/behind `<span>`. Keep the language label (real, from `pickRenderer`) and the notebook `KernelStatusPill` (real). Leave `UTF-8`/`default-user` for now (separate concern; out of scope).

**Test (vitest):** the `StatusBar` isn't exported. Extract it to a named export (or add `data-testid="ws-statusbar"` and render `WorkspaceShell` region) and assert the bar renders **no** `main` / `↑`/`↓` git text. Red first (text present) → green after removal.

## P2 — SEARCH side panel clips its own overflow

**Where:** `web/src/pages/investigation/SearchPanel.tsx` — `frame` (314–321), `fieldWrap` (331–339), `Toggle` (266–277) + the toggle group container (~143).

**Change:**
- `frame`: add `overflow: hidden` (match the other sidebar frames, `WorkspaceShell.tsx:1181–1191`).
- `fieldWrap`: add `minWidth: 0` so the row can shrink without pushing the toggles out.
- toggle-group container: add `flexShrink: 0` so `Aa`/`ab`/`.*` stay fully visible; the `input` already has `minWidth:0` (350) so it shrinks first.

**Test (vitest):** add `data-testid` to `frame` + toggle group; render `SearchPanel`; assert inline `style.overflow === "hidden"` on the frame and `flexShrink` on the toggle group (jsdom exposes React inline styles via `element.style`).

## P3 — FILES tree sticky header masks scrolled rows (no bleed-through, no gap)

**Where:** header `web/src/pages/investigation/FileTree.tsx:405–419` (`zIndex:1`); scroll container `web/src/styles/kb.css:2247–2259` (`padding:10px 0`).

**Change:**
- Move the scroll container's top padding off the pin line: `.kb-ide__tree` `padding: 10px 0` → `padding: 0 0 10px` (kills the 10px band above the sticky header where rows show through). Add matching top spacing inside the header instead if needed.
- Raise the header stacking: `zIndex` from `1` to a token/`3`, and ensure the header background is opaque and spans full width (it already sets `--filetree-header-bg`). Give the header its own stacking context reliability (e.g. keep `position:sticky` + opaque bg).
- Empty-gap: investigate the caps-gated action cluster (`476–567`); keep the action area width stable (reserve/space) so a missing capability button doesn't leave a jarring gap. Scope to a minimal, safe layout tweak; if the gap turns out to be in the tab-content container (not FileTree), note and defer.

**Test (vitest):** render `FileTree` with `searchable`; assert header has raised z-index + opaque bg via inline/computed style, and (structural) that the scroll container no longer carries top padding. jsdom can't test true paint overlap, so assert the style contract that prevents it.

## P4 — Settings › About: real link affordance + correct sign-in copy

**Where:** `web/src/components/GlobalSettings.tsx:82–102`; i18n `web/src/lib/i18n.tsx:37–40`; reset `web/src/styles/base.css:31–34`.

**Change:**
- Docs link (`GlobalSettings.tsx:96–100`): give the `<a>` a link style — reuse an existing link affordance (`color: var(--accent-h); text-decoration: underline`) via a shared class or inline style. Keep `href`/`target`/`rel`.
- Sign-in copy `about.signin.value` (`i18n.tsx:38`): replace `單人示範（免登入）` / `Single-user demo (no sign-in)` with copy that reflects reality (multi-user directory + identity resolved via the `get_user_id()` seam, SSO not yet wired). **Chosen default (overridable):** zh-TW `示範模式（尚未接 SSO）`, en `Demo mode (SSO not wired)`. Avoids internals; states the true auth state.

**Test (vitest):** extend `GlobalSettings.test.tsx` — assert the docs anchor carries the link class/style, and assert the new `about.signin.value` text (both locales via the i18n map).

## P5 — Move WIKI GUIDANCE out of the always-open inline slot

**Where:** `web/src/pages/kb/WikiBrowser.tsx` — `WikiGuidanceEditor` (45–155); ready-state mount (509–522); empty-state mount (498–503).

**Change:** wrap the guidance mount in a **collapsed-by-default disclosure** (accessible `<details>`/toggle with a "Wiki guidance" summary + gear affordance) so it's discoverable but doesn't dominate the column beneath the tree. Preserve save/dirty behavior when expanded. Apply to both mount points (or lift into one shared collapsible).

**Test (vitest):** render `WikiBrowser` ready-state; assert the guidance textareas are **not** in the document by default (collapsed), and appear after activating the disclosure; Save still wired.

## P6 — Share/permission preview reflects the selected visibility

**Where:** helper `web/src/lib/permission.ts:140–156`; dialog `web/src/components/PermissionDialog.tsx:151–155`; backend truth `src/workspace_app/perm/authorize.py:104–108`.

**Change:**
- Add a pure FE helper mirroring backend visibility semantics, e.g. `effectiveVerbSubjects(visibility, permission, grants)`:
  - `public` → every verb ⇒ `everyone` **except** `change_permission` (stays grant-list-only, per backend 100–103).
  - `private` → every verb ⇒ empty (`—`); owner-only is implicit.
  - `restricted` → the grant-derived lists (today's `permissionFromGrants` output).
- `PermissionDialog` advanced preview renders from this helper keyed on the selected `visibility` radio, not the raw `permissionFromGrants`. Display "everyone" for the all-subject.

**Tests:** (1) unit-test the pure helper for all three visibilities incl. the `change_permission` exception; (2) vitest on `PermissionDialog`: select Public ⇒ preview shows "everyone" (not alice/bob) for read/write verbs and grant-list for `change_permission`; Private ⇒ `—`; Restricted ⇒ named users.

## P7 — #105: wire quality detail to the IDE + show verdict label + expandable rationale

**Where:** FE `getSourceDocMeta` (`web/src/api/kb.ts:883–895`) + `KbDocMeta` type (308–311); `web/src/pages/kb/KbDocIde.tsx:439–451` (status quality area); `QualityBadge.tsx`; `web/src/styles/kb.css:1478–1484`. Backend `/source-doc/{id}` — **verify** `env.data` already carries `quality_breakdown` + `quality_score` (the raw SourceDoc does); extend the route projection only if it doesn't.

**Change:**
- Extend `getSourceDocMeta` to also read `quality_score` + `quality_breakdown` from `env.data`; add both to `KbDocMeta`.
- Show the good/ok/bad **verdict label** as visible text (not just color/tooltip) in the open-doc quality area (drive label from the `quality.ts` tone thresholds).
- Replace the 22-char hover-truncated rationale with a **click-to-expand** disclosure showing the full `quality_rationale` (remove the `max-width:22ch` clamp; expandable panel).

**Tests (vitest):** `getSourceDocMeta` maps score+breakdown; `KbDocIde` quality area renders the verdict word and expands the full rationale on click.

## P8 — #105: render the per-dimension breakdown detail scores

**Where:** the expanded quality panel in `KbDocIde.tsx` (from P7); new small presentational piece (e.g. `QualityBreakdown`).

**Change:** in the expanded quality panel, render `quality_breakdown` as a compact dimension→score list/bars (dimensions are user-rubric-named, dynamic `dict[str, number]`). Empty/absent ⇒ render nothing (no "0" noise). This is the headline user ask ("detail scores that should be added").

**Tests (vitest):** given a doc meta with a breakdown dict, the panel lists each dimension + its score; absent breakdown ⇒ no breakdown block.

---

## Verification (not a phase)

- App-icon: after `pnpm run build`, confirm the "Project Management" card shows the `kanban` glyph (source already fixed at `6178e2c5`). `web/dist` is gitignored — nothing to commit.

## Global DoD

- `cd web && pnpm run typecheck` + `pnpm run build` clean; vitest green.
- If backend touched (only if P7 needs a route change): `uv run ruff check && uv run ruff format --check && uv run ty check`, and the coverage gate.
- Commit per phase; push branch; open draft PR; wait CI green; merge.
- No hardcoded app-slug branching; UI copy avoids internals ([[feedback_ui_copy_no_internals]]).
