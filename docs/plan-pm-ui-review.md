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
| 6 | **Left-sidebar member panel ignores visibility + hides group grants**: `ItemMembersPanel`'s `rosterOf` lists owner + `members` ∪ *user* grantees regardless of visibility — so a **public** item shows "you and him" (misleading) and **private** still shows others (who have no access); and a granted **group** (#608) never appears (only `itemGrantsFromPermission` = users is used, not `itemGroupGrantsFromPermission`). | **Decided (user):** panel shows the `AccessChip` + reflects visibility — **public** → "Everyone can access this."; **private** → "Only you."; **restricted** → the roster (owner + user grants + **group grant rows**, resolving group name/count via `usePickableGroups` like `ItemShareDialog`, "Unknown group" fallback). Browser-verified (public→Everyone, private→Only you, restricted→roster + group row). | ✅ done |

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

| 7 | **Top-bar `👥 N` Members popover feels redundant** (user). It's the same `ItemMembersPanel` as the sidebar (Members mode), so it duplicates the roster; the head-count is noise for the dominant solo/private case and doesn't answer the question that matters (visibility). | **Decided (user): A.** Top bar shows an `AccessChip` (Public/Restricted/Private); clicking it opens "Manage access…" for whoever may change access (else read-only). The member-count popover is removed; the roster lives in the Members sidebar. Browser-verified. | ✅ done |
| 8 | **Gantt doesn't show who is responsible** — the standard PM-tool need. | **Decided (user): ① + ②.** ① The bar shows the assignee's **avatar** (the Asana/Monday/Jira convention): a `spec.assignee` field → `BarAvatar` at the bar's right end (`gantt.ai.yaml` gains `assignee: assignee`). ② A **Workload** resource view (`views/workload.ai.yaml`, `group_by: assignee`) — one lane per person; `groupLanes` now resolves an `actor` group field to the user's **name**, not the raw id. Browser-verified (avatars on bars + named lanes). ⚠️ Built on the pre-#641 gantt (this branch is off master); reconcile with #641's GanttView rewrite when both land. | ✅ done |

## Real-app testing follow-ups

Testing on the running app (not just harnesses) surfaced gaps the seeded
harnesses had masked — a reminder that the harness data must match reality:

- **#5 (ref picker) — empty-target case:** a fresh project has no milestones, so
  `RoleField` (record editor / table cell) still fell back to a raw number box
  (its `refOptions.length > 0` guard), even though the create modal already
  showed a dropdown. Fixed: a `ref` renders the `#N-title` picker whenever options
  are *defined* — an empty list shows the dropdown with just "—", never a number.
- **#6 (member panel) — narrow sidebar clipped:** adding the access chip widened
  the header past a narrow sidebar, clipping "Manage access…" and the roster's
  role labels. First pass (`min-width:0` + `flex-wrap`) still clipped at ~185px
  because the chip + "Manage access…" stayed one non-shrinkable group. Real fix:
  the header **stacks** — title + chip on line 1, the "Manage access…" button on
  its own line — so it fits any width. Browser-verified at 185px.
- **No way to add a milestone:** `milestone` is a `ref`, but the only milestone
  view is the Roadmap (a **gantt**), which suppressed `+ New` — so a milestone
  could never be created in the UI, and the ref picker was always empty. Fixed by
  dropping `suppressQuickCreate` from the gantt renderer (it was only there
  because the OLD inline create form was awkward; create is a modal now), so the
  Roadmap — and any gantt — offers `+ New`. A FE registry change, so it applies to
  existing items immediately.
- **#6 (member panel) — group members hidden:** a group grant showed only a
  head-count. It now expands (collapsed by default) to reveal its members,
  resolved from `useMyGroups` (`listGroups`, which carries member ids for groups
  the viewer can see). Browser-verified at a 234px sidebar.

## Open decisions (for the user)

- **Card ⋯ menu — Delete?** Needs a backend entity-delete route (record file +
  backref semantics). Not in this PR; open if wanted.
- **Gantt `group_by` picker UI?** Swimlanes are derived from the view's
  `group_by` field (YAML-only today); there is no in-UI control to change the
  grouping or add a lane. A "Group by ▾" dropdown on the Gantt toolbar is a
  possible follow-up.
