# Plan — every text control wears `.input` (#829)

Reported 2026-09-19 (#829): PR #825 made `.input` the house input chrome but
converted only the two files it touched. The rest of the app still dresses
each field by hand — an inline copy of the border / radius / surface, a scoped
`.foo input { … }` rule, or nothing at all (the browser's inset grey, the look
#825 was opened for). One field type, three looks, and nothing turned red.

A first attempt (PR #832, 2026-09-21) was scrapped: it was built without the
grill-me round, so the decisions below were made in the code instead of with
the user. This plan is that round's outcome (Q1–Q14, 2026-09-21); nothing
under `web/src` changes until it is agreed.

## Ground truth (counted on `72903920`, the branch base, by the guard's own scan)

The scan: every `<input|textarea|select` opening tag in `web/src/**/*.tsx`
(tests excluded; comments blanked, newlines kept), bracket-aware across lines,
minus `type=checkbox|radio|file|range|color|hidden|submit|button|image|reset`.

- **126 text-like controls in 54 files; 27 wear `.input`; 99 bare in 46
  files.** (#829 counted 123 / 18 / 105 on `f5fe658a`: #826, #827 and #828
  landed since; #829's scan also counted three `<select>` / `<input
  type=date>` tokens that sit in comments, and read past two — a placeholder
  saying `src/**` opened a block comment that swallowed `SearchPanel`'s
  fourth input, and `SheetGrid`'s cell.)
- Of the 99, by what the TAG carries: 47 wear a class of their own (a scoped
  rule dresses or sizes them); 36 carry an inline `style` — 9 with a
  `border: "1px …"` on the tag, 4 with a slot reset (`border: none` inside a
  wrapper that draws the box), 16 via a shared style object (`inputStyle`,
  `input`, `field`, `noteInput`, `ta`, `box` — eight objects, six names), 7
  other (two of them a `{...inputStyle, border}` spread, the rest size /
  font only); 16 wear nothing on the tag, of which 15 are dressed or reset by
  a wrapper's descendant rule (`.page-tools`, `.admin-row`,
  `.kb-cardgen__pickbar`, `.kb-docsearch`, `.rvw-drawer__field`,
  `.rvw__search`, `.ev-viewpanel__range`) and ONE is the browser default
  outright (`ItemShareManagers` "add a manager").
- Seven places are the input-group shape (a wrapper draws the box, the
  control inside is bare): `.kb-docsearch`, `.rvw__search`, `SearchPanel`'s
  `fieldWrap` (four rows), `FileTree`'s filter box, `ItemForm`'s tag input,
  `.ev-viewpanel__range`, `.kb-composer`.
- Three wrapper-scoped copies of "fill the width in a column":
  `.kb-field .input`, `.export-dialog__field > .input`,
  `.item-environment .env-field > .input` (all `width: 100%; flex: none`).
- Three versions of the disabled look, one of `aria-invalid`.
- The scoped rules that re-draw the chrome are what the guard's CSS check
  lists on this base; the number is derived when P1 runs it, not written
  here first.

## Decisions (grill-me, 2026-09-21; each against how MUI / Ant / Radix / shadcn / Bootstrap do it)

- **D1 · Three exceptions, and only these, in a whitelist with a reason each
  (Q1).** `TerminalPane`'s command line (a terminal has a prompt, not a
  field); `CommandPalette`'s search line (the panel's header row divided from
  the list by a rule — the Spotlight shape; a bordered field in a bordered
  panel is a box in a box); `SheetGrid`'s cell (the grid draws the lines,
  the cell shows a border only while edited). Monaco's own textarea is not a
  `.tsx` tag and needs no entry. A whitelist entry that matches no bare
  control is stale and fails.
- **D2 · Input groups: the box draws the chrome, the control inside draws
  nothing (Q2).** The MUI `OutlinedInput` / Ant `affix-wrapper` / Radix
  `TextField.Root` shape, chosen over shadcn / Chakra's "field keeps its
  chrome, icon overlaid" because one of ours holds TWO inputs in one box (the
  PM time range, whose CSS already says "the border lives on the GROUP") and
  an overlay cannot. The box wears `input input-group` (the chrome is still
  `.input`'s one rule; `.input-group` adds `display: flex; align-items:
  center; gap`), the control wears `input-group__field` (`flex: 1; min-width:
  0; padding: 0; border: 0; background: none; outline: none; font: inherit;
  color: inherit`). Seven copies become one.
- **D3 · One chrome, two sizes; existing dense-area sizes stay (Q3).** Design
  systems have a size scale, not a height per field. Default 34px /
  `--text-body-sm`; compact is the existing `.inline-edit` (26px) for
  chip-sized selects, table cells and the IDE panes. Sizes that align with a
  neighbour stay as scoped SIZE rules: `.ev-field` 28px (table cells 26px),
  `.admin-row` 28px, `.page-tools` 32px, the IDE panes' 12px type,
  `AppDashboard`'s filters 28px (level with the `Clear filters` button).
  Sizes that were only a by-product of a field's own padding go to the
  house: the KB forms, GroupsPage's form, the cards editor, the review drawer,
  `ItemShareManagers`, `DomainField`, `/review`'s toolbar (its selects were
  26px beside a 32px box; 34 / 34 now).
- **D4 · `.inline-edit` is a chip's width by default (Q4).** `flex: none` —
  MUI / Ant selects are content-width, `fullWidth` is opt-in. With `.input`'s
  `flex: 1` it stretched across the row; with `flex: 0 1 auto` and `.input`'s
  `min-width: 0` it collapsed to its arrow in a tight row (96px → 18px in a
  240px row). It overflows instead, as before. The four rows that fill
  (TodoPanel's two, AskUserCard's two) say `flex: 1` themselves and get the
  shrink back with it.
- **D5 · A textarea's padding is the element's, not a modifier to remember
  (Q5).** `textarea.input { padding: 8px 10px; resize: vertical; line-height }`
  in base.css — MUI `multiline` / Ant `TextArea` / shadcn `Textarea` all own
  their padding. `.kb-textarea` (the KB-only spelling of the same four
  declarations) is deleted. (0,1,1) beats a later single class: a textarea
  whose padding must differ writes `textarea.x` — said in the comment.
- **D6 · `.input--block { width: 100%; flex: none }` for a column (Q6).** The
  MUI `fullWidth` shape rather than Bootstrap's block-by-default (which would
  change #825's row default and not cure `flex: 1` stretching a field DOWN in
  a fixed-height column). The three wrapper-scoped copies are rewritten to
  use it and deleted.
- **D7 · The KB composer keeps its own box; the app composer wears `.input`
  (Q7).** ChatGPT / Claude / Slack composers are one heavier box around text
  + attachments + send; `.kb-composer`'s accent 1.5px card-radius box stays
  and its textarea is a slot (`input-group__field`); `AgentPanel`'s textarea
  draws its own paper-3 border today and becomes `input input--block`. That
  the two chats' composers differ is a design question for another ticket.
- **D8 · `ItemForm`'s title loses its permanent accent border (Q8).** No
  design system marks a "primary field" by border colour; required is `*`,
  wrong is `aria-invalid`. The house look at rest, accent on focus, red via
  `aria-invalid="true"` on an empty submit — `.input[aria-invalid="true"]`
  hoisted from `item-environment.css` to base.css, with a test that the
  attribute appears on the empty submit and clears on the first keystroke.
- **D9 · `.input:disabled { opacity: .7; cursor: not-allowed }` is the
  default; the entity views keep `.ev-field:disabled { opacity: .6; cursor:
  default }` (Q9).** A read-only table draws every cell as a disabled input
  (#448); a not-allowed cursor on each says "forbidden", and a cell is data.
- **D10 · Keyboard focus keeps the global 2px ring (Q10).** `.input`'s
  `outline: none` (moved unchanged from `.kb-input` by #825) breaks
  base.css's own rule ("never to none") and would downgrade ~50 controls
  from the 2px `:focus-visible` ring to a 1px colour change — below WCAG
  2.4.11's 2px-equivalent, and no design system does it (MUI 2px border,
  shadcn `ring-2`, Ant 2px shadow). `.input` drops `outline: none`; mouse
  focus keeps the accent border (`:focus`), keyboard focus gets the global
  ring; an input group's box gets the ring via `.input-group:has(:focus-visible)`
  (its slot's own outline stays off, or the ring would sit inside the box).
  The `aria-invalid` focus ring stays a box-shadow so red + ring both show.
- **D11 · The guard reads string-literal class lists only (Q11).** A computed
  `className={…}` is refused with its own message — a branch the scan cannot
  see is a branch that can be bare — and state goes on `data-` attributes, as
  `.btn[data-active]` does. No control uses a computed class today; the one
  pass-through (`roleWidget`'s `className` prop, every caller passing
  `"ev-field"`) is replaced by the literal `input ev-field` and the prop
  removed.
- **D12 · Three things #829 did not name, done and named (Q12).** The dead
  `.kb-cardgen__body > input, .kb-cardgen__todo, .kb-cardgen__proposal …`
  rule (no tsx uses those classes) is deleted because it draws a chrome the
  guard flags — the rest of cardgen's dead CSS is another ticket; the three
  fill-the-width copies use `.input--block` (D6); `/review`'s selects go to
  the house size (D3). Not touched: anything server-side, config / schema /
  manifest (no `docs/migrations.md` entry), `sandbox-host/`, any handler /
  value / `aria-label` / `data-testid` / `autoFocus` / `readOnly` /
  `disabled` / `rows` / `type` / `placeholder`.
- **D13 · Acceptance is three measurements, not one (Q13).** See
  "Acceptance" below.

### Treatments (the rule each control gets; counts derived by the scan after the sweep)

| Treatment | Rule |
|---|---|
| A · bare or inline copy → `className="input"`; the inline border / radius / background / outline goes; size and type (`width`, `height`, `minHeight`, `padding`, `fontSize`, `resize`, `fontFamily` for a mono field of keys) may stay inline or move to `.input--block` / `.inline-edit`. |
| B · a class of its own that re-draws the chrome → `className="input <cls>"`; the rule keeps only what differs (height, font, width, `resize`, a hover / disabled / active state) and loses `border` / `border-radius` / `background` / `color` / `outline`. |
| C · a slot inside a box → the box wears `input input-group` (or is `.kb-composer`), the control wears `input-group__field`. |
| W · whitelisted (D1). |

### Deliberately not unified

- `.kb-composer`'s accent box (D7); `.ev-table tbody .ev-field` transparent
  until hover (#448); `.ev-field:disabled` (D9); `AppDashboard`'s active
  filter as an inline `color` / `borderColor` (#172) — a state, not chrome;
  the dense-area sizes (D3).
- Surfaces: fields drawn on `--paper` / `--paper-2` today become `--white`;
  radii of 4 / 8 / 0 become 6px; type inherited as 14px becomes 13px where no
  size rule keeps it. That IS the ticket — one look. (The differential in
  Acceptance lists each one.)
- The autofocused in-place renames (`.chat-rail__rename`, `.kb-att__rename`,
  `.kb-colpage__nameedit` / `__descedit`, FileTree's) drew an accent border
  themselves; `.input:focus` draws it now, and they commit on blur.

## Phases (one commit each; flat numbers; the guard is RED from P1 until the last conversion)

- **P1** `base.css`: `textarea.input`, `.input--block`, `.input-group` +
  `__field`, `.input:disabled`, `.input[aria-invalid]` (+ the focus change of
  D10), `.inline-edit` reduced to its size with `flex: none`; `.kb-textarea`
  and the three fill-the-width copies deleted (their users updated);
  `input-class.test.ts` rewritten as the global guard (below), red on this
  base with the bare controls listed by `file:line`.
- **P2** `components/` + the `.page-tools` pair (SkillHubPage, WuiOverviewPage).
- **P3** `pages/` outside `kb/` + `pages/investigation/`.
- **P4** `pages/kb/` + the `kb.css` scoped rules.
- **P5** `renderers/entity/` + `entity-views.css` + `WuiView`; the guard goes
  green; the treatment counts are derived and written here.
- **P6** Acceptance (below); anything it finds is fixed in P6 with a test
  that pins it.

## Test plan (red first; targeted only)

- `web/src/styles/input-class.test.ts` — the scan of Ground truth. Every
  control wears the class as a literal or sits in the whitelist; a
  whitelist entry must match exactly one bare control; a computed class is
  refused; a dressed control's tag carries no inline border (shorthand or
  longhand), radius, background, box-shadow or outline; no CSS rule whose
  selector names a control element, `.input` outside base.css, or a class
  that rides beside `input` on any tag (derived from the sources, never
  listed by hand) declares one of those (`0` / `none` / `transparent` and
  state selectors excepted; base.css's theme reset excepted by exact
  selector; a rule keyed on an id or a data attribute is not seen — none
  targets a control today, said in the comment). The scanner's edges are
  pinned on a temp-dir fixture: a docblock's `<select>`, a JSX comment, a
  placeholder saying `src/**` or `e.g. /*.md`, a `>` in a string, a computed
  class; every reported line is checked to hold its tag. base.css's
  companions are pinned by regex (`textarea.input` padding, `.input--block`,
  `.input-group` + slot, `.inline-edit`'s `flex: none` and `min-height`,
  `.input:disabled`, `.input[aria-invalid]`, no `outline: none` on `.input`).
- Mutation probes, on file copies, before the push: each guarantee above is
  broken once and exactly its test must redden — recorded here with the
  mutation and the test that caught it.
- `entity-views.test.ts`: the range's ends are slots and the range's rule
  draws no chrome; the table's cells keep `height: 26px` AND `min-height:
  26px` (a `min-height` on `.ev-field` would otherwise win); `.ev-field:disabled`
  keeps the default cursor.
- `item-environment.test.ts`: the `aria-invalid` rules are base.css's and the
  sheet keeps no copy; `ItemForm.test.tsx`: `aria-invalid` on the empty
  submit, gone on the first keystroke.
- Tests that pinned an inline `minWidth: 0` (`TodoPanel.test.tsx`,
  `SearchPanel.layout.test.tsx`) assert the class instead, and the guard pins
  `.input { flex: 1; min-width: 0 }` — each link reddens on its own mutation.
- Per-change gate: the touched directories' suites + `ruff check` /
  `ruff format --check` / `ty check` / `pnpm typecheck`; the full suite is CI's.

## Acceptance (D13) — three measurements, light AND dark, in real Chromium

1. **HEAD**: every route, panel and modal that can be opened on a fresh
   instance; each rendered control's computed border (colour: `--paper-3` or
   `--accent`), radius (`--radius-btn`) and surface (`--white`), or slot
   (border 0, transparent, inside an `.input` box); numbers derived from the
   drivers' `report.json`, never from their logs. What could not be opened
   (needs an admin, grants, proposals, findings, published skills, deployed
   pages) is listed by name.
2. **Base vs HEAD differential**: `git archive` both sides' stylesheets, one
   harness with the same markup (each control as its base tsx dressed it and
   as HEAD does), every key compared — bbox, font, padding, cursor, opacity,
   flex — and every difference matched to "Deliberately not unified" or
   called a regression. This is what a HEAD-only check cannot see: a cell
   that grew 2px, a rename 2px shorter than the row it replaces.
3. **Tight-row probe**: every control that sits in a flex row, in a 240px row
   with long neighbours — overflow is fine, collapse is not.

## Review and CI

Push as soon as the seconds-long gates pass (backup + draft PR), cancel the
run the push starts. Four lenses (conformance / veracity / defect /
regression) in one parallel pass, each measuring on its own `git archive`
snapshot outside the repo (the worktree-per-reviewer of the first attempt was
refused by the harness and two reviewers probed in the author's tree). A fix
that replaces a mechanism gets another round; three rounds is the budget. CI
runs on the final sha only after a round comes back clean — if reviewers
cannot be launched (the account's spend limit), stop and report; do not run
CI in their place.
