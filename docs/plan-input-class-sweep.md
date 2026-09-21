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
| `components/EnvVarsModal.tsx:278` | `<input>` “env-tool-search” | wears .input | **—** input input--block (+ inline mono font) |
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
| `components/ItemForm.tsx:217` | `<input>`  | spread of `inputStyle` | **A** title / fields / description: input input--block (+ minHeight 38, 14px inline); tags: box `input input-group`, field `input-group__field` — `inputStyle` object deleted; title's accent border → `aria-invalid` (D8) |
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
| `pages/AppDashboard.tsx:570` | `<select>`  | inline size only | **A** input + inline height/minHeight 28, padding, flex:none; active → inline color/borderColor (D3) |
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
`FileTree`'s filter and `ItemForm`'s tag input.

Fill-the-width copies replaced by `.input--block` (D6): `.kb-field .input`
(12 controls), `.export-dialog__field > .input` (10, keeps its
`font-variant-numeric`), `.item-environment .env-field > .input` (1, same).

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
  green; the scan's counts are checked against the table (A 32 · B 53 ·
  C 11 · W 3).
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
