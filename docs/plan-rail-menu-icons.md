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
  there is one source. ~~Its existing tests are the pin that nothing moved.~~
  Round 1 showed they are not — they pin links and labels and stayed green
  with the switcher's glyphs deleted outright. What pins it now: `NavGlyph`'s
  own tests (every box property, sizes, colours) plus one per-row glyph case
  added to `GlobalNav.test.tsx` (P4); the existing cases are untouched.

## Design

### `web/src/components/NavGlyph.tsx` (new)

```tsx
/** The 22px glyph box that precedes a label in both platform menus. */
export function NavGlyph(props: { app: AppSummary } | { icon: IconName }) …
```

- Both forms render inside ONE 22px `inline-flex` box (`line-height: 1`,
  `flex-shrink: 0`) — P4; the first cut boxed only the `icon` form.
- `app` form → `<AppIcon icon slug color={app.color || undefined} size={22} />`
  (what `Switcher` did, plus the box and the colour fallback: the manifest
  defaults `icon`/`color` to `""`; an emoji's line box is 34px tall, an empty
  icon has no width, an empty stroke paints nothing — each put that row out of
  column, in the switcher already and in the rail once it drew glyphs).
- `icon` form → `<Icon name size={16} color="var(--text-paper-d)" />` in the
  box (what `FixedLink` did, moved).

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
- **P4** Review round 1: both `NavGlyph` forms in one box + `color || undefined`;
  every box property and stroke colour pinned in `NavGlyph.test.tsx`; a
  per-row glyph case in `GlobalNav.test.tsx`.
- **P5** Review round 2 (tests only): both per-row cases assert the 22px box
  on every row and the FORM (22px App glyph / 16px destination glyph).

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
  `Switcher` (:120-124 `AppIcon size={22}`; :133-140 `FixedLink`, Help filtered);
  `FixedLink` is :62-79 (the plan first said 80).
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
`button[name="Platform menu"]`; at 390 the rail was tucked and `Show Scratches`
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

## Review round 1 (2026-09-19 — defect / conformance / veracity / regression, in parallel, isolated snapshots)

Worst finding: **MEDIUM** (a claimed pin that pinned nothing). Nothing HIGH.

- **Conformance**: every deliverable present; "not doing" respected; P1–P3
  cover every hunk. One gap: the plan called `GlobalNav.test.tsx` the "no
  visual change" pin, and with both `<NavGlyph>` lines deleted from
  `GlobalNav.tsx` it stays 12/12 green.
- **Veracity**: rail mutation pins TRUE (as stated, 1 failed / 24 passed each,
  naming `RCA` / `Knowledge base`); `NavGlyph.test` size pins TRUE; every
  code-comment sentence and ground-truth line TRUE (two cosmetic: `FixedLink`
  :62-79, `Show Scratches`). FALSE: the same pin claim — and `NavGlyph`'s
  `inline-flex` / centring / `flexShrink` / colour each survived deletion
  40/40. Live-check numbers judged derived (row 30 = 22 + 2×4; glyph order =
  manifests + hook order).
- **Regression**: none. Switcher HTML byte-identical to master for named /
  file / emoji / unknown icons and destinations; `.chat-rail__menu-item`'s only
  other consumer (⋯ buttons) is out of the child combinator's reach; the six
  suites `ChatListRail` / `GlobalNav` / `AppIcon` / `Icon` /
  `chat-rail-responsive` / `layering` 67/67 on master → 68/68 on the branch
  (+1 = the rail's icon case); menuitem accessible names unchanged for all
  icon forms.
- **Defect**: nothing reaching a user for a shipped App. LOW: the `app` form
  was unboxed, so an emoji / empty icon / empty colour App sat out of column
  (measured 42px row, label at 67.6 / 16 vs 38, invisible stroke).

**P4** (`e5feda7b`) answers all three: one box for both forms (+ `color ||
undefined`), every box property pinned, a per-row glyph case for the switcher.
Mutation pins re-run after P4 — each of the six that passed before now
reddens: drop `flexShrink` 5, drop `inline-flex`+centring 5, drop the muted
colour 1, delete the switcher's App glyph 1, delete `FixedLink`'s glyph 1,
skip the `app` branch outright 6 (round 2 corrected the label: the faithful
"unbox the `app` form, keep the colour fallback" reddens **4** — the emoji,
named, file and empty-icon box cases — which is the honest figure for the box). Real Chromium: six glyph forms on the rail's CSS all
30px / box 22×22 / label x=38; the built switcher's ten rows all 36px, glyph
centre offset 0.0, label x=44 — the added box changes nothing visible for a
shipped App. Rail live check re-run unchanged (11/11 at 1280 and 390, 30px).

## Review round 2 (2026-09-19 — verify P4 only: regression + veracity)

Worst finding: **LOW** (a guard that let the wrong form through). No code
defect.

- **Regression on P4** (master vs branch, six icon forms — named, file,
  emoji, unknown key, empty icon + empty colour, named + empty colour): the
  switcher's link HTML is byte-identical once the one added wrapper box is
  stripped and `stroke=""` → `currentColor` on the colourless row; the
  destination box gains `line-height: 1` (contains only a 16px svg — no
  effect). `AppIcon`'s other consumers (`AppTag`, `AppDashboard`, `Launcher`,
  `AgentPanel`) and their tests byte-identical; accessible names unchanged for
  all six forms in both menus; `line-height: 1` does not reach the label (a
  sibling text node). Nine suites 165/165 → 173/173, +8 = the branch's tests.
- **Veracity on P4**: (a)–(e) reproduce exactly; (g) `lineHeight` 1 and (h)
  `color || undefined` are each pinned by one case. Two labels corrected
  above ("unbox" → 4; the 67/68 suite set named). **LOW, unguarded**: an App
  row hardcoded to `<NavGlyph icon="flame" />` (the 16px destination form)
  passed both per-row cases — they asked for `[data-icon]`, not the form —
  and an emoji / empty-icon App row was invisible to `[data-icon], img`.

**P5** (`8869b07f`, tests only): every row's first child must be the 22px
box; `apps[0]`'s glyph must be 22px and `destinations[0]`'s 16px. Six
mutations each redden exactly their own case (1 failed / 43 passed): App row
→ destination form in the switcher / in the rail; App glyph deleted in both;
`FixedLink`'s / the rail's destination glyph deleted.

Stop: a test-only fix pinned by its mutations, no mechanism replaced.

