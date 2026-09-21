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

The reader is the guard's: every `<input|textarea|select` JSX element in
`web/src/**/*.tsx` (tests excluded), through the TypeScript compiler's
parser (P7; the first reader was a regex scan, and both agree on every
control of this base and of the swept tree), minus
`type=checkbox|radio|file|range|color|hidden|submit|button|image|reset`.

- **126 text-like controls in 54 files; 27 wear `.input`; 99 bare in 46
  files.** (#829 counted 123 / 18 / 105 on `f5fe658a`; its scan is not in
  the tree and cannot be reproduced — this guard's reader on `f5fe658a` gives
  117 / 18 / 99. The three `<select>` / `<input type=date>` tokens in
  comments account for part of the gap; the rest is #829's own tool.)
- Of the 99, by what the TAG carries: 47 wear a class of their own (a scoped
  rule dresses or sizes them); 36 carry an inline `style` — 10 with a
  border on the tag (nine as `border: "1px …"`, `AppDashboard`'s as a
  template literal), 4 with a slot reset (`border: none` inside a wrapper
  that draws the box), 16 via a shared style object (`inputStyle`, `input`,
  `field`, `noteInput`, `ta`, `box` — eight objects, six names), 6 other
  (three `{...inputStyle, …}` spreads — one adding a border — and three
  size / font only); 16
  wear nothing on the tag, of which 15 are dressed or reset by
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
- 23 scoped rules (66 declarations) draw a control's chrome on this base —
  the guard's CSS check, run with the plan's class list, lists exactly them
  (the second table below).

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
  a fixed-height column). `flex: none` also removes flex-SHRINK: in a column
  squeezed below its content a `.input--block` textarea keeps its rows and
  the column scrolls (a modal's own safety net) instead of the textarea
  crushing to nothing — on base it shrank to 70px. The three wrapper-scoped
  copies are rewritten to use it and deleted.
- **D7 · The KB composer keeps its own box; the app composer wears `.input`
  (Q7).** ChatGPT / Claude / Slack composers are one heavier box around text
  + attachments + send; `.kb-composer`'s accent 1.5px card-radius box stays
  and its textarea is a slot (`input-group__field`); `AgentPanel`'s textarea
  draws its own paper-3 border today and becomes `input input--block`. The
  slot reset drops the UA's 2px textarea padding (the box's 12px is the
  spacing), and the composer box gets the same `:has(:focus-visible)` ring
  every group box gets — its slot has no outline and its border is accent at
  rest, so keyboard focus moved with nothing on screen. That the two chats'
  composers differ is a design question for another ticket.
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
- **D10 · Focus keeps the global 2px ring (Q10).** `.input`'s
  `outline: none` (moved unchanged from `.kb-input` by #825) breaks
  base.css's own rule ("never to none") and would downgrade ~50 controls
  from the 2px `:focus-visible` ring to a 1px colour change — below WCAG
  2.4.11's 2px-equivalent, and no design system does it (MUI 2px border,
  shadcn `ring-2`, Ant 2px shadow). `.input` drops `outline: none`: a
  focused field shows the accent border AND the global ring. The grill
  said "mouse = border, keyboard = ring"; Chromium does not offer that split
  — it applies `:focus-visible` to a text field (and a `<select>`) on click
  as well as on Tab — so mouse and keyboard look the same, border colour
  plus ring, which is the shadcn / Ant shape (border-ring + ring-[3px];
  border + 2px shadow). Found by the review round's defect lens and kept.
  An input group's box gets the ring via `.input-group:has(:focus-visible)`
  (its slot's own outline stays off, or the ring would sit inside the box) —
  and a toggle button inside the box lights the box too: the box is the
  field. Two places would have shown THREE indicators and show two: the PM
  time range keeps its per-end inset ring and drops the box's
  (`.ev-viewpanel__range:has(:focus-visible) { outline: none }` — not
  "none": the end's ring stays); the invalid field's extra soft shadow is
  gone (red border + the ring).
- **D11 · The guard reads string-literal class lists only (Q11).** A computed
  `className={…}` is refused with its own message — a branch the scan cannot
  see is a branch that can be bare — and state goes on `data-` attributes, as
  `.btn[data-active]` does. No control uses a computed class today; the one
  pass-through (`roleWidget`'s `className` prop, every caller passing
  `"ev-field"`) is replaced by the literal `input ev-field` and the prop
  removed. The same holds for `style` on a dressed control: an object
  literal, or a same-file `const` object literal (`style={fieldSize}`,
  `{...fieldSize}`) which the guard reads; anything else (an import, a call,
  a conditional spread) is refused — a review probe put a border into
  `SearchPanel`'s shared `input` const and the tag-text check stayed green.
- **D12 · Three things #829 did not name, done and named (Q12).** The dead
  `.kb-cardgen__body > input, .kb-cardgen__todo, .kb-cardgen__proposal …`
  rule (no tsx uses those classes) is deleted because it draws a chrome the
  guard flags — the rest of cardgen's dead CSS, `.kb-cardgen__todo`'s size
  rule included, is another ticket (P5 had deleted that one too; P7 put it
  back); the three
  fill-the-width copies use `.input--block` (D6); `/review`'s selects go to
  the house size (D3). Not touched: anything server-side, config / schema /
  manifest (no `docs/migrations.md` entry), `sandbox-host/`, any handler /
  value / `aria-label` / `data-testid` / `autoFocus` / `readOnly` /
  `disabled` / `rows` / `type` / `placeholder`.
- **D13 · Acceptance is three measurements, not one (Q13).** See
  "Acceptance" below.

### Treatments (the rule each control gets; the per-control table below assigns them and carries the counts)

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
- Textareas take `textarea.input`'s line-height (`--leading-body-sm`, 1.5)
  where they inherited the body's 1.55, its `8px 10px` padding where they
  had 2px (CardDiffReview's and WorkflowDecisionCard's notes grow 43 → 54px)
  and `resize: vertical` where the UA allowed both.
- `/my-resources`' admin row: the `UserPicker` search inside it already wore
  `.input` (34px); the row's descendant rule now sizes it with the row's
  fields, 28px. `ItemShareManagers`' add field fills its form (218px UA
  width → the form's).
- `.kb-docsearch--inline` gains `min-width: 160px` (the floor `.rvw__search`
  has): with `.input`'s `min-width: 0` it shrank to 20px at 390px wide; on
  base the row overflowed off the left edge instead — both broken, and the
  plan's rule is "overflow is fine, collapse is not".

## The table (derived from the base by a script over the guard's scan; counts come OUT of it)

Of the 126: **27 already wear the class** (some change anyway: the two
`input kb-textarea` drop the modifier, the controls under the three
fill-the-width wrappers and ToolsChecklist's search gain `input--block` —
P1 counts them as it edits them), **99 do not: A 32 · B 53 · C 11 · W 3**.

| File · line | Element · label | Today | Treatment · target |
|---|---|---|---|
| `components/AskUserCard.tsx:334` | `<input>` “補充:${opt.label}” | shared style `noteInput` | **B** input inline-edit + inline flex:1, minWidth:80 — `noteInput` object deleted |
| `components/AskUserCard.tsx:370` | `<input>` “自己回答” | shared style `noteInput` | **B** input inline-edit + inline flex:1, minWidth:80 — `noteInput` object deleted |
| `components/CardDiffReview.tsx:228` | `<textarea>` “What should change?” | inline size only | **A** input input--block |
| `components/ChatListRail.tsx:295` | `<input>` “Rename ${noun}” | class `chat-rail__rename` | **B** input input--block chat-rail__rename — `.chat-rail__rename` → min-height 30 + padding |
| `components/CollectionsChecklist.tsx:53` | `<input>` “collections-search” | inline border | **A** input input--block |
| `components/DomainField.tsx:35` | `<select>`  | class `inline-edit` | **B** input inline-edit |
| `components/DomainField.tsx:55` | `<input>`  | class `inline-edit` | **B** input inline-edit |
| `components/EnvVarsModal.tsx:278` | `<input>` “env-tool-search” | wears .input | **—** already `input` (+ inline `width: 100%` in its grid); unchanged |
| `components/EnvVarsModal.tsx:386` | `<input>` “env-field-${field.name}” | inline border | **A** input input--block (+ inline mono font) |
| `components/EnvVarsModal.tsx:443` | `<input>` “env-cred-${field.name}” | inline border | **A** input input--block (+ inline mono font) |
| `components/EnvVarsModal.tsx:507` | `<textarea>` “env-text” | inline border | **A** input input--block (+ inline mono font) |
| `components/ExportDialog.tsx:323` | `<input>` “export-latest-n” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:357` | `<select>` “export-${end}” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:435` | `<select>` “export-fmt” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:452` | `<select>` “export-aspect” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:500` | `<select>` “export-text-size” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:522` | `<input>` “export-width” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:539` | `<input>` “export-height” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:556` | `<select>` “export-custom-text” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/ExportDialog.tsx:607` | `<input>` “export-${key.replace(/_/g, "-"” | wears .input | **—** already `input` (#823); `export-dialog__field > .input` → `input--block` |
| `components/GroupPicker.tsx:48` | `<input>`  | wears .input | **—** already `input` |
| `components/ItemEnvironmentPanel.tsx:137` | `<input>` “cpu-input” | wears .input | **—** already `input`; `.env-field > .input` → `input--block` |
| `components/ItemEnvironmentPanel.tsx:199` | `<input>` “memory-input” | wears .input | **—** already `input`; `.env-field > .input` → `input--block` |
| `components/ItemForm.tsx:106` | `<input>` “+ add” | inline slot reset | **C** title / fields / description: input input--block (+ minHeight 38, 14px inline); tags: box `input input-group`, field `input-group__field` — `inputStyle` object deleted; title's accent border → `aria-invalid` (D8) |
| `components/ItemForm.tsx:217` | `<input>`  | spread of `inputStyle` | **A** title / fields / description: input input--block (+ minHeight 38, 14px inline, as one shared `fieldSize` const); tags: box `input input-group`, field `input-group__field` — `inputStyle` object deleted; title's accent border → `aria-invalid` (D8) |
| `components/ItemForm.tsx:246` | `<input>`  | shared style `inputStyle` | **A** title / fields / description: input input--block (+ minHeight 38, 14px inline); tags: box `input input-group`, field `input-group__field` — `inputStyle` object deleted; title's accent border → `aria-invalid` (D8) |
| `components/ItemForm.tsx:286` | `<textarea>`  | spread of `inputStyle` | **A** title / fields / description: input input--block (+ minHeight 38, 14px inline); tags: box `input input-group`, field `input-group__field` — `inputStyle` object deleted; title's accent border → `aria-invalid` (D8) |
| `components/ItemShareDialog.tsx:241` | `<select>` “Role for ${g.userId}” | class `inline-edit` | **B** input inline-edit (marginLeft auto stays) |
| `components/ItemShareDialog.tsx:323` | `<select>` “Role for ${groupName(g.groupId” | class `inline-edit` | **B** input inline-edit (marginLeft auto stays) |
| `components/ItemShareManagers.tsx:66` | `<input>` “manager-add” | nothing | **A** input input--block |
| `components/ManageChatsModal.tsx:82` | `<input>` “manage-rename-input-${id}” | class `manage-chats__rename` | **B** search: input input--block manage-chats__search; rename: input input--block inline-edit — `.manage-chats__search` → margin only; `.manage-chats__rename` deleted |
| `components/ManageChatsModal.tsx:225` | `<input>` “Search chats” | class `manage-chats__search` | **B** search: input input--block manage-chats__search; rename: input input--block inline-edit — `.manage-chats__search` → margin only; `.manage-chats__rename` deleted |
| `components/PermissionDialog.tsx:238` | `<select>` “role-${g.userId}” | class `inline-edit` | **B** input inline-edit |
| `components/PermissionDialog.tsx:303` | `<select>` “group-role-${g.groupId}” | class `inline-edit` | **B** input inline-edit |
| `components/SkillHubPickerModal.tsx:103` | `<input>`  | inline border | **A** input input--block |
| `components/TodoPanel.tsx:209` | `<input>` “goal-input” | class `inline-edit` | **B** input inline-edit + inline flex:1 |
| `components/TodoPanel.tsx:329` | `<input>` “todo-add-input” | class `inline-edit` | **B** input inline-edit + inline flex:1 |
| `components/ToolsChecklist.tsx:121` | `<input>` “tools-search” | wears .input | **—** already `input`; its inline `flex:none; width:100%` → `input--block` |
| `components/UserPicker.tsx:50` | `<input>`  | wears .input | **—** already `input` |
| `components/WorkflowDecisionCard.tsx:97` | `<textarea>` “What should change?” | inline size only | **A** input input--block |
| `pages/AppDashboard.tsx:570` | `<select>`  | inline border (a template literal) | **A** input + inline height/minHeight 28, padding, flex:none; active → inline color/borderColor (D3) |
| `pages/GroupsPage.tsx:153` | `<input>` “Search groups” | wears .input | **—** rename: input inline-edit (existing); form: input input--block — `input` object deleted |
| `pages/GroupsPage.tsx:364` | `<input>` “Group name” | class `inline-edit` | **B** rename: input inline-edit (existing); form: input input--block — `input` object deleted |
| `pages/GroupsPage.tsx:626` | `<input>` “Group name” | shared style `input` | **A** rename: input inline-edit (existing); form: input input--block — `input` object deleted |
| `pages/GroupsPage.tsx:635` | `<input>` “Group description” | shared style `input` | **A** rename: input inline-edit (existing); form: input input--block — `input` object deleted |
| `pages/MyResourcesPage.tsx:394` | `<input>` “q-count” | nothing | **B** input (rule `.page .admin-row input` keeps 28px, flex:none) |
| `pages/MyResourcesPage.tsx:404` | `<input>` “q-cpu” | nothing | **B** input (rule `.page .admin-row input` keeps 28px, flex:none) |
| `pages/MyResourcesPage.tsx:415` | `<input>` “8G” | nothing | **B** input (rule `.page .admin-row input` keeps 28px, flex:none) |
| `pages/MyResourcesPage.tsx:424` | `<input>` “50G” | nothing | **B** input (rule `.page .admin-row input` keeps 28px, flex:none) |
| `pages/SanityQuestions.tsx:98` | `<input>` “q-category” | shared style `field` | **A** input input--block — `field` object deleted |
| `pages/SanityQuestions.tsx:105` | `<textarea>` “q-prompt” | shared style `field` | **A** input input--block — `field` object deleted |
| `pages/SanityQuestions.tsx:113` | `<textarea>` “q-expected” | shared style `field` | **A** input input--block — `field` object deleted |
| `pages/SanityTable.tsx:291` | `<select>` “category-filter” | inline border | **B** input inline-edit |
| `pages/SkillHubPage.tsx:128` | `<input>`  | nothing | **B** input (rule `.page-tools > input[type=search]` keeps 32px, flex basis) |
| `pages/WorkCalendarPage.tsx:106` | `<textarea>` “Calendar exceptions” | shared style `box` | **A** input input--block (+ inline mono font) — `box` object deleted |
| `pages/WuiOverviewPage.tsx:210` | `<select>`  | nothing | **B** input (rules `.page-tools …` keep 32px; select flex:none) |
| `pages/WuiOverviewPage.tsx:228` | `<input>`  | nothing | **B** input (rules `.page-tools …` keep 32px; select flex:none) |
| `pages/WuiOverviewPage.tsx:237` | `<select>`  | nothing | **B** input (rules `.page-tools …` keep 32px; select flex:none) |
| `pages/investigation/AgentPanel.tsx:1197` | `<textarea>`  | inline border | **A** input input--block (+ inline height from the seam, resize:none) |
| `pages/investigation/CommandPalette.tsx:115` | `<input>` “Go to file…” | inline slot reset | **W** whitelist (D1) |
| `pages/investigation/FileTree.tsx:600` | `<input>` “Filter files” | inline slot reset | **C** filter: box `input input-group` + `input-group__field`; rename: input (+ inline 22px, 12px) |
| `pages/investigation/FileTree.tsx:995` | `<input>`  | inline border | **A** filter: box `input input-group` + `input-group__field`; rename: input (+ inline 22px, 12px) |
| `pages/investigation/SearchPanel.tsx:137` | `<input>` “Search” | shared style `input` | **C** four rows: box `input input-group` (28px) + `input-group__field` — `fieldWrap` / `input` objects → size only |
| `pages/investigation/SearchPanel.tsx:159` | `<input>` “Replace” | shared style `input` | **C** four rows: box `input input-group` (28px) + `input-group__field` — `fieldWrap` / `input` objects → size only |
| `pages/investigation/SearchPanel.tsx:179` | `<input>` “files to include — e.g. *.md, ” | shared style `input` | **C** four rows: box `input input-group` (28px) + `input-group__field` — `fieldWrap` / `input` objects → size only |
| `pages/investigation/SearchPanel.tsx:187` | `<input>` “files to exclude” | shared style `input` | **C** four rows: box `input input-group` (28px) + `input-group__field` — `fieldWrap` / `input` objects → size only |
| `pages/investigation/TerminalPane.tsx:189` | `<input>` “terminal command” | inline slot reset | **W** whitelist (D1) |
| `pages/kb/AttachmentBar.tsx:87` | `<input>` “rename ${name} to” | class `kb-att__rename` | **B** input kb-att__rename — `.kb-att__rename` → 28px, mono |
| `pages/kb/AutoGenerateCards.tsx:131` | `<input>` “Search sources” | nothing | **B** input — `.kb-cardgen__pickbar input` deleted; dead `.kb-cardgen__proposal …` rule deleted (D12) |
| `pages/kb/CodeConnectionEditor.tsx:65` | `<input>`  | wears .input | **—** already `input` |
| `pages/kb/CodeConnectionEditor.tsx:69` | `<input>` “(default branch)” | wears .input | **—** already `input` |
| `pages/kb/CodeConnectionEditor.tsx:78` | `<input>` “leave blank to keep the curren” | wears .input | **—** already `input` |
| `pages/kb/ContextCardsTab.tsx:284` | `<input>` “Search cards” | class `kb-cards__search-input` | **B** search/title: input input--block kb-cards__…; term: input kb-cards__term — `.kb-cards__search-input` → 28px/12px; `.kb-cards__title` → type only; `.kb-cards__term` → flex/28px |
| `pages/kb/ContextCardsTab.tsx:378` | `<input>` “Title” | class `kb-cards__title` | **B** search/title: input input--block kb-cards__…; term: input kb-cards__term — `.kb-cards__search-input` → 28px/12px; `.kb-cards__title` → type only; `.kb-cards__term` → flex/28px |
| `pages/kb/ContextCardsTab.tsx:400` | `<input>` “Add a term” | class `kb-cards__term` | **B** search/title: input input--block kb-cards__…; term: input kb-cards__term — `.kb-cards__search-input` → 28px/12px; `.kb-cards__title` → type only; `.kb-cards__term` → flex/28px |
| `pages/kb/GraphBrowsePage.tsx:89` | `<input>`  | class `gbr__search` | **B** input gbr__search / gbr__kind / gbr__collection — `.gbr__*` → flex shares only |
| `pages/kb/GraphBrowsePage.tsx:96` | `<input>`  | class `gbr__kind` | **B** input gbr__search / gbr__kind / gbr__collection — `.gbr__*` → flex shares only |
| `pages/kb/GraphBrowsePage.tsx:102` | `<select>`  | class `gbr__collection` | **B** input gbr__search / gbr__kind / gbr__collection — `.gbr__*` → flex shares only |
| `pages/kb/KbChatPanel.tsx:356` | `<textarea>` “Ask the knowledge base…” | class `kb-composer__input` | **C** input-group__field kb-composer__input (box = `.kb-composer`, D7) — `.kb-composer__input` → width/resize/font-size |
| `pages/kb/KbChatsPage.tsx:154` | `<input>` “Rename ${label}” | class `kb-chats__open kb-chats__rename` | **B** input kb-chats__rename — `.kb-chats__open, .kb-chats__rename { padding }` shared; `.kb-chats__rename` → weight only |
| `pages/kb/KbCollectionPage.tsx:570` | `<input>`  | class `kb-colpage__nameedit` | **B** input kb-colpage__nameedit / kb-colpage__descedit — `.kb-colpage__nameedit` → type only; `textarea.kb-colpage__descedit` → measure only |
| `pages/kb/KbCollectionPage.tsx:609` | `<textarea>` “Add a description…” | class `kb-colpage__descedit` | **B** input kb-colpage__nameedit / kb-colpage__descedit — `.kb-colpage__nameedit` → type only; `textarea.kb-colpage__descedit` → measure only |
| `pages/kb/KbCollectionsGrid.tsx:172` | `<input>` “Filter collections…” | nothing | **C** box `input input-group kb-docsearch` + `input-group__field` — `.kb-docsearch` → padding/margin; `.kb-docsearch input` deleted |
| `pages/kb/NewCollectionModal.tsx:198` | `<input>` “https://github.com/owner/repo.” | wears .input | **—** already `input`; `kb-textarea` dropped (D5); `.kb-field .input` → `input--block` |
| `pages/kb/NewCollectionModal.tsx:216` | `<input>` “New collection name…” | wears .input | **—** already `input`; `kb-textarea` dropped (D5); `.kb-field .input` → `input--block` |
| `pages/kb/NewCollectionModal.tsx:227` | `<textarea>` “What lives in this collection?” | wears .input | **—** already `input`; `kb-textarea` dropped (D5); `.kb-field .input` → `input--block` |
| `pages/kb/NewCollectionModal.tsx:250` | `<input>` “(default branch)” | wears .input | **—** already `input`; `kb-textarea` dropped (D5); `.kb-field .input` → `input--block` |
| `pages/kb/NewCollectionModal.tsx:259` | `<input>` “for a private repo” | wears .input | **—** already `input`; `kb-textarea` dropped (D5); `.kb-field .input` → `input--block` |
| `pages/kb/QualityRubricEditor.tsx:67` | `<textarea>`  | inline border | **A** input input--block (+ minHeight) |
| `pages/kb/ReviewDrawer.tsx:119` | `<input>`  | nothing | **A** title/body/answer: input input--block; add-key: input rvw-drawer__addkey — `.rvw-drawer__field input, textarea, .rvw-drawer__answer` deleted; `.rvw-drawer__addkey` → 24px/xs |
| `pages/kb/ReviewDrawer.tsx:127` | `<textarea>`  | nothing | **A** title/body/answer: input input--block; add-key: input rvw-drawer__addkey — `.rvw-drawer__field input, textarea, .rvw-drawer__answer` deleted; `.rvw-drawer__addkey` → 24px/xs |
| `pages/kb/ReviewDrawer.tsx:148` | `<input>`  | class `rvw-drawer__addkey` | **B** title/body/answer: input input--block; add-key: input rvw-drawer__addkey — `.rvw-drawer__field input, textarea, .rvw-drawer__answer` deleted; `.rvw-drawer__addkey` → 24px/xs |
| `pages/kb/ReviewDrawer.tsx:263` | `<textarea>`  | class `rvw-drawer__answer` | **A** title/body/answer: input input--block; add-key: input rvw-drawer__addkey — `.rvw-drawer__field input, textarea, .rvw-drawer__answer` deleted; `.rvw-drawer__addkey` → 24px/xs |
| `pages/kb/ReviewPage.tsx:140` | `<input>`  | nothing | **C** search: box `input input-group rvw__search` + slot; selects: input (34px, D3) — `.rvw__search` → flex/gap; `.rvw__search input` deleted; `.rvw__toolbar > select { flex:none }` |
| `pages/kb/ReviewPage.tsx:149` | `<select>`  | class `inline-edit` | **A** search: box `input input-group rvw__search` + slot; selects: input (34px, D3) — `.rvw__search` → flex/gap; `.rvw__search input` deleted; `.rvw__toolbar > select { flex:none }` |
| `pages/kb/ReviewPage.tsx:163` | `<select>`  | class `inline-edit` | **A** search: box `input input-group rvw__search` + slot; selects: input (34px, D3) — `.rvw__search` → flex/gap; `.rvw__search input` deleted; `.rvw__toolbar > select { flex:none }` |
| `pages/kb/ReviewPage.tsx:178` | `<select>`  | class `inline-edit` | **A** search: box `input input-group rvw__search` + slot; selects: input (34px, D3) — `.rvw__search` → flex/gap; `.rvw__search input` deleted; `.rvw__toolbar > select { flex:none }` |
| `pages/kb/TuneParsingModal.tsx:190` | `<input>`  | shared style `inputStyle` | **A** input input--block — `inputStyle` object deleted |
| `pages/kb/TuneParsingModal.tsx:247` | `<textarea>`  | spread of `inputStyle` | **A** input input--block — `inputStyle` object deleted |
| `pages/kb/WikiBrowser.tsx:149` | `<textarea>` “e.g. Group pages by reflow zon” | shared style `ta` | **A** input input--block — `ta` object deleted |
| `pages/kb/WikiBrowser.tsx:161` | `<textarea>` “e.g. Lead with a one-line summ” | shared style `ta` | **A** input input--block — `ta` object deleted |
| `pages/kb/WikiCorrectionDialog.tsx:181` | `<input>`  | wears .input | **—** already `input`; `kb-textarea` dropped (D5) |
| `pages/kb/WikiCorrectionDialog.tsx:199` | `<textarea>`  | wears .input | **—** already `input`; `kb-textarea` dropped (D5) |
| `pages/kb/WikiCorrectionDialog.tsx:210` | `<input>`  | wears .input | **—** already `input`; `kb-textarea` dropped (D5) |
| `renderers/SheetGrid.tsx:239` | `<input>`  | class `sheet-cell` | **W** whitelist (D1) |
| `renderers/entity/HealthView.tsx:106` | `<select>` “filter level” | class `ev-select` | **B** input ev-select — `.ev-select, .ev-viewpanel__range` → 28px, flex 0 1 auto |
| `renderers/entity/HealthView.tsx:114` | `<select>` “filter type” | class `ev-select` | **B** input ev-select — `.ev-select, .ev-viewpanel__range` → 28px, flex 0 1 auto |
| `renderers/entity/HealthView.tsx:125` | `<select>` “filter field” | class `ev-select` | **B** input ev-select — `.ev-select, .ev-viewpanel__range` → 28px, flex 0 1 auto |
| `renderers/entity/TableView.tsx:212` | `<select>` “batch ${f.name}” | class `ev-select` | **B** input ev-select |
| `renderers/entity/TableView.tsx:272` | `<select>` “filter ${c}” | class `ev-select` | **B** input ev-select |
| `renderers/entity/ViewSettingsPanel.tsx:74` | `<select>` “group by” | class `ev-select` | **B** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:97` | `<select>` “sort field ${i + 1}” | class `ev-select` | **B** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:224` | `<input>` “day starts” | nothing | **C** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:233` | `<input>` “day ends” | nothing | **C** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:250` | `<select>` “colour by” | class `ev-select` | **B** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:278` | `<select>` “weekday format” | class `ev-select` | **B** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:287` | `<select>` “day of month” | class `ev-select` | **B** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/ViewSettingsPanel.tsx:305` | `<select>` “people display” | class `ev-select` | **B** selects: input ev-select; range: box `input input-group ev-viewpanel__range` + two `input-group__field` — `.ev-viewpanel__range input[type=time]` → flex/mono only |
| `renderers/entity/roleWidget.tsx:80` | `<select>`  | class from a prop | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/entity/roleWidget.tsx:120` | `<select>`  | class from a prop | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/entity/roleWidget.tsx:185` | `<input>` “${name} start” | class from a prop | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/entity/roleWidget.tsx:196` | `<input>` “${name} end” | class from a prop | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/entity/roleWidget.tsx:234` | `<select>`  | class from a prop | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/entity/roleWidget.tsx:302` | `<input>`  | class `ev-field` | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/entity/roleWidget.tsx:356` | `<input>`  | class `ev-field` | **B** input ev-field (literal; `className` prop removed, D11) — `.ev-field` → 28px, flex 0 1 auto, hover; `.ev-field:disabled` kept (D9); tbody rule gains min-height 26 |
| `renderers/wui/WuiView.tsx:1118` | `<input>` “Page address” | inline size only | **A** input (+ inline 28px) |

### Scoped rules that draw a control's chrome on the base (23 rules, 66 declarations — the guard's CSS check lists exactly these), and what each keeps

| Sheet | Rule (base) | Chrome it draws today | After |
|---|---|---|---|
| `base.css` | `.inline-edit` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `flex: none; height: 26px; min-height: 26px; padding: 0 8px` (D3, D4) |
| `chat-rail.css` | `.chat-rail__rename` | border: 1px solid var(--accent); border-radius: var(--radius-card); background: var(--white) | → `min-height: 30px; padding: 0 var(--space-8)` (autofocused; `.input:focus` draws the accent) |
| `entity-views.css` | `.ev-field` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `flex: 0 1 auto; height: 28px; min-height: 28px; max-width; padding; transition`; `:hover` border-color and `:disabled` stay (D9); tbody rule gains `min-height: 26px` |
| `entity-views.css` | `.ev-select, .ev-viewpanel__range` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `flex: 0 1 auto; height: 28px; min-height: 28px; padding` (the range is also an `.input-group` box) |
| `item-environment.css` | `.item-environment .input[aria-invalid="true"]` | border-color: var(--err) | → hoisted to base.css as `.input[aria-invalid="true"]` (D8); the sheet keeps the note's colour only |
| `kb.css` | `.kb-colpage__nameedit` | border: 1px solid var(--accent); border-radius: 6px; background: var(--white) | → display type + padding only |
| `kb.css` | `.kb-colpage__descedit` | border: 1px solid var(--accent); border-radius: 6px; background: var(--white) | → `textarea.kb-colpage__descedit`: margin / width / max-width / padding only |
| `kb.css` | `.kb-docsearch` | background: var(--white); border: 1px solid var(--paper-3); border-radius: var(--radius-btn) | → `padding: 0 12px; margin-bottom: 12px` (the box wears `input input-group`); `.kb-docsearch input` deleted (slot) |
| `kb.css` | `.kb-cards__search-input` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → `min-height: 28px; padding: 0 6px; font-size: 0.75rem` |
| `kb.css` | `.kb-cards__title` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → `font-size: 0.9375rem; font-weight: 600` |
| `kb.css` | `.kb-cards__term` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → `flex: 1 1 120px; min-width: 120px; min-height: 28px; padding: 0 8px` |
| `kb.css` | `.kb-cardgen__body > input, .kb-cardgen__todo, .kb-cardgen__proposal textarea, .kb-cardgen__proposal input` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → deleted (dead: no tsx uses these classes) (D12) |
| `kb.css` | `.kb-cardgen__pickbar input` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → deleted (the input wears `input`) |
| `kb.css` | `.rvw__search` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `gap: 6px; flex: 1 1 220px; min-width: 160px` (the box wears `input input-group`); `.rvw__search input` deleted (slot); `.rvw__toolbar > select { flex: none }` added |
| `kb.css` | `.rvw-drawer__field input, .rvw-drawer__field textarea, .rvw-drawer__answer` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → deleted (all wear `input input--block`); its `:disabled` pair → base.css (D9) |
| `kb.css` | `.rvw-drawer__addkey` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → `flex: 1 1 100px; min-width: 100px; min-height: 24px; padding: 0 8px; font-size: var(--text-xs)` |
| `kb.css` | `.kb-att__rename` | border: 1px solid var(--accent); border-radius: var(--radius-btn); background: var(--paper) | → `min-height: 28px; padding: 0 8px; font-family: mono; font-size: small` |
| `kb.css` | `.gbr__search, .gbr__kind, .gbr__collection` | border: 1px solid var(--paper-3); border-radius: var(--radius-chip); background: var(--white) | → flex shares only (`.gbr__search { flex: 2 1 240px }` …) |
| `my-resources.css` | `.page > .page-tools > input[type="search"]` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `flex: 1 1 200px; height: 32px; min-height: 32px; padding; font-size: small` |
| `my-resources.css` | `.page > .page-tools select` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `flex: none; height: 32px; min-height: 32px; padding; font-size: small` |
| `my-resources.css` | `.page .admin-row input` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--paper) | → `flex: none; height: 28px; min-height: 28px; padding: 0 8px` |
| `topic-hub.css` | `.manage-chats__search` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn); background: var(--white) | → `margin: 12px 0` |
| `topic-hub.css` | `.manage-chats__rename` | border: 1px solid var(--paper-3); border-radius: var(--radius-btn) | → deleted (wears `input input--block inline-edit`) |

Slot resets replaced by `.input-group__field` (base.css): `.kb-docsearch input`,
`.rvw__search input`, `.ev-viewpanel__range input[type="time"]`'s border /
background / padding (its mono type and per-end focus ring stay),
`.kb-composer__input`'s border / background / outline (its width / resize /
font-size stay), and the inline resets on `SearchPanel`'s `input` object,
`FileTree`'s filter and `ItemForm`'s tag input. Their focus companions go
with them — `.kb-docsearch:focus-within`, `.ev-viewpanel__range:focus-within`,
`.kb-chats__rename:focus` — the box's accent border and ring are base.css's.
`.kb-docsearch--inline` (on the base already: `flex: 0 1 280px; margin: 0`)
gains `min-height`-style protection as `min-width: 160px`.

Fill-the-width copies replaced by `.input--block` (D6): `.kb-field .input`
(11 controls in NewCollectionModal / WikiCorrectionDialog /
CodeConnectionEditor), `.export-dialog__field > .input` (8; the tabular
figures stay as `.export-dialog__field > input, … > select`),
`.item-environment .env-field > .input` (2, same).

## Phases (one commit each; flat numbers; the guard is RED from P1 until the last conversion)

- **P1** `base.css`: `textarea.input`, `.input--block`, `.input-group` +
  `__field`, `.input:disabled`, `.input[aria-invalid]` (+ the focus change of
  D10), `.inline-edit` reduced to its size with `flex: none`; `.kb-textarea`
  and the three fill-the-width copies deleted (their users updated);
  `input-class.test.ts` rewritten as the global guard (below), red on this
  base with the bare controls listed by `file:line`. P1 corrected this plan
  as it edited: the fill-the-width counts are 11 + 8 + 2 (the plan had said
  12 / 10 / 1), and `.export-dialog__field`'s tabular figures moved to
  `> input, > select` because the guard forbids `.input` in another sheet.
  On the base the guard's CSS check cannot list the 23 rules by itself (its
  modifier list is derived from tags that do not wear `input` yet); the
  count came from a script with the plan's class list.
- **P2** `components/` + the `.page-tools` pair (SkillHubPage, WuiOverviewPage).
- **P3** `pages/` outside `kb/` + `pages/investigation/`.
- **P4** `pages/kb/` + the `kb.css` scoped rules.
- **P5** `renderers/entity/` + `entity-views.css` + `WuiView`; the guard goes
  green; every row of the table is checked against the swept tree (a script
  walks each file's controls in base order and compares the assigned
  treatment with the actual `className`: 126 rows, 0 mismatches).
- **P6** Acceptance (below); anything it finds is fixed in P6 with a test
  that pins it.
- **P7** Review round 1 (four lenses on `git archive` snapshots; every
  finding below is pinned by a test or a probe): the guard reads the JSX
  through the TypeScript compiler's parser instead of a regex scan (the scan
  could be made to swallow the control after a tag holding a `// don't`
  comment or a regex literal with a quote — never live, but the reader is
  the guard's core), finds a slot's box by walking its JSX ancestors (the
  per-file grep passed a renamed composer, a stray slot, and SearchPanel's
  rows 2–4 losing `input`), reads `style` consts (a border in `SearchPanel`'s
  shared `input` object passed), and refuses an empty whitelist reason;
  TodoPanel's and AskUserCard's fill rows pin their `flex: 1` (removing it
  passed 47 tests while "Set goal" left the panel again in Chromium);
  `.ev-field`'s 28px is pinned; the tight-row probe gets a criterion that
  can fail and a positive control; D10 is rewritten to what Chromium does
  and the range's third ring and the invalid field's second ring are
  removed; `.kb-docsearch--inline` gets its floor; `.kb-cardgen__todo` is
  restored; two indentation slips; this plan's counts and the acceptance
  record.
- **P8** Review round 2 (veracity / defect / regression on P7's snapshot; no
  regression, no product defect; all three judged a further round not
  worth running): the reader resolves a `style` const by its ONE
  declaration in the file (a sibling component's own `const input` carrying
  a border was read as the module's), refuses a spread on the tag, reads a
  quoted key and refuses a computed one, holds a box to the inline-chrome
  rule, and fails on a file the parser could not read; `.kb-composer`'s
  box / ring and the doc search's floor are pinned (deleting either passed
  every test); the `.ev-field` `height` pin no longer matches the tail of
  `min-height`; the fill rows pin `flexGrow` exactly `"1"`; the record's
  "After P7" says what the differential measured and what a focus probe did.

## Test plan (red first; targeted only)

- `web/src/styles/input-class.test.ts` — the reader of Ground truth: every
  `<input|textarea|select` JSX element of every `.tsx` under `src/`, through
  `typescript`'s parser (P7; the first reader was a regex scan). Every
  control wears the class as a literal or sits in the whitelist; a
  whitelist entry must carry a reason and match exactly one bare control; a
  computed class is refused; a dressed control's `style` (an object literal
  or the ONE `const` of its name in the file — a name declared twice, a
  `let`, a spread on the tag, a computed key: refused) carries no border
  (shorthand or longhand), radius, background, box-shadow or outline, and a
  box (any element wearing `input`) is held to the same; a slot has a JSX
  ancestor that wears `input input-group` or is the composer; no CSS rule
  whose selector names a control element or a class that rides beside
  `input` on any element (derived from the sources, never listed by hand)
  declares a chrome (`0` / `none` / `transparent` and state selectors
  excepted; base.css's theme reset excepted by exact selector; a rule keyed
  on an id, a data attribute or a universal child, and a state rule drawing
  a whole second chrome, are not seen — none exists today, said in the
  comment); no sheet but base.css names `.input`. The reader's edges are
  pinned on a temp-dir fixture: a docblock's `<select>`, a JSX comment, a
  placeholder saying `src/**` or `e.g. /*.md`, a `>` in a string, a computed
  class, an apostrophe in a trailing comment inside a tag, a quote in a
  regex literal, a slot in a box / in a box without `input` / outside any
  box, a `style` const with and without a border, a conditional style, a
  quoted and a computed key, a spread on the tag, a name declared in two
  components, a `let`, a box with a border, a file the parser cannot read
  (a failure, not a pass); every reported line is checked to hold its tag.
  `.kb-composer`'s box, its ring and `.kb-docsearch--inline`'s floor are
  pinned by regex (the one box the slot rule accepts by name was otherwise
  unguarded). base.css's companions are
  pinned by regex (`textarea.input` padding, `.input--block`, `.input-group`
  + slot, `.inline-edit`'s `flex: none` and `min-height`, `.input:disabled`,
  `.input[aria-invalid]` with no second focus ring, no `outline` on
  `.input`).
- Mutation probes, on file copies, before the push: each guarantee above is
  broken once and exactly its test must redden — recorded here with the
  mutation and the test that caught it.
- `entity-views.test.ts`: the range's ends are slots and the range's rule
  draws no chrome; the range's box does not add a third ring; `.ev-field`
  keeps `height: 28px` AND `min-height: 28px`, the table's cells `26px` of
  both (else `.input`'s 34 / `.ev-field`'s 28 would win); `.ev-field:disabled`
  keeps the default cursor.
- `item-environment.test.ts`: the `aria-invalid` rules are base.css's and the
  sheet keeps no copy; `ItemForm.test.tsx`: `aria-invalid` on the empty
  submit, gone on the first keystroke.
- Tests that pinned an inline `minWidth: 0` (`TodoPanel.test.tsx`,
  `SearchPanel.layout.test.tsx`) assert the class instead, and the guard pins
  `.input { flex: 1; min-width: 0 }`; the rows that FILL (TodoPanel's two,
  AskUserCard's two) also pin their own `flex: 1` — `.inline-edit` is a
  chip's width, so the tag's `flex: 1` is the growing half and the shrinking
  half at once (D4) — each link reddens on its own mutation.
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
   with long neighbours — overflow is fine, collapse is not. The criterion:
   a chip-sized control is exactly as wide in the 240px row as in a 2000px
   one; a filling one keeps at least its placeholder's first word (60px).
   `scrollWidth` is not it — for a form control it equals `clientWidth`, so
   "width ≥ scrollWidth" is always true (the first record used it). A
   positive control — the same page with `.inline-edit { flex: 0 1 auto }`,
   the shape D4 rejected — must be reported as collapsed, or the criterion
   is empty.

## Review and CI

Push as soon as the seconds-long gates pass (backup + draft PR), cancel the
run the push starts. Four lenses (conformance / veracity / defect /
regression) in one parallel pass, each measuring on its own `git archive`
snapshot outside the repo (the worktree-per-reviewer of the first attempt was
refused by the harness and two reviewers probed in the author's tree). A fix
that replaces a mechanism gets another round; three rounds is the budget. CI
runs on the final sha only after a round comes back clean — if reviewers
cannot be launched (the account's spend limit), stop and report; do not run
CI in their place. Round 1 (2026-09-21) ran all four on snapshots; its
findings are P7. P7 replaced the guard's reader (a mechanism), so round 2
(veracity / defect / regression) verified P7: no regression (the two readers
agree on all 126 controls of base and HEAD, and on the fixtures where they
differ the new one is right), no product defect, three low findings inside
the fix itself — P8, which adjusts the reader and adds pins, replaces
nothing. All three lenses judged a third round not worth running as a
sweep; the one question they left (do P8's pins redden on their mutations)
is answered by the probe table above. CI runs on P8's sha.

## Acceptance record (2026-09-21, worktree build on 127.0.0.1:8263, Chromium 1148, 1280×900, light AND dark)

### Mutation probes (before the push; each restored by file copy, the tree clean after)

Nineteen, each reddening exactly the test that guards it (`probe_guard2.sh` in
the job dir; the control run after all restores is green):

| # | Mutation | Reddened |
|---|---|---|
| M01 | a control loses `input` | guard: dresses every text control |
| M02 | a control's class becomes `{draft ? … : …}` | guard: dresses every text control (computed) |
| M03 | the whitelisted terminal line gains `input` | guard: whitelist entry stale (+ inline copy) |
| M04 | inline `backgroundColor` on a dressed control | guard: no inline copy |
| M05 | `.ev-field { background-color }` | guard: no scoped re-draw |
| M06 | a class invented on the spot beside `input`, with a `box-shadow` | guard: no scoped re-draw (the list is derived) |
| M07 | `.kb-field .input { … }` in kb.css | guard: no other sheet names `.input` |
| M08 | `.ev-select { border-top }` beside an allowed `border-color` | guard: no scoped re-draw |
| M09 | `.inline-edit` loses `flex: none` | guard: companions |
| M10 | `.input` loses `min-width: 0` | guard: companions |
| M11 | `outline: none` back on `.input` | guard: companions (+ item-environment: the invalid field's focus is the ring) |
| M12 | the table's `min-height: 26px` removed | entity-views: keeps the table calm |
| M13 | `.ev-field:disabled` removed | entity-views: keeps the table calm |
| M14 | ItemForm's `aria-invalid` removed | ItemForm: says the empty title with aria-invalid |
| M15 | TodoPanel's goal row loses `input` | TodoPanel: rows shrink (both) + guard |
| M16 | SearchPanel's row loses `input input-group` | SearchPanel.layout: row shrinks (+ guard: its slot is loose) |
| M17 | item-environment.css copies `aria-invalid` back | guard: no other sheet names `.input` + item-environment (two) |
| M18 | one range end loses the slot | entity-views: skins every control + guard |
| M19 | `.input-group__field` loses `border: 0` | guard: companions |

### 1 · HEAD in the browser (numbers derived from the drivers' `report.json`)

Order matters: build, THEN start the app — a rebuild under a running app
replaces `web/dist` and every SPA route answers 404, and the driver then
reports "0 controls, 0 not house" for every page, which is nothing at all.
(That happened once in this run; the pages were re-measured on a restarted app.)

- 20 routes as they load (`/`, `/a/playground`, `/a/playground/new`, a
  playground item, `/a/pm`, a PM project, `/kb/collections`, a collection +
  its cards / wiki / review tabs, `/kb/graph`, `/kb/chats`, `/groups`,
  `/work-calendar`, `/my-resources`, `/wui`, `/skill-hub`, `/review`,
  `/diagnostics`): 26 controls in light, 26 in dark, **0 not house**; eight
  of the routes have no text control when they load.
- 20 interactive states (the item's Files and Search panes; the Env, Tools,
  Share, Export and Sandbox modals; the PM project's view-settings popover,
  the table's and the board's New-issue form, the table with one row created,
  the health view; a new KB chat; the collection title rename; a new context
  card; the diagnostics model matrix with its question form; `/wui`,
  `/skill-hub`, `/my-resources`, `/kb/graph`): 82 controls in light, 84 in
  dark (the row created in the light pass shows the table's two filter
  selects in the dark pass), **2 flagged in each, both by design** — the KB
  composer's textarea is a slot in `.kb-composer`'s own accent box (D7), and
  `ime-text-area` is Monaco's. Everything else is house.
- Read by eye: the create-item form's title / tags / description at 38px
  beside the 38px Owner box; `/review`'s toolbar one height in dark; the PM
  view-settings popover in dark with the time range as ONE box and a row in
  the timeline.

### 2 · Base vs HEAD differential (the same 26 harness cases, 77 controls + their rows, both schemes)

The stylesheets of `72903920` and of this branch under one markup, each
control dressed as its own tsx dresses it on either side; every difference
read out and matched:

- **Nothing outside "Deliberately not unified".** Surfaces `--paper` /
  `--paper-2` → `--white` (admin row, AskUserCard, cards, drawer, env, groups,
  pickbar, rubric, sanity, tune, work calendar); radii 4 / 8 / 0 → 6 (chat
  rail rename, FileTree, graph filters, rubric, tune, address bar, the
  managers field); type 14 → 13 where no size rule keeps it (the same set
  plus `/review`'s toolbar); heights to 34 where only padding had set them
  (drawer 38 → 34, env 32.6 / 35.7 → 34, groups 39.7 → 34, tune 37.5 → 34,
  the collections search 30 → 34, the skill picker 32 → 34) and to the
  compact size where the row is dense (AskUserCard 29.5 → 26, sanity's filter
  29 → 26, ManageChats' rename 28 → 26, the attachment rename 30.6 → 28, the
  add-key 25 → 24, the address bar 32 → 28 = the button beside it);
  `/review`'s selects 26 → 34 with their box 32 → 34 (D3); the graph filters'
  2:1:1 shares finally realised (270 / 210 / 175 → 288 / 184 / 184) now that
  `min-width: 0` lets them; the rest-state accent border gone from the title
  (D8) and the autofocused renames; keyboard focus `outline: none → solid`
  on the nine harness controls that had switched it off (D10).
- **Unchanged, as the plan says they must be**: the table's cells 26px with
  the default cursor and 0.6 opacity when disabled; the time range 28px and
  its two ends; `.kb-field`'s controls 34px full-width; the App dashboard's
  filters 28px; the KB chats rename the same 46.1px as the row button it
  replaces; TodoPanel's rows fill and shrink; `.page-tools` 32px; the admin
  row 28px.
- One non-control differs in dark only: the button beside the WUI address
  bar reads `rgb(236, 234, 227)` on base and `rgb(235, 233, 226)` here — a
  1/255 rendering difference on an element this change does not touch.

### 3 · Tight-row probe (240px rows with long neighbours, base vs HEAD; re-run at P7 with a criterion that can fail)

The first record's criterion ("width ≥ `scrollWidth`") was empty — for a
form control the two are equal, and the veracity lens showed the D4 collapse
(96 → 18px) passing it. `probe-tight3.cjs` measures each control in a
2000px row (its intrinsic width) and in a 240px row: a chip-sized control
(`flex: none`) must not lose a pixel; a filling one keeps ≥ 60px. Seven rows
(the Share role select, the sanity category filter, the App dashboard's
filter, the KB collections doc search beside its three buttons, the cards'
term adder among chips, TodoPanel's goal row, SearchPanel's find row):

- **HEAD: 0 collapsed.** role 96 = 96, sanity 127 = 127 (13px type; base
  134), dashboard 122 = 122 — each overflowing its 240px row by 304 / 126 /
  82px, as base does; the doc search 280 → 160 (its new floor; base 211.8,
  the row's min-content), the term adder and the find row 240 (they wrap or
  fill), the goal row 121.8 on both sides.
- **Positive control** (the same page with `.inline-edit { flex: 0 1 auto }`,
  the shape D4 rejected): role and sanity **collapse to 18px** and are
  reported so — the criterion bites.

### After P7 and P8

P7 changed no rest-state chrome. The differential re-run on its stylesheets
(`measurements.json`, 123 entries per scheme: 77 controls + 46 rows / boxes)
has 80 differing entries in light and the same 80 in dark — 63 of the 77
controls and 17 rows / boxes, with 9 focus-outline changes among the
controls; the one dark-only 1/255 difference of the first run is gone. The
differential records focus on controls only, so the three focus claims were
measured separately (`focus-probe.cjs`, this branch's stylesheets vs the
base's, real Chromium): Tab into the PM range's start → the end's inset
1px ring and the box's accent border, the box's outline `none` (two
indicators; on P6 the box's outline was `solid 2px`, a third); click an
invalid `.input` → border `--err`, outline `solid 2px`, `box-shadow: none`;
click or Tab into the KB composer's textarea → the slot's outline `none`,
the box's `solid 2px` (on base: nothing changed on screen). The HEAD-in-
browser numbers above are from the P6 build; the P7 additions — the doc
search's floor, the composer's ring — are measured in the tight-row probe
and the focus probe. P8 changed tests and the guard only.

Round 2's probes (P8; `probe_guard4.sh`, each restored): a sibling
component's `const input` with a border, a `{...{ style }}` spread on a
control, a quoted `"border"` key, a computed `["boxShadow"]` key, a box
with an inline border → "no inline copy"; the `.kb-composer` rule, its ring,
the doc search's floor deleted → "keeps the composer's box"; `.ev-field`'s
`height` alone deleted → entity-views; a fill row at `flex: 10` and at
`flex: none` → TodoPanel. Eleven, each on its own guard; the control run
green.

### Not reached on this instance

The default user is not an admin and there were no grants, proposals,
findings, published skills or deployed pages: the `/my-resources` admin row,
the New-group form, the `.page-tools` search / selects on `/wui` and
`/skill-hub`, the Share / Permission role selects, the review drawer,
`AskUserCard`, the chat rail / KB chats / attachment renames,
`ManageChatsModal`, `TuneParsingModal`, the wiki guidance form, `WuiView`'s
address bar, `SkillHubPickerModal`, `HealthView`'s filters (they render only
with findings), the table's cells in the live app. Every one of them is in
the differential and the tight-row probe above; what is unverified there is
their page's layout around them, not the control.
