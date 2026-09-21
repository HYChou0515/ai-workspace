# Plan — every text control wears `.input` (#829)

Reported 2026-09-19 (#829): PR #825 made `.input` the house input chrome but
converted only the files it touched. The rest of the app still dressed each
field by hand — some in an inline copy of the border/radius/surface, some in a
scoped `.foo input { … }` rule, some not at all (the browser's inset grey, the
look #825 was opened for). One field type, three looks, and nothing turned red.

## Ground truth (counted on `d12001e1`, the branch base — not #829's `f5fe658a`)

The scan is the one the guard test now runs: every `<input|textarea|select`
opening tag in `web/src/**/*.tsx` (tests excluded, comments stripped), minus
`type=checkbox|radio|file|range|color|hidden|submit|button|image|reset`.

- **126 text-like controls in 54 files; 27 wore `.input`; 99 bare in 46
  files.** (#829 counted 123 / 18 / 105 on `f5fe658a`: #826 and #827 landed
  since; #829's scan counted three `<select>` / `<input type=date>` tokens
  that sit in comments and, like this plan's first pass, missed two — a
  placeholder saying `src/**` opened a block comment that swallowed
  `SearchPanel`'s fourth input, and `SheetGrid`'s cell was read past. The
  guard's scan, which found both, is the count.)
- Of the 99 (by what the TAG carried, same scan): 47 wore a class of their
  own (a scoped rule dressed or sized them); 36 carried an inline `style` —
  11 with a border on the tag (two of them a `{...inputStyle, border}`
  spread), 4 with a slot reset (`border: none` inside a wrapper that draws
  the box), 16 via a shared style object (`input` / `field` / `noteInput` /
  `inputStyle` / `ta` / `box`), 5 a size / font style only; 16 wore nothing
  on the tag, of which 15 were dressed or reset
  by a wrapper's descendant rule (`.page-tools`, `.admin-row`,
  `.kb-cardgen__pickbar`, `.kb-docsearch`, `.rvw-drawer__field`,
  `.rvw__search`, `.ev-viewpanel__range`) and ONE was the browser default
  outright (`ItemShareManagers` "add a manager"). Whether a class or style
  amounted to the house look is what the live check decides — #829 says so:
  "不能用 grep 判".

## Decisions

1. **Four treatments, decided per control, recorded here and derived into the
   guard.** The guard test lists the whitelist; everything else must wear the
   class, so the table below is the whole set of exceptions.

   Counts are of all 126 after the sweep, by the guard's scan (classify each
   tag by its `className`).

   | Treatment | Rule | Count |
   |---|---|---|
   | A · bare or inline copy → `className="input"`; inline border / radius / background / outline removed; **size and type** props (`width`, `height`, `minHeight`, `padding`, `fontSize`, `resize`, and `fontFamily` for a mono field of keys — EnvVarsModal, WorkCalendarPage) may stay inline or move to `.input--block` / `.inline-edit`. | 40 plain `input` |
   | B · a class of its own that re-draws the chrome → `className="input <cls>"`; the rule keeps only what differs (height, font, width, `resize`, a hover / disabled / active variant) and loses `border` / `border-radius` / `background` / `color` / `outline`. | 72 (`input--block` 32, `inline-edit` 13, `ev-select` 11, `ev-field` 7, one scoped class each for 15 — the four overlap: 5 wear `input--block` with a scoped class, 1 wears `input--block inline-edit`) |
   | C · a slot inside a box → the box wears `className="input input-group"` (or keeps its own chrome when that chrome is deliberately not the house one — only `.kb-composer`, the accent-bordered primary action); the control wears `className="input-group__field"`. The box's `:focus-within` is the focus ring. | 11 slots in 10 boxes |
   | W · whitelisted, with the reason in the test. | 3 |

2. **Three additions to `base.css`, no new file.**
   - `textarea.input { padding: 8px 10px; resize: vertical; line-height: … }` —
     a textarea with `.input`'s `padding: 0 10px` has its first line on the
     border. Automatic by element rather than a modifier to remember
     (`.kb-textarea`, the KB-only spelling of the same four declarations, is
     folded in and deleted; the two names would drift like `.kb-input` did).
     Specificity (0,1,1) means a later single-class override of a textarea's
     padding must write `textarea.x` — said in the comment.
   - `.input--block { width: 100%; flex: none; }` — in a column, `.input`'s
     "fill the flex row" is the wrong axis (`flex: 1` stretches the field
     DOWN in a fixed-height column) and in a block parent an input is its UA
     width (~20ch). `.kb-field .input` and `.export-dialog__field > .input`
     are the two wrapper-scoped spellings of the same pair and stay as they are
     (they are sizing, scoped to a form, and #829 §2 keeps sizing rules).
   - `.input-group { display: flex; align-items: center; gap }`,
     `.input-group:focus-within { border-color: accent }`,
     `.input-group__field { flex: 1; min-width: 0; padding: 0; border: 0;
     background: none; outline: none; font: inherit; color: inherit }` — the
     Bootstrap `input-group` shape: one box, adornments beside the control.
     Seven copies existed (`.kb-docsearch`, `.rvw__search`, `SearchPanel`'s
     `fieldWrap`, `FileTree`'s filter box, `ItemForm`'s tag input,
     `.ev-viewpanel__range`, `.kb-composer`).
   - `.inline-edit` keeps `height: 26px; padding: 0 8px` and gains
     `min-height: 26px` (else `.input`'s `min-height: 34px` wins) and
     `flex: 0 1 auto` (a chip's width is its content's; the four rows that
     want it to fill — TodoPanel's two, AskUserCard's two — say `flex: 1`);
     its border / radius / background / color go. Every `.inline-edit`
     element also wears `input`.
   - `.input[aria-invalid="true"] { border-color: var(--err) }` (+ the
     focus ring) moves up from `item-environment.css`, where it was scoped to
     one panel; `ItemForm`'s title field is the second user.

3. **What is deliberately NOT unified.**
   - `.kb-composer` keeps its accent 1.5px card-radius box: the composer is
     the page's primary action and looks different on purpose; its textarea is
     a slot (C).
   - `.ev-table tbody .ev-field` stays transparent-until-hover: a table cell
     that shows a border on every row is a spreadsheet, not a table (#448).
   - `AppDashboard`'s filter `<select>` keeps its active accent border + text
     (#172) as an inline `borderColor` / `color` — a state, not chrome.
   - Surfaces: `var(--paper)` / `var(--paper-2)` backgrounds on some fields
     become `var(--white)`. That IS the ticket — one look.
   - Fixed sizes stay (`.ev-field` 28px, the table's cells 26px,
     `.inline-edit` 26px, `.admin-row` 28px, `.page-tools` 32px, the IDE
     panes' 12px type, AppDashboard's filters 28px beside a 28px button):
     #829 §2 keeps "尺寸 / 對齊". Sizes that were only a by-product of a
     field's own padding go to the house: 34px and `--text-body-sm` (13px,
     where the body's 14px used to be inherited) — ReviewPage's toolbar,
     the KB forms, GroupsPage's form, the KB cards editor, the review
     drawer, ItemShareManagers, DomainField, SanityTable's filter.
   - `.ev-field:disabled` keeps the views' own look (opacity .6, default
     cursor): a read-only cell is data, not a forbidden action, and
     `.input:disabled`'s not-allowed cursor on every cell of a read-only
     table said otherwise. (P5 deleted it; the review round measured it and
     P7 put it back.)
   - `ItemForm`'s title loses its permanent 1.5px accent border (the
     design-handoff's "accent field"): the house border at rest, accent on
     focus, red via `aria-invalid` on an empty submit — which is also the
     first time the state reached AT.
   - `ReviewPage`'s three filter selects go from `inline-edit` (26px) to the
     house 34px so the toolbar is one height with its search box (base had
     them at 26 beside a 32px box).
   - The autofocused in-place renames (`.chat-rail__rename`,
     `.kb-att__rename`, `.kb-colpage__nameedit` / `__descedit`, FileTree's)
     drew an accent border themselves; `.input:focus` draws it now, and they
     commit on blur, so no one sees the paper-3 rest state.
   - Keyboard focus on the ~50 controls that now wear the class is
     `.input`'s: `outline: none` + a 1px accent border (the rule #825 moved
     unchanged from `.kb-input`), where the bare ones had the global 2px
     `:focus-visible` ring. Not a decision of this ticket — noted for an
     a11y pass, since a 1px colour change is a weaker indicator than the
     ring base.css promises "never to none".

4. **Whitelist (W), each with its reason in `input-class.test.ts`:**
   - `TerminalPane` command line — a terminal has a prompt, not a field; a box
     around the command line is not a terminal.
   - `CommandPalette` search line — the panel's header row, divided from the
     list by a rule (the Spotlight shape); a bordered field at the top of a
     bordered panel is a box in a box.
   - `SheetGrid`'s spreadsheet cell — the grid draws the lines and the cell
     shows a border only while edited; a box per cell is a form, not a sheet.
   Monaco's own textarea (`ime-text-area`) is not a `.tsx` tag and needs no
   entry; the live check names it when it meets one.

## Phases (one commit each; the guard is RED from P1 until P5)

- **P1** `base.css`: `textarea.input`, `.input--block`, `.input-group` +
  `__field`, `.inline-edit` stripped, `aria-invalid` hoisted; `.kb-textarea`
  deleted; `input-class.test.ts` rewritten as the global scan + whitelist +
  "no inline chrome on a dressed control" + "no scoped rule re-draws the
  chrome"; this plan.
- **P2** `components/` (14 files) + the `.page-tools` pair (SkillHubPage,
  WuiOverviewPage).
- **P3** `pages/` outside `kb/` (6) + `pages/investigation/` (3, plus the
  two whitelisted).
- **P4** `pages/kb/` (13) + the `kb.css` scoped rules.
- **P5** `renderers/entity/` (4) + `entity-views.css` + `WuiView`; the guard
  goes green.
- **P6** Live check in Chromium, light AND dark (#825's lesson: the text
  guard was green while `:not()` blew three pages up); layout fix-ups found
  there; `docs/migrations.md` needs no entry (no config, no schema, no knob).
- **P7** The review round (conformance / veracity / regression; the defect
  lens was cut off by the account's spend limit before it reported): the
  four regressions above, the guard's line numbers / derived modifier list /
  longhands / `.foo .input` rules / literal-only classes, a test for
  ItemForm's `aria-invalid`, and this plan's stale numbers.

## Test plan (red first, targeted only)

- `web/src/styles/input-class.test.ts` — the global scan: red on `d12001e1`
  listing 96 controls by `file:line` (the 99 bare minus the three the
  whitelist excuses; the line is the file's own — P7 fixed a scan that
  counted lines on comment-stripped text and was 267 lines out); each
  whitelist entry must match exactly ONE control in its file and that
  control must be bare (a dressed one makes the entry stale → red); a
  computed `className={…}` is refused with its own message. A dressed
  control's tag must not carry a border (shorthand or longhand), radius,
  background, box-shadow or outline. No CSS rule whose selector names
  `input` / `textarea` / `select` as an element, names `.input` outside
  base.css, or names a class that rides beside `input` on any tag (derived
  from the sources, not listed by hand) may declare one of those — `0` /
  `none` / `transparent` and state selectors excepted, base.css's theme
  reset excepted by exact selector. The scanner's own edges (a docblock's
  `<select>`, a JSX comment, `src/**` and `e.g. /*.md` in a placeholder, a
  `>` in a string, a computed class) are pinned on a temp-dir fixture.
  Mutation probes in the review round (P7): eleven, each reddened exactly
  the test that guards it — a longhand `background-color` on `.ev-field`, a
  `border` on `.kb-field .input`, a `box-shadow` on a class invented on the
  spot, an inline `backgroundColor`, a computed class, the tbody
  `min-height`, `.inline-edit`'s `flex: none`, ItemForm's `aria-invalid`,
  `.ev-field:disabled`, a dressed whitelist entry, a `border-top` beside an
  allowed `border-color`.
- `web/src/styles/entity-views.test.ts` — the range's time inputs are slots:
  the range's own rule declares no border / background; the ring per END stays.
- Existing component tests keep passing; those that query by class
  (`.inline-edit`, `.ev-field`) still match because the class is kept.

## Live check record (2026-09-21, worktree build on 127.0.0.1:8263, real Chromium 1148, 1280×900, light AND dark; re-run at P7 on the fixed build)

Two drivers (`livecheck.cjs`, `livecheck2.cjs` in the job dir) read every
rendered `<input|textarea|select>`'s computed `border` / `border-radius` /
`background` and call it house when the border is `1px solid` in `--paper-3`
(or `--accent` while focused) at `--radius-btn` on a `--white` surface, or a
slot (border 0, transparent) inside an `.input` box. The numbers below are
derived from the drivers' `report.json` files (`totals.py`), not from their
logs, both schemes:

- **20 routes as they load** (`/`, `/a/playground`, `/a/playground/new`, a
  playground item, `/a/pm`, a PM project, `/kb/collections`, a collection +
  its cards / wiki / review tabs, `/kb/graph`, `/kb/chats`, `/groups`,
  `/work-calendar`, `/my-resources`, `/wui`, `/skill-hub`, `/review`,
  `/diagnostics`): 21 controls in light, 21 in dark, **0 not house**. (The
  two App dashboards were read while the fresh instance had no items yet;
  read again with items, `/a/pm`'s three filter selects are 28px.)
- **20 interactive states** (the item's Files and Search panes; the Env,
  Tools, Share, Export and Sandbox modals; the PM project's view-settings
  popover, the table's and the board's New-issue form, the table with one
  row created, the health view; a new KB chat; the collection title rename;
  a new context card; the diagnostics model matrix with its question form;
  `/wui`, `/skill-hub`, `/my-resources`, `/kb/graph`): 82 controls in light,
  82 in dark, **2 flagged in each, both by design** — the KB composer's
  textarea is a slot in `.kb-composer`'s accent box, not an `.input` box,
  and `ime-text-area` is Monaco's own. Everything else is house.
- Read by eye (screenshots in the job dir): the create-item form's title /
  tags / description at 38px beside the 38px Owner box and Picker; the
  Search pane's four group rows at 28px with the focused row's accent
  border; `/review`'s toolbar one height across search and selects; the
  view-settings popover's selects at 28px and the time range as ONE box; the
  New-issue form's eight fields at 28px in dark with the title's focus
  accent; the cards editor's title / term / search; the KB composer's
  accent box with a transparent slot in dark.
- **Base-vs-HEAD differential** (the review round's static harness: the real
  stylesheets of `d12001e1` and of this branch under the same markup, 26
  cases, 77 controls, both schemes): after P7 the table's cells are 26px
  on both sides, disabled cells 0.6 / default cursor on both, the KB chats
  rename 46.1px on both (= the row button it replaces), and a chip-sized
  `inline-edit` select no longer shrinks. What still differs is the ticket:
  23 surfaces paper → white, 10 radii → 6px, heights and type to the house
  where nothing kept a size (listed in §3), the title's rest border.

Not reached on this instance (the default user is not an admin, and there
were no grants, proposals, findings, published skills or deployed pages):
the `/my-resources` admin row, the New-group form, the `.page-tools` search
/ selects on `/wui` and `/skill-hub`, the Share / Permission role selects,
the review drawer, `AskUserCard`, the chat rail / KB chats / attachment
renames, `ManageChatsModal`, `TuneParsingModal`, the wiki guidance form,
`WuiView`'s address bar, `SkillHubPickerModal`, `HealthView`'s three filter
selects (they render only with findings), the table's cells in the live
app (the harness above covers them). All wear the same class and the same
three modifiers the reached ones do; what is unverified there is layout,
not chrome.

Found and fixed by the checks: `.input`'s `flex: 1` would have stretched
every chip-sized `inline-edit` select in a flex row (P6); the review
round's differential then found its `min-width: 0` letting the same
selects collapse to their arrow in a tight row, `.ev-field`'s new
`min-height` growing the table's 26px cells to 28, a hand-computed 44px
on the KB chats rename against a 46.1px row, the App dashboard's filters
26px beside a 28px button, and the views' disabled look replaced by the
house one (all P7).
