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
  own (a scoped rule dressed or sized them); 36 carried an inline `style` — 9
  with a border copy on the tag, 4 with a slot reset (`border: none` inside a
  wrapper that draws the box), 16 via a shared style object (`input` /
  `field` / `noteInput` / `inputStyle` / `ta` / `box`), 7 a size / font
  style only; 16 wore nothing on the tag, of which 15 were dressed or reset
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
   | A · bare or inline copy → `className="input"`; inline border / radius / background / outline / font-family removed; **size** props (`width`, `height`, `minHeight`, `padding`, `fontSize`, `resize`) may stay inline or move to `.input--block` / `.inline-edit`. | 39 plain `input` |
   | B · a class of its own that re-draws the chrome → `className="input <cls>"`; the rule keeps only what differs (height, font, width, `resize`, a hover / disabled / active variant) and loses `border` / `border-radius` / `background` / `color` / `outline`. | 73 (`input--block` 32, `inline-edit` 14, `ev-select` 11, `ev-field` 7, one scoped class each for the rest) |
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
   - Fixed sizes stay (`.ev-field` 28px, `.inline-edit` 26px, `.admin-row`
     28px, the IDE panes' 12px type): #829 §2 keeps "尺寸 / 對齊".

4. **Whitelist (W), each with its reason in `input-class.test.ts`:**
   - `TerminalPane` command line — a terminal has a prompt, not a field; a box
     around the command line is not a terminal.
   - `CommandPalette` search line — the panel's header row, divided from the
     list by a rule (the Spotlight shape); a bordered field at the top of a
     bordered panel is a box in a box.

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

## Test plan (red first, targeted only)

- `web/src/styles/input-class.test.ts` — the global scan: red on `d12001e1`
  with the 98 bare controls listed by `file:line`; each whitelist entry must
  match exactly ONE control in its file and that control must be bare (a
  dressed one makes the entry stale → red). A dressed control's tag must not
  carry `border:` / `borderRadius` / `background:` / `outline:`. No CSS rule
  whose selector names `input` / `textarea` / `select` as an element, or one
  of the stripped modifier classes, may declare `border` / `border-radius` /
  `background` (base.css's theme reset excepted by exact selector).
- `web/src/styles/entity-views.test.ts` — the range's time inputs are slots:
  the range's own rule declares no border / background; the ring per END stays.
- Existing component tests keep passing; those that query by class
  (`.inline-edit`, `.ev-field`) still match because the class is kept.

## Live check record (2026-09-21, worktree build on 127.0.0.1:8263, real Chromium 1148, 1280×900, light AND dark)

Two drivers read every rendered `<input|textarea|select>`'s computed
`border` / `border-radius` / `background` and call it house when the border is
`1px solid` (paper-3, or accent while focused) at `--radius-btn`, or a slot
(border 0, transparent) inside an `.input` box. Both schemes, every state:

- 20 routes as they load (`/`, `/a/playground`, `/a/playground/new`, a
  playground item, `/a/pm`, a PM project, `/kb/collections`, a collection +
  its cards / wiki / review tabs, `/kb/graph`, `/kb/chats`, `/groups`,
  `/work-calendar`, `/my-resources`, `/wui`, `/skill-hub`, `/review`,
  `/diagnostics`): 26 controls in light, the same 26 in dark, **0 not house**.
- 19 interactive states (the item's Files and Search panes; the Env, Tools,
  Share, Export and Sandbox modals; the PM project's view settings popover,
  the table's and board's New-issue form, the health view; a new KB chat;
  the collection title rename; a new context card; the diagnostics model
  matrix with its question form; `/wui`, `/skill-hub`, `/my-resources`,
  `/kb/graph`): 35 + 35 controls in light, the same in dark, **0 not house**
  — the two the driver flagged are by design: the KB composer's textarea is
  a slot in `.kb-composer`'s accent box, not an `.input` box, and
  `ime-text-area` is Monaco's own.
- Read by eye (screenshots in the job dir): the create-item form's title /
  tags / description at 38px beside the 38px Owner box and Picker; the
  Search pane's four group rows at 28px with the focused row's accent
  border; `/review`'s toolbar one height across search and selects; the
  view settings popover's selects at 28px and the time range as ONE box; the
  New-issue form's eight fields at 28px in dark with the title's focus
  accent; the cards editor's title / term / search; the KB composer's
  accent box with a transparent slot in dark.

Not reached on this instance (the default user is not an admin, and there
were no grants, proposals, published skills or deployed pages): the
`/my-resources` admin row, the New-group form, the `.page-tools` search /
selects on `/wui` and `/skill-hub`, the Share / Permission role selects,
the review drawer, `AskUserCard`, the chat rail / KB chats / attachment
renames, `ManageChatsModal`, `TuneParsingModal`, the wiki guidance form,
`WuiView`'s address bar, `SkillHubPickerModal`. All wear the same class and
the same three modifiers the reached ones do; what is unverified there is
layout (a select growing in a row), not chrome.

Found and fixed by the check, before the push: `.input`'s `flex: 1` would
have stretched every chip-sized `inline-edit` select in a flex row (the four
`marginLeft: auto` role selects, SanityTable's category filter), so
`.inline-edit` took a flex item's default share and the four rows that fill
say `flex: 1` themselves.
