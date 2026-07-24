# Plan — PM app UI review (PR #640 + follow-ups)

A review of the PM app's declarative **entity views** (`web/src/renderers/entity/`
— board / table / gantt + the record file editor) surfaced a batch of concrete
UI defects and one data-corruption bug. This plan tracks the fixes as flat
phases (one commit each), with layout results confirmed in a **real browser**
(Playwright over the real renderers — happy-dom has no layout engine).

Branch: `worktree-pm-ui-review-fixes` · draft PR **#640**.

## Verification method

Unit tests (vitest/happy-dom) cover behaviour. Anything visual/layout is verified
by mounting the **real** `EntityViewBody` / `TableView` / `BoardView` /
`GanttView` with mock data on a throwaway Vite page and driving Playwright's
bundled Chromium (`~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome`) — the
components take `entities` as a prop, so no backend is needed. Harness files are
deleted before commit. See [[project_issue_pm_ui_review_640]] in memory for the
gotcha log (single `<FileView>` mount → keyed remount; flexbox `min-width:0` was
NOT the real overflow cause).

## Phases

| # | Issue | Fix | Status |
|---|-------|-----|--------|
| 1 | **Cross-record state bleed** (🔴 data corruption): editing `issues/1.md` then opening `issues/2.md` showed #1's title/date; a Save persisted #1's values onto #2. | The IDE mounts one `<FileView path={activePath}>`; a tab switch only swaps the prop, and `EntityFileEditor` seeds its form from `record` via `useState` (once). Key the editor to `path` so a different record remounts + re-seeds. | ✅ done |
| 2 | **Create form crammed in the header**: the expanded "+ New" grid lived in the panel's flex header, floating as a lopsided card on the board. | Open it in a `ModalShell` (backdrop / Escape / focus-trap) with a titled responsive field grid. | ✅ done |
| 3 | **Board cards not editable** → wanted a ⋯ menu. | Each card gets a `⋯` Popover: **Edit** (modal wrapping `EntityFileEditor`) + **Open file** (`{records_path}/N.md`). `EntityViewProps` gains `onSave`/`onOpenRecord`, wired from `AiYamlRenderer`; the menu/modal stop pointerdown so they never arm a drag. **Delete deferred** — no backend entity-delete route. | ✅ done |
| 4 | **Table "text cut"**: DUE / PROGRESS clipped off the right edge. | Real cause (browser-confirmed): every cell was an always-on native `<select>`/date input + long titles expanded columns, so the table was far wider than the pane. Fix: value cells render **text at rest, editor on click** (`EditableCell`), + `table-layout: fixed; width:100%` so columns share the pane and long values truncate in-cell. (`min-width:0` containment landed first but was insufficient alone.) | ✅ done |
| 5 | **`ref` field (milestone) is a raw number box** in the create/edit forms — "只能填數字很怪". A ref points at another entity, so a bare number is meaningless. | Render a `#N <title>` **picker** wherever a ref is edited (the table already did). Wire `refOptionsForField(type, refIndex, name)` into every edit surface. | ✅ done |

### Phase 5 sub-steps

- [x] `refTraversal.refOptionsForField(type, refIndex, name)` — resolve a ref
      field's picker options (or `undefined` to keep the number fallback).
- [x] `roleWidget.RoleCreateInput` — ref widget renders `RefSelect` (`#N title`)
      when `refOptions` are supplied.
- [x] Create modal — `EntityViewBody` computes `refOptionsFor` from
      `type` + `refIndex` and passes it through `QuickCreate` → `RoleCreateInput`.
- [x] `EntityFileEditor` — accepts `refOptionsFor`, passes `refOptions` to
      `RoleField` (already supports ref pickers).
- [x] `BoardView` — thread `refOptionsFor` into the card Edit modal's editor.
- [x] `RecordFileRenderer` — load referenced records (`useReferencedRecords`) so
      opening `issues/N.md` also shows the milestone picker.
- [x] Tests green (`EntityViews`/`EntityFileEditor`/`roleWidget`/`refTraversal`
      158 + drift guards), `tsc` clean.
- [x] Real-browser verify: create modal + card Edit modal show a milestone
      dropdown (`#1 v1.0 Launch …`), not a number box.
- [x] Commit + push to #640.

## Open decisions (for the user)

- **Card ⋯ menu — Delete?** Needs a backend entity-delete route (record file +
  backref semantics). Not in this PR; open if wanted.
- **Gantt `group_by` picker UI?** Swimlanes are derived from the view's
  `group_by` field (YAML-only today); there is no in-UI control to change the
  grouping or add a lane. A "Group by ▾" dropdown on the Gantt toolbar is a
  possible follow-up.
