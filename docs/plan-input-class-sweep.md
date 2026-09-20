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

- 124 text-like controls in 53 files; 26 wear `.input`; **98 bare in 46 files**.
  (#829 counted 123 / 18 / 105 on `f5fe658a`; #826 and #827 landed since, and
  #829's scan counted three `<select>` / `<input type=date>` tokens that are in
  comments.)
- Of the 98 (by what the TAG carries, counted by the same scan): 46 wear a
  class of their own (a scoped rule dresses or sizes them); 26 carry an inline
  `style` (9 with a border copy on the tag, 4 with a slot reset — `border:
  none` inside a wrapper that draws the box — 13 via a shared style object
  `input` / `field` / `noteInput` / `inputStyle`); 16 wear nothing on the tag,
  of which 15 are dressed or reset by a wrapper's descendant rule
  (`.page-tools`, `.admin-row`, `.kb-cardgen__pickbar`, `.kb-docsearch`,
  `.rvw-drawer__field`, `.rvw__search`, `.ev-viewpanel__range`) and ONE is
  the browser default outright (`ItemShareManagers` "add a manager").
  Whether a class or style amounts to the house look is what the live check
  decides — #829 says so: "不能用 grep 判".

## Decisions

1. **Four treatments, decided per control, recorded here and derived into the
   guard.** The guard test lists the whitelist; everything else must wear the
   class, so the table below is the whole set of exceptions.

   | Treatment | Rule | Count |
   |---|---|---|
   | A · bare or inline copy → `className="input"`; inline border / radius / background / outline / font-family removed; **size** props (`width`, `height`, `minHeight`, `padding`, `fontSize`, `resize`) may stay inline or move to `.input--block` / `.inline-edit`. | (P5) |
   | B · a class of its own that re-draws the chrome → `className="input <cls>"`; the rule keeps only what differs (height, font, width, `resize`, a hover / disabled / active variant) and loses `border` / `border-radius` / `background` / `color` / `outline`. | (P5) |
   | C · a slot inside a box → the box wears `className="input input-group"` (or keeps its own chrome when that chrome is deliberately not the house one — only `.kb-composer`, the accent-bordered primary action); the control wears `className="input-group__field"`. The box's `:focus-within` is the focus ring. | (P5) |
   | W · whitelisted, with the reason in the test. | 2 |

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
     Six copies existed (`.kb-docsearch`, `.rvw__search`, `SearchPanel`'s
     `fieldWrap`, `FileTree`'s filter box, `.ev-viewpanel__range`, `.kb-composer`).
   - `.inline-edit` keeps `height: 26px; padding: 0 8px` and gains
     `min-height: 26px` (else `.input`'s `min-height: 34px` wins); its
     border / radius / background / color go. Every `.inline-edit` element also
     wears `input`.
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
- **P2** `components/` (17 files).
- **P3** `pages/` outside `kb/` (11) + `pages/investigation/` (5).
- **P4** `pages/kb/` (15) + the `kb.css` scoped rules.
- **P5** `renderers/entity/` (5) + `entity-views.css` + `WuiView`; the guard
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
