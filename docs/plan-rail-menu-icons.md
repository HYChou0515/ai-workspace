# Plan — the chat rail's ☰ menu draws the same icons the global switcher does

Reported 2026-09-19 with a screenshot of a chat-first App's left rail menu:
APPS (Playground, Project Management, Root Cause Analysis, Topic Hub), a rule,
then Knowledge base / 審核 / Diagnostics / 我的資源 / WUI / Skill hub / Help —
"他們全部沒有 icon".

## Problem

There are two menus that list the same things — the global bar's app switcher
(`GlobalNav.tsx:Switcher`) and the chat rail's ☰ (`ChatListRail.tsx`). Their
**data** was unified once already (`hooks/usePlatformDestinations.ts`: the rail
had silently fallen behind on My resources / Groups / Work calendar), and every
entry carries an icon: an App has `icon` + `color` from its manifest
(`AppSummary`, `api/types.ts:188`), a platform destination has an `IconName`
(`layers`, `check`, `sparkle`, `external`, `users`, `clock`, `quote`).

Only the **rendering** was left unshared:

- `GlobalNav` draws `<AppIcon icon slug color size={22}>` before an App's title
  and a 22px box holding `<Icon name size={16}>` before a destination's label
  (`FixedLink`, `GlobalNav.tsx:62-80`).
- `ChatListRail` draws `{a.title}` and `{l.label}` — text only
  (`ChatListRail.tsx:177-199`), and `usePlatformDestinations.ts:32` records it as
  intent: "the rail's menu is text-only".

So the drift the shared hook was written to end has re-appeared one layer up:
the same list, two looks. The rail's `.chat-rail__menu-item` is not a flex row
(`styles/chat-rail.css:124-130`), so the icons cannot simply be dropped in.

(The screenshot also shows **Skill hub**, which is PR #818, not master. The fix
lands on master; #818 inherits it on merge.)

## Locked decisions

- **Both menus draw the same row.** One component owns "the glyph box before a
  menu label"; both menus use it. No second copy of the markup in the rail —
  that is how the data drifted, and it would drift the look the same way.
- **The glyph, not the link, is what is shared.** The two menus have different
  link semantics that existing tests pin — `GlobalNav`'s `MenuLink` carries
  `aria-current` inside a `Popover`; the rail's items are `role="menuitem"`
  inside `role="menu"` with a backdrop. Sharing the whole link would change one
  of those; sharing the glyph changes neither.
- **The rail's look otherwise stays**: same paddings, same font size, same
  hover, same order, Help still listed there (the rail has no "?" button).
- **`GlobalNav` does not change visually.** It switches to the shared glyph so
  there is one source; its existing tests are the pin that nothing moved.

## Design

### `web/src/components/NavGlyph.tsx` (new)

```tsx
/** The 22px glyph box that precedes a label in both platform menus. */
export function NavGlyph(props: { app: AppSummary } | { icon: IconName }) …
```

- `app` form → `<AppIcon icon={app.icon} slug={app.slug} color={app.color} size={22} />`
  (what `Switcher` does today).
- `icon` form → the 22px `inline-flex` box with `<Icon name size={16}
  color="var(--text-paper-d)" />` (what `FixedLink` does today, moved).

### `GlobalNav.tsx`

`Switcher` and `FixedLink` render `<NavGlyph …>` instead of their inline
markup. No other change.

### `ChatListRail.tsx`

Both `.map`s render `<NavGlyph …>` before the text. The ☰ menu's items become
a flex row — `.chat-rail__menu > .chat-rail__menu-item { display: flex;
align-items: center; gap: var(--space-8) }` — so the glyph and the label sit on
one line at every width. **Scoped to the ☰ menu on purpose** (P3): the ⋯ row
menu's Rename / Share / Delete buttons share `.chat-rail__menu-item`, sit in a
flex column and are centred by the UA button default; `display: flex` on the
bare class would have left-aligned them.

### `usePlatformDestinations.ts`

The `icon` doc comment loses "the rail's menu is text-only" — after this it is
a false sentence, and the file is the one both menus are told to read.

## Deliberately not doing

- No new icons, no icon changes. Every entry already has one; this only draws
  what is there.
- The rail's hardcoded "Apps" eyebrow (`chat-rail__menu-label`) is not
  i18n'd here — unrelated to the report, and the launcher's
  `launcher.appsEyebrow` key exists for a later sweep.
- Help stays filtered out of `GlobalNav` only (#230) — untouched.

## Phases (one commit each)

- **P1** `NavGlyph` + `GlobalNav` uses it. Red first: a `NavGlyph` test for both
  forms (App → `AppIcon`'s output; icon → `svg[data-icon=<name>]` inside a 22px
  box). `GlobalNav.test.tsx` must stay green unchanged — that is the "no visual
  change" pin.
- **P2** The rail renders `NavGlyph` in both sections + the flex CSS + the stale
  comment removed. Red first (see test plan).
- **P3** The flex row scoped to `.chat-rail__menu > …` — found by the regression
  lens on my own diff before the push (below).

## Test plan (red first, targeted only)

- `NavGlyph.test.tsx`: the `icon` form renders `[data-icon="layers"]`; the `app`
  form with a named icon renders `[data-icon="flame"]`, with a file icon renders
  `<img src=…/apps/<slug>/icon>` (the three `AppIcon` forms are `AppIcon`'s own
  tests; here one of each proves the props are forwarded, not re-derived).
- `ChatListRail.test.tsx` — new case *"every menu entry has its icon, in both
  sections"*: open the menu (`button /platform menu/i`), take
  `getAllByRole("menuitem")` inside `role="menu"`, assert **each** contains
  `[data-icon]` or `img`. The count is whatever the mocked `useApps` (2 apps,
  named icons) plus the destinations produce — derived from the DOM, not typed.
  Positive control: with the `NavGlyph` line removed from the rail this test
  reddens and the existing destinations test stays green (they measure
  different things).
- Mutation pins (file copy, restore): (1) drop `NavGlyph` from the apps `.map`
  only → the new test reddens on the first section; (2) drop it from the
  destinations `.map` only → reddens on the second; (3) revert the CSS flex →
  no unit test can see it, so the layout is checked live (below).
- Gate per change: the two test files + `pnpm run typecheck`; backend untouched
  so no ruff/ty.

## Live check (before the PR leaves draft)

Run the app in the worktree, open a chat-first App (Playground), press ☰, and
screenshot the menu at **1280px and 390px** — the rail tucks itself at narrow
widths and the menu must still read as one line per entry. Compare against the
global switcher on the same build: same glyphs, same order for the shared
section.

## Verified ground truth (origin/master `82d5c861`)

- Data source for both menus: `web/src/hooks/usePlatformDestinations.ts`
  (`PlatformDestination.icon: IconName`; entries `/kb layers`, `/review check`,
  `/diagnostics sparkle`, `/my-resources layers`, `/wui external`,
  `/groups users`*, `/work-calendar clock`*, `/help quote`; * gated).
- App entries: `useApps()` (`web/src/hooks/useResources.ts:18`) →
  `AppSummary { slug, title, icon, color, … }` (`web/src/api/types.ts:188-189`).
- Global switcher: `web/src/components/GlobalNav.tsx` — `MenuLink` (:31-60,
  `display:flex; gap:10`), `FixedLink` (:62-80, the 22px box + `Icon` 16),
  `Switcher` (:120-124 `AppIcon size={22}`; :133-140 `FixedLink`, Help filtered).
- Rail menu: `web/src/components/ChatListRail.tsx:172-199` — `role="menu"`,
  `Link role="menuitem" className="chat-rail__menu-item"`, text only in both
  sections. CSS `web/src/styles/chat-rail.css:124-130` — no `display`.
- `AppIcon` (`web/src/components/AppIcon.tsx`): file / emoji / named forms,
  `size` prop; `Icon` renders `<svg data-icon={name} aria-hidden>`
  (`Icon.tsx:320-326`).
- Existing tests: `GlobalNav.test.tsx` (mocks apps with `icon: "flame"` /
  `"bug"`), `ChatListRail.test.tsx:86-100` (opens the menu via
  `button /platform menu/i`, asserts destination hrefs), `AppIcon.test.tsx`.

## Live check (2026-09-19, worktree build on 127.0.0.1:8256, real Chromium)

Merged `origin/master` (`06131733`, Skill hub) first so the menu matches the
report's screenshot. A fresh Playground item, the rail's ☰ opened by
`button[name="Platform menu"]`; at 390 the rail was tucked and `Show scratches`
expanded it first. Per entry: the `[role=menuitem]`, its glyph
(`[data-icon]` / `img`), whether the glyph's vertical centre is within 4px of
the row's, and the row height.

| width | entries | with glyph | glyph on the label's line | row height |
|---|---|---|---|---|
| 1280 | 11 | 11 | 11 | 30px |
| 390 | 11 | 11 | 11 | 30px |

Glyphs, in order: sparkle, kanban, flame, flame · layers, check, sparkle,
layers, external, sparkle, quote — the same names the global switcher draws
for the entries it shares (probed on `/kb`: Playground sparkle, PM kanban, RCA
flame, Topic Hub flame, Knowledge base layers, WUI external). Screenshots
`rail-menu-1280.png`, `rail-390.png`, `switcher-1280.png` (job tmp; not
committed).

## Self-review before the push (conformance / veracity / regression on my own diff)

- Conformance: P1 (`NavGlyph`, both forms; `GlobalNav` uses it; its test file
  unchanged — the diff stat for it is empty) and P2 (both rail sections, flex
  CSS, the "text-only" sentence gone) match the Design section; the test plan's
  three `NavGlyph` cases and the per-item rail case exist; mutation pins (1)
  and (2) run — each reddens only the new test, naming that section's first
  entry (`RCA` / `Knowledge base`), the other 24 stay green.
- Veracity: every `file:line` in Verified ground truth was re-checked against
  the worktree (one corrected: `MenuLink` starts at :31). Commit claims about
  which test reddens on which mutation were produced by running them.
- Regression — **one finding, fixed as P3**: `.chat-rail__menu-item` is also
  the ⋯ row menu's button class. Probe on the built bundle: ☰ items compute
  `display: flex` (11/11); ⋯ buttons compute `block / center` — as on master.
  Before the scoping they would have computed `flex` and read left-aligned.

