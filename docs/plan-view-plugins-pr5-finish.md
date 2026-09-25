# Plan — finishing #847 and #848 on #855's branch

Part of [plan-view-plugins.md](plan-view-plugins.md); the decisions are Q22 and Q23. The
2026-09-25 audit of PRs 1–4 against #847/#848 found six asks that no PR built. The user
chose to build all six before #855 reaches master **[user]**. The same audit's engineering
gaps are phases here too, so one list tracks what is left.

Everything stays generic **[user]**: a wafer map is a map. No option, default or label
gives a value a meaning ("higher is worse", "yield", "defect rate"); #847/#848's domain
words are examples, not spec keys.

## Done means

- **Tables follow a marking.** The built-in entity `table` and the `csv-table` view take
  `marking:` as a chart does.
  - While the marking holds a set, the table shows only the rows it lights. A bar above
    the header reads "filtered by <name> · 3 of 25 rows · show all"; "show all" keeps
    every row and highlights the lit ones **[user]**.
  - Selecting rows in the table writes the marking, so the charts light up **[user]**.
    A table with no marking keeps today's multi-select (batch edit, drag).
- **Gallery box select.** Dragging a box over the gallery selects every tile it touches
  and writes the marking **[user]**.
- **Gallery sort by any column [user].** A menu lists every column. A column with one
  value per tile sorts by that value (text, number and date in natural order). A column
  with several values per tile sorts by a statistic the user picks: count, min, max,
  mean or median for numbers; count or distinct count for text. Ascending or descending.
  The sort runs over all groups, as today's does.
- **Stack panel beside the gallery [user].** One map that stacks the selected tiles, or
  all tiles when none is selected. The user picks the column and the statistic (count,
  min, max, mean, median, sum; count or distinct count for text) and can subtract a
  second set for an A − B map. The panel takes the gallery's marking.
- **Card thumbnails [user].** A `show_file` card for a chart, a gallery or a layout
  draws that spec small, once, when the card scrolls into view. It uses the same
  renderer, is static, and opening the card gives the live view.
- **Save a selection as a table [user].** The marking's header control and its chat
  chip offer "save as table". It writes the source's rows that the marking lights, with
  every column, as a new CSV in the workspace. The file tree shows it, `csv-table` opens
  it, and the AI can read it. When #843 lands, the file is already a row source.
- **The AI knows each of these** (Q2's rule): the skill and the `## Available views`
  line name them, and a scenario per feature ships under `scenarios/`.

## Decisions made while planning [mine, open to override]

- **Table keys.** A table lights a row when, on every column it shares with the marking,
  the row's value is in the set. The values are compared as marking text (`canon`, the
  same string a chart writes). A table that shares no column with the marking shows all
  its rows and says "no column in common with <name>".
- **What a table writes.** The key columns are the spec's `keys:`. Without `keys:`, the
  marking's own columns are used. With neither, the table writes nothing, and the header
  control says why.
- **Filter toggle.** "show all" is per view and per person, like the header's marking
  override. It is not written to the spec.
- **Box select gestures.** These follow a file manager: dragging from empty space
  replaces the selection, and Shift-drag adds to it. A tile counts as selected when the
  box touches it.
- **Where the gallery's sort and stack are computed.** They run in the sandbox half, as
  the gallery build does. The build cache key gains the sort column and statistic. A
  stack is a query over the same cache, not a new build.
- **A − B.** "Set as B" takes the current selection. The panel shows A, B and A − B.
- **Thumbnails** load lazily (IntersectionObserver). They are not cached across page
  loads. A layout card's panes each draw their own thumbnail.
- **Save as table.** The file is `markings/<name>-<yyyymmdd-hhmm>.csv`, written through
  the file facade, so the workspace quota applies. The rows are selected in the sandbox
  from the view the action was taken in, with its transforms applied before the marking
  lights them.

## Phases

- **P1 — Table reads a marking.** A pure function lights rows by shared columns
  (`canon` text). Add it to the entity `TableView` and `CsvTableView`: the filter bar,
  "show all", and the "no column in common" note. `linkable` is set on both renderers.
- **P2 — Table writes a marking.** Selecting rows projects them onto the keys and writes
  the marking. This path is kept apart from batch edit when no marking is attached.
- **P3 — Gallery box select.** The rubber band is hit-tested against tile rectangles and
  wired to the existing range writer. Test the gesture rules.
- **P4 — Sort by any column.** Sandbox: a per-group sort key for any column (single
  value, or a statistic), plus a list of columns with their kinds for the menu. Web:
  the menu, and a cache-key change. Parity: the sandbox order equals pandas' sort over
  the same groups.
- **P5 — Stack panel.** Sandbox: stack a set of groups by column and statistic over the
  cache, and compute A − B. Web: the panel, the pickers, "set as B", and the marking.
- **P6 — Card thumbnails.** A static small render of chart, gallery and layout panes in
  `ShownFiles` / `ShownLayoutCard`, loaded lazily.
- **P7 — Save as table.** Sandbox: a command that writes the lit rows as a CSV. API/web:
  the action on the header control and the chip, going through the facade.
- **P8 — The AI side.** SKILL lines, the views-index line, one scenario per feature, and
  `skill_eval`'s show_file stub gains `layout` (audit).
- **P9 — Audit engineering gaps.**
  - Category gallery enlarge shows no value: `facet_exact` raises TypeError, which exits
    2 and is never shown. Fix it, add a category test, and show exit-2 errors.
  - A spec over 128 KB passes `show_file` but gets a 413 on every render: pass a path,
    or refuse it at `show_file`.
  - Generic column names in SKILL's facet example (no `[lot, wafer]`).
  - Guard tests: ECharts stays out of the SPA bundle; sandbox-host's Dockerfile bakes
    the plugins; the CSP has no `script-src`.
  - Component tests: a lasso and a legend click write the marking.
- **P10 — The gallery's first open shows its progress.** PR 4 planned it and
  delivered a fixed notice. The build's progress lines reach the view while it runs:
  a streamed sandbox run, or polling the build's state, whichever the platform's
  runner supports.
- **P12 — Concurrent commands in one jailed sandbox.** The 2026-09-25 live check
  (on #855's branch with #856 merged) broke 5 of 30 queries of a five-chart layout under `kind: local`'s jail
  (the k8s base default): `mount: …/dev/urandom: mount point does not exist`, or
  `NotImplementedError: /dev/urandom … not found`. Every exec bind-mounts and then removes
  the shared `$ROOT/dev/*` targets (`sandbox/local_process.py` `_JAIL_BOOTSTRAP`), so
  execs that overlap tread on each other. Before #855, an agent's execs ran one at a
  time; side-by-side charts and layouts make overlap routine. Issue #859.
- **P13 — A layout is usable in narrow panes.** At 1440 wide, with the tree and chat
  open, a five-pane layout gives panes 255 / 128 / 64 px. The view header takes most of
  the height, a chart's fixed 360 px does not fit, tab titles spill into the chat
  column, the toolbox overlaps the title, and 390 wide scrolls sideways. A chart fills
  its pane, the header compacts, titles truncate inside their tab, and nothing scrolls
  the page sideways.
- **P14 — A zoned time shows in its column's zone.** The tooltip shows UTC ISO and the
  axis the browser's zone, while a filter reads a zone-less time in the column's zone.
  The axis, the tooltip and the gallery's labels show a zoned column in its own zone and
  name it. A zone-less column shows as written (UTC), in every browser.
- **P15 — `highlight: values` on a date column reads its values as a filter does.**
  Today it compares `canon` text, so `"2026-03-01T03:00"` matches nothing where
  `oneOf` matches. It uses the same reader as `equal` / `oneOf` (`chart_view/instants.py`).
  A `where:` whose pandas comparison fails names the column and the value, instead of
  pandas' raw message.
- **P16 — A colour scale shows readable values.** A heatmap's legend labelled both ends
  "0" (precision 0 over 0.056–0.346), and a grid's colour bar has no numbers. Both show
  the range with enough digits to tell the ends apart.
- **P17 — Clearing a chart's selection clears a seeded marking.** A marking seeded by
  `highlight:` survives the chart's ✕; the ✕ clears whatever the marking holds.
- **P18 — Small chart fixes from the live check.**
  - A rule's label is not clipped ("102" drew as "10").
  - A grid's axis line sits on the lattice's edge, not mid first cell.
  - "N selected" does not push the chart down.
  - A lasso on a grid with no marking shows which cells it took.
- **P19 — A category colour draws its categories.** The 2026-09-25 live check of #857
  on this branch: a gallery with `color: {type: nominal}` paints every tile and the
  enlarged view one colour — `gallery.ts` `thumbnail()` sends `cat` codes through a
  continuous ramp over 0..0. Category columns get a category palette and a legend, in
  the gallery, its enlarged view, and a plain `grid` with a nominal colour.
- **P20 — `show_file`'s check refuses what the gallery refuses.** `validate` passes a
  `facet:` spec with an entity source, a missing facet or sort column, or an `aggregate`
  on a channel; `facet_build` then refuses it, so the AI gets a card and the person a red
  panel. `validate` runs the same checks `facet_build` does, and SKILL.md says the source
  must be a table file.
- **P21 — The enlarged tile fits a narrow screen.** At 390 wide the fixed 384 px canvas
  is clipped on the right, and the clipped cells cannot be hovered.
- **P22 — A cached gallery opens without "Building…".** A reopen whose cache is reused
  (`built: false`) still flashes the building notice for about a second.
- **P23 — A recovery rebuild keeps the scroll position.** After the cache is reaped
  while the gallery is open, the rebuild opens at the top, not where the view was, as
  `FacetGallery.tsx` says it does.
- **P11 — Live check at 390 and 1440 wide, with a base differential on P42 (the branch before these phases).** Run
  #847's full scene (a grid, a scatter and a table on one marking, plus an unrelated
  view), the gallery (box select, sort, stack, A − B), the cards, and save-as-table.
  Update the docs (`view-plugin-chart.md`) and the #855 runbook entry if the operator
  must act.

## Verification

- Every phase starts from a test that reddens on the code before it. A guard is proven
  by mutating its line. Parity tests use an oracle (pandas for sort and stack; `canon`
  for table keys).
- Targeted tests, `ruff` / `ty` / `pnpm typecheck`.
- Review rounds with the four lenses in parallel, then CI on the final sha.
