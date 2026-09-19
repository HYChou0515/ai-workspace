# Plan — the item's Sandbox modal, redrawn from the parts the app already has

Reported 2026-09-19: "沙箱的 modal 超級醜" and "你的 text input 看起來超級廉價".
Confirmed in real Chromium on master `e98ae9cb` with a running sandbox and a
per-user quota configured (screenshots in the PR). The user asked only that the
result be good and left the details to me; every decision below is mine and can
be vetoed by looking at the result.

## What is wrong (measured)

- **Bare text button.** "Close sandbox" is a `<button>` with no class; the global
  reset (`base.css:36`) strips UA chrome, so it reads as a centred caption.
  `/my-resources` draws the same action as `.btn[data-variant="secondary"]`.
- **Unlabelled numbers.** "Running   1 · 512.0 MB" — cores · memory, unsaid.
  "Your total 1 / 6" — CPU only, unnamed; memory total absent.
- **UA inputs.** `item-environment.css:65` gives the two inputs only
  `width: 6rem; tabular-nums` — no border, radius, height or padding of their
  own, so they draw with the browser's inset grey chrome. Every other input in
  the app sets the house chrome itself: `.kb-input` (kb.css:611, 16 uses in 8
  files) or an inline copy (`border: "1px solid var(--paper-3)"` — 113
  occurrences in 57 tsx files). There is no shared class, which is how a
  field can ship with none.
- **Two field widths.** CPU 6rem, memory full width; value and origin text
  glued on one line ("1Default (from your current quota)").
- **Save on blur.** The modal's own header comment (`ItemEnvironmentModal.tsx`)
  spends 30 lines on why blur-to-save makes Escape/✕ uneven across browsers and
  why `useDirtyClose` cannot be used, and concludes "what would actually fix
  it is a Save button". The user agreed to that direction.
- **Frame.** 420px, 12px padding, a 14px title with a gear icon and a ✕; the
  Tools modal beside it is 480px, `<strong>` title + 12px lede, `.btn`
  Cancel/Save footer, no ✕.

## Decisions (mine — the user asked for the result, not the details)

1. **Frame = `ToolsPickerModal`'s.** `ModalShell width={480}`, `<strong>` title
   `itemenv.heading`, lede `itemenv.tip` (already the button's tooltip), footer
   Cancel / Save (`tools.cancel` / `tools.save` — the same words), no gear, no
   ✕. `useDirtyClose(dirty, onClose)` wraps Escape and Cancel. A viewer without
   `change_permission` gets a single "Close panel" secondary button instead.
2. **Save model.** The modal owns the two drafts (cpu string, memory string).
   `dirty` = draft ≠ stated (measured against what the modal opened with, per
   #779). Save = ONE `setSize` with both dimensions (the route replaces both).
   Save is enabled only when dirty, `canEdit`, not running, not saving. "Back to
   default" clears the draft (pending Save, which then sends `null`). A refused
   save keeps the modal open and shows `itemenv.saveFailed` above the footer.
   Server semantics unchanged: >0 / ceilings refused with 422, over-quota values
   accepted and clamped — the clamp note shows after the round trip as today.
3. **Status row = `/my-resources`'s live row.** `.live-dot` (green; grey when
   idle) + "Running" / "Not running" + detail "1 core · 512.0 MB" (the
   `resources.live.cores*` strings) + right-aligned `.btn` secondary sm
   "Close sandbox" (`itemenv.close`) when running and `canEdit`; the hint
   `itemenv.close.hint` as a `.detail` line under the row. No confirm dialog —
   `/my-resources` has none either.
4. **Size = two labelled fields side by side.** A 2-column grid (1 column under
   480px): `CPU (cores)` (a new key, `itemenv.field.cpu`; the tile keeps
   `resources.gauge.cpu`) and `Memory` (`resources.memory`); each field:
   `<label>` → `<input class="input">` (number step 0.5, min 0.5 / text
   placeholder in the SERVER's spelling, `toSizeString(effective)` = `512M`
   — round 1 caught the display spelling `512.0 MB` there, which `parse_size`
   refuses) → helper line: effective value +
   origin (`itemenv.size.default` / `itemenv.size.stated` + "Back to default"
   link-button) → clamp note (`…clamped.quota/app`) → `itemenv.unenforced`
   where the backend applies no ceiling (then no input, as today). Read-only:
   inputs disabled + `itemenv.readonly`.
5. **Your total = two `Gauge` tiles in a `.stat-row`**: CPU `cpuInUse / cpu`,
   Memory `memoryInUse / memoryBytes` (`formatBytes`). Not the sandbox COUNT —
   that is per person, not about this item, and lives on `/my-resources`.
6. **Shared parts, not copies.** `Meter` exists twice (MyResourcesPage,
   ItemEnvironmentPanel) and `Gauge` is page-private → `components/Gauge.tsx`
   exporting both; their CSS (`.gauge`, `.summary`, `.gauge-label`,
   `.gauge-value`, `.meter`, `.meter-fill`, `.stat-row`) moves to
   `styles/gauge.css` unscoped and the `.page`-prefixed copies in
   `my-resources.css` go. `.live-dot` likewise.
7. **`.input` is the house input.** New rule in `base.css` with `.kb-input`'s
   values (34px, `--paper-3` border, `--radius-btn`, `--white`, `0 10px`). The
   sandbox fields, the Tools search box, and every `.kb-input` use (16) switch
   to it; `.kb-input` is deleted (two names for one rule drift). The 113 inline
   copies are NOT swept here — a separate ticket; this PR only stops the files
   it touches from carrying one (a source guard, see tests).

## Deliberately not doing

- No change to what is saved or how the server resolves it.
- No confirm on "Close sandbox" (parity with `/my-resources`).
- The header "Sandbox" button being hidden at 390px is a different report.
- The 113 inline input chromes outside the touched files.

## Phases (one commit each)

- **P1** `.input` in `base.css`; `.kb-input` → `.input` everywhere; Tools search
  box uses it. Guard test. No visual change intended for KB (same values).
- **P2** `components/Gauge.tsx` (`Meter`, `Gauge`) + `styles/gauge.css`;
  `MyResourcesPage` and the panel use them; the two private copies go.
  `MyResourcesPage.test.tsx` keeps every case (one import line moves, since
  `formatAgainstLimit` left the page).
- **P3** The modal: Tools frame, drafts + dirty + Save/Cancel + `useDirtyClose`,
  read-only footer, saveFailed placement. Panel becomes presentational (draft
  in, `onDraft` out), status row, two labelled fields, two tiles.
- **P4** Live check (below): one finding fixed in it — at 390 the status
  row's figures broke mid-number and then ran under the button; the figures
  are now unshrinkable (`flex: 1 0 auto`, nowrap) and the row wraps, so the
  BUTTON drops to its own line. Plan record; PR body last.

## Test plan (red first, targeted only)

- P1: `styles/inputClass.test.ts` — source guard: no `kb-input` anywhere under
  `web/src`; in the touched tsx files (`ItemEnvironmentPanel`,
  `ToolsChecklist`) every `<input` carries `className="input"` and no inline
  `border: "1px solid var(--paper-3)"`. Mutation: put one back → red.
- P2: `Gauge.test.tsx` — limit 0 draws no meter and says unlimited; pct is
  clamped at 100; `role="progressbar"` with `aria-valuenow`. Mutation: drop the
  clamp → red.
- P3 modal (`ItemEnvironmentModal.test.tsx`, rewritten around Save):
  1. typing a cpu value enables Save; Save sends ONE `setSize` with BOTH
     dimensions (the untouched one = its stated value / null);
  2. Cancel on a clean modal closes without a prompt; Escape too;
  3. Cancel / Escape on a dirty modal asks once (DialogProvider), confirming
     discards and closes, declining keeps it open, and NOTHING is sent;
  4. "Back to default" makes the modal dirty; Save then sends `null`;
  5. running → inputs disabled, Save disabled, "Close sandbox" present;
     clicking it sends the DELETE and re-reads both queries (new in P5 — no
     such test existed before this PR either);
  6. read-only viewer → no Save, no Cancel, a Close-panel button, inputs
     disabled, `itemenv.readonly` shown;
  7. refused save → `itemenv.saveFailed` with `role="alert"`, modal stays open;
  8. the two existing "is a real modal" / "asks the item's route … and the
     person's" cases stay.
  Mutations: (a) Save sends only the edited dimension → test 1 (and 4) red;
  (b) drop `useDirtyClose` from Cancel → test 3 red; (c) enable Save while
  running → NOT test 5 (a modal opened running is clean, so `dirty` already
  disables Save) but the extra case "disables Save when the sandbox starts
  under a draft" — the only path to a dirty draft while running.
  P5 (round 1) adds: the draft is dropped after Save; Save carries the other
  dimension as the server holds it at save time; one Save is one PUT; a
  failed re-read after Save says so; client-side refusal of `0` / an
  unparseable memory spelling; reset links hidden while locked; a CSS shape
  guard for the status row and the fields (`styles/item-environment.test.ts`);
  `.live-card` guarded in `gauge.test.ts`.
- P3 panel (`ItemEnvironmentPanel.test.tsx`, adjusted): keeps its 13 cases'
  intent (default vs stated, clamped notes, unenforced, read-only, running
  lock) re-aimed at the labelled fields; adds: both tiles present with the
  right figures; the status row's detail names cores and memory.
- Gate per change: the touched test files + `pnpm run typecheck`.

## Live check (before the PR leaves draft)

Real Chromium, worktree build, `resources.per_user` configured
(`wsconfig-quota.yaml`): open a Playground item, `POST …/exec` to wake the
sandbox, open the modal. Screenshots at **1280** and **390** for: running
(fields locked, Close sandbox), idle (fields editable), read-only (a second
user id), a dirty draft with Save enabled, the Escape prompt. Compare against
the Tools modal on the same build: same title size, lede colour, footer
buttons; and against `/my-resources`: same tile and row shapes.

## Verified ground truth (origin/master `e98ae9cb`)

- Frame to copy: `web/src/components/ToolsPickerModal.tsx:82-131` (width 480,
  `panelStyle padding 18 gap 10`, `<strong 14px>`, lede 12px `--text-paper-d`,
  footer `.btn[data-variant=secondary|primary][data-size=sm]`,
  `useDirtyClose(dirty, onClose)` on both Cancel and `ModalShell.onClose`).
- Modal today: `ItemEnvironmentModal.tsx` (420px, gear + h2 14px + ✕; two
  queries `item-environment` / `my-resources`; `save` = `setSize(sizeToSave
  (env, edit))`; `close` = `closeEnvironment`). Panel: `ItemEnvironmentPanel.tsx`
  (drafts at :81-86, blur saves at :180/:224, private `Meter` :59-69, cpu gauge
  only :239-247). CSS `styles/item-environment.css` (inputs :65-68).
- Size model: `ItemEnvironmentSize.ts` — `sizeToSave(stated, edit)` fills the
  untouched dimension from the stated value; `null` clears. API
  `api/itemEnvironment.ts:96-104` PUT `{cpu_cores, memory}`. Server
  `api/item_routes.py:160-215` refuses ≤0 / above hard ceilings (422), clamps
  the rest at resolve (`bound_by`).
- `/my-resources` parts: `pages/MyResourcesPage.tsx` `Meter` :50-58, `Gauge`
  :68-88, `.stat-row` :127, `LiveEnvironmentRow` :236-290 (`.live-dot`, cores
  strings `resources.live.cores(_one)`, `.btn` secondary Close, no confirm).
  CSS `styles/my-resources.css` `.page .gauge*` :118-139, `.meter` :164-176,
  `.page .stat-row` :199-222, `.page .live-*` :269-320.
- Inputs: `.kb-input` `styles/kb.css:611-623` (+ `.kb-field .kb-input` :693);
  Tools search inline chrome `ToolsChecklist.tsx:59-74`; global input reset
  `base.css:68-77`; `.btn` `base.css:122-180`.
- i18n present: `itemenv.*` (heading, tip, close, close.hint, size.*,
  memory.clamped.*, unenforced, readonly, saveFailed, loadFailed, loading,
  status.running/idle, usage.total, dismiss), `tools.save/cancel`,
  `resources.gauge.cpu`, `resources.memory`, `resources.live.cores(_one)`.
- Existing tests: `ItemEnvironmentPanel.test.tsx` 13 cases,
  `ItemEnvironmentModal.test.tsx` 10 (four of them pin blur-to-save and the ✕
  — replaced by the Save cases above), `MyResourcesPage.test.tsx`.
- Styles are imported in `web/src/main.tsx` (kb :25, my-resources :31,
  item-environment :32).

## Live check record (2026-09-19, worktree build on 127.0.0.1:8256, `resources.per_user` configured, real Chromium)

Playground items; the running one woken with `POST …/exec {cmd:["echo","hi"]}`.
The header's Sandbox button is tucked at 390, so the 390 shots open the modal
at 1280 and shrink the viewport around it (the tucked button is a separate
report, per "Deliberately not doing").

| state | 1280 | 390 |
|---|---|---|
| idle, editable | 480×461; inputs 215×34, 1px solid, 6px radius; Save disabled; 2 tiles | 342 wide; fields 1 column (304px each); tiles 1 column; no horizontal overflow |
| dirty (cpu → 2) | Save enabled (accent) | — |
| Escape on dirty | "Discard unsaved changes?" with Keep editing / Discard changes; nothing sent | — |
| after Save | one PUT; helper reads "1 core · Set by you · Back to default" + "You set 2; 1 is in effect (held down by this App's ceiling)" — the server's clamp, unchanged | — |
| running | inputs disabled, Save disabled, "Close sandbox" (`.btn` secondary) in the status row, hint below | figures "1 core · 512.0 MB" on one line, the button wrapped to its own line |

Frame parity with the Tools modal on the same build: 480px, `<h2>` 14px /
`<strong>` 14px, 12px lede in `--text-paper-d`, `.btn` sm footer. Tile parity
with `/my-resources`: same `Gauge` component, same `gauge.css`.

Screenshots `sm-idle-1280.png`, `sm-dirty-1280.png`, `sm-escape-1280.png`,
`sm-saved-1280.png`, `sm-running-1280.png`, `sm-running-390.png`,
`sm-tools-1280.png` (job tmp; not committed).

## Review round 1 (2026-09-19 — defect / conformance / veracity / regression, in parallel, isolated snapshots)

Worst finding: **HIGH** — the memory placeholder. Regression: none.

- **Defect**: HIGH — the memory field's placeholder was `formatBytes(effective)`
  = `"512.0 MB"`, a spelling `parse_size` refuses (integer + K/M/G/T only), so
  typing what the hint showed gave a 422 that only said "not saved". LOW ×4:
  the untouched dimension was copied into the draft at the first keystroke (a
  concurrent resize by the other `change_permission` holder would be written
  back over); a Save whose re-read failed showed the old numbers with nothing
  saying so; `min={0}` let `0` through to a 422; `<label for>` pointed at an
  input that is not drawn when the dimension is unenforced. Escape/confirm
  stacking, refetch-under-draft, CSS scope and tokens all held.
- **Conformance**: D1–D7, "not doing" and P1–P4 all map; the same placeholder
  gap; plan text wrong in three places (P2 "unchanged", mutation (c)'s test,
  test 5's "kept from today" — no such test ever existed); `memory-unenforced`
  / `reset-memory` shipped without tests; `sizeToSave` left as a second, dead
  rule for building the PUT.
- **Veracity**: every mutation the plan names bites ((c) via a different test);
  unguarded: `setDraft({})` after Save, reset hidden while locked,
  `!isPending`, the whole Close-sandbox path, the P4 CSS fix. Two false
  sentences in `item-environment.css`'s header ("nothing else"; "declares no
  chrome of its own" — `.env-status` re-declared a look-alike of
  `/my-resources`' row with different padding and surface). `.kb-input` and
  113 counts confirmed; after the PR it is 112.
- **Regression**: none — 15 `.kb-input` fields map 1:1 onto `.input` with
  byte-equal declarations and no cascade flip; `/my-resources` HTML
  byte-identical after the gauge move; the five old blur-save cases fail on
  the new modal exactly as planned; 243 → 260 tests green.

**P5** answers all of it: placeholder in the server's spelling + client-side
validity (`isValidCpu` / `isValidMemory`, the server's rules asked first) with
`aria-invalid` and a grammar note; the draft holds only typed fields and Save
reads the other from the live record; an in-flight ref makes one Save one PUT;
a `reload-failed` alert when the re-read after Save fails; `min={0.5}`;
`htmlFor` only where the input exists; `.live-card` in gauge.css shared by
the page's rows and the modal's status row (padding restated on the page rule
because the generic `.page ul > li` outranks a one-class card);
`ItemEnvironmentSize` trimmed to `toSizeString` + the two validity rules, its
test rewritten; seven new modal cases, four panel cases, five CSS guards.

