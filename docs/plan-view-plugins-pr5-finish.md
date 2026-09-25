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
  stack is a query against the built cache, not a new build.
  *As built (951e69cf):* the cache holds only the colour column, so a stack reads the
  source the cache names (checked against the version the cache recorded) once per
  query; the browser keeps each answer per argument set.
- **A − B.** "Set as B" takes the current selection. The panel shows A, B and A − B.
- **Thumbnails** load lazily (IntersectionObserver). They are not cached across page
  loads. A layout card's panes each draw their own thumbnail.
- **Save as table.** The file is `markings/<name>-<yyyymmdd-hhmm>.csv`, written through
  the file facade, so the workspace quota applies. The rows are selected in the sandbox
  from the view the action was taken in, with its transforms applied before the marking
  lights them.

- **Made while building [mine, open to override]:**
  - (P2) The table a selection is made in is not filtered by it: it keeps every row,
    the marked ones highlighted ("fail marks N of M rows"), so the first click does not
    make the other rows vanish. Every other view on the marking still filters.
  - (P2) A table decides only about the values it holds: ticking L03 in a 15-lot table
    does not un-mark L19, which only a 25-lot chart has.
  - (P2) Members who cannot edit records still get the checkboxes on a marking; marking
    writes no record.
  - (P13) A chart no longer draws its `title:` inside the canvas; the view header right
    above shows it.
  - (P7) Saving a marking's rows is a capability a plugin declares
    (`"provides": {"marking_rows": "<command>"}`); two plugins declaring it refuse boot.
    A chip saves exactly what it sent (a digest recorded at send), or refuses by name.
  - (P10) First-open progress is polled from a progress file inside the plugin, not
    streamed: a streamed run would need a platform streaming route and a new SDK hook,
    and the sandbox already runs a second command beside a running one (a685ea8f).
  - (P24) An entity table keeps each data column at least 6rem wide, which holds a
    date or a status chip; a narrow pane scrolls sideways instead (26bfacaa).
  - (P25) A line's points are hidden until hovered, rather than an axis-trigger
    tooltip, which would change what a scatter layer in the same chart shows
    (686b3533).
  - (P26) A zone-less datum on a zoned axis is a wall time in the axis's zone; a wall
    time the zone had twice or never is refused by name rather than guessed
    (083369e0).
  - (P29) In a stack on a quantitative or time axis, a series with no row at another
    series' x counts as 0 there (plotly's `stackgaps: "infer zero"`), drawn clear and
    mapped to no row (a90969cf); on a log y it is left empty instead, since 0 has no
    place on a log axis (f86df895). A category axis is left to ECharts, which already
    stacks by value.
  - (P8) "A scenario per feature" means one per feature **the model can show or read**:
    the gallery, the stack, a linked table, a saved selection (four scenarios). Box select
    (P3) is a gesture only a person makes, and a card thumbnail (P6) is drawn by the card
    whatever the model does, so neither has a model behaviour to score; a scenario for
    them would pass with the skill and without it, which `skill_eval --control` names as
    measuring nothing. Recorded here after round 16's conformance lens found the rule
    narrowed in commit 07a6b591 without a line in the plan.
  - (P31) A pie with no axes draws a toolbox with only the clear tool (✕), which clears
    as ✕ does on every chart (P17: a marking its `highlight:` seeded too) and forgets
    the slice a click picked (eac1146d). P30 had taken the whole toolbox off, so a
    seeded marking could not be cleared from the pie.
  - (P31) A chart narrower than 320 px is laid out compact: ChartView's ResizeObserver
    sets a boolean (`compactAt`), so the option is rebuilt only when the width crosses
    320, and a switch is set in full. The colour bar or category legend stands under
    the plot at the left, the y axis's name above the plot, the tools shrink to fit
    one row. ECharts' `media` queries were not used: they change the option's shape
    for every consumer of it (cb1b4d7f).
  - (P31) `useContainerWidth` reports the border box from its observer, as its first
    measurement already did, for every consumer (the workspace shell, the header
    actions, the skills modal's footer, a view panel), not only the view panel whose
    narrow padding made the content box flip it (0ecd6666).

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
- **P24 — What the lead saw in the phase demos.**
  - A table in a narrow pane is clipped (P13 made panes `overflow: hidden`): at 390 the
    entity table's LOT column showed "L" and the csv-table two of four columns. A pane
    clips the page; a table inside it scrolls sideways.
  - A numeric axis's tick labels run together in a narrow pane ("98100102104106");
    P14 thinned only a time axis's labels.
  - A zoned tooltip on hourly points read "2026-03-01 Asia/Taipei": the time of day must
    show whenever the column has one.
- **P25 — A line chart has a tooltip.** Found live (P14's check): an item tooltip needs
  points, and a line hides them, so hovering a line shows nothing — PR 2's Done-means
  lists tooltips for every mark.
- **P26 — A zone-less temporal datum on a zoned axis reads in the column's zone.** Since
  P14 the axis shows the column's zone, and filters read a zone-less time there, but a
  rule `datum: "2026-03-01T12:00"` is still placed at 12:00 UTC (20:00 on a Taipei axis).
  The renderer and `validate`'s placement (`datums.py`, held together by
  `wire-corpus/datum-axes.json`) read it in the axis column's zone, UTC when it has none.
- **P27 — Say which columns a marking marks by [user].** A marking is `column → set of
  values`, so a selection over a two-column key (lot + wafer) lights every combination:
  6 tiles picked, 15 marked. The user kept that model ("this is normal") and asked for it
  to be stated where it shows: the gallery's count, the chart's "N selected", a table's
  bar, and the marking chip name the key columns (e.g. "15 of 48 marked · by lot, wafer").
- **P28 — What P24's demo left.**
  - A line or area chart's brush selects nothing (ECharts' line series has no brush
    selector); PR 2's Done-means lists box brush for every mark.
  - The enlarged gallery view's box is only as tall as the gallery: with few tiles the
    canvas overflows it at 1440 and is clipped at 390.
  - In a narrow pane "Save as table" ends past the pane's edge.
  - A 40-cell grid's first label read "1001", not "1000" (unverified when written;
    verified in Chromium and fixed in b85c8c03).
- **P29 — What P27/P28's demo showed.**
  - A `grid`'s cells blur into each other at their edges (the raster is scaled with
    smoothing); a cell is one flat colour with a sharp edge.
  - A `stack: true` area on a quantitative x does not stack (two layers of 1s span
    0–1); on a category x it stacks by value, and on a time x it only looked stacked
    (it stacked by index).
  - The chart a selection is made in shows only its own brush box (16 lit) while every
    other view shows what the marking lights (27): after a selection writes the marking,
    that chart lights by the marking too.
- **P30 — Every mark a person can select from.** Round 16 (veracity): only bar,
  scatter, line/area (P28) and grid select; `heatmap`, `boxplot`, `errorbar` and `pie`
  select nothing, yet the skill tells the AI to draw a `diff` as a heatmap on a
  marking. A brush or lasso selects heatmap cells by their centre and boxplot /
  errorbar groups by their drawn element; a click selects a pie slice.
- **P11 — Live check at 390 and 1440 wide, with a base differential on P42 (the branch before these phases).** Run
  #847's full scene (a grid, a scatter and a table on one marking, plus an unrelated
  view), the gallery (box select, sort, stack, A − B), the cards, and save-as-table.
  Update the docs (`view-plugin-chart.md`) and the #855 runbook entry if the operator
  must act.
  *Done 2026-09-25 at 1d7422c4* (`kind: local`, jail on, real Chromium, a scripted
  model that only picks the tool; every tool ran for real). Passed: the full scene at
  1440 and 390 (lasso 72 lit → table 72 of 432 and records 13 of 40, the unrelated bar
  unchanged, save 4 rows, chip save 90 rows, the AI reading both), the 1000-group
  gallery (sorts equal to pandas' top 8, box / Shift-box / replace, ranks 1–30 = 494,
  stack and A − B ranges equal to pandas, category colours, enlarged fits at 390,
  recovery keeps scroll), P22's reopen ("Opening…" only; the wall in 1.4–1.7 s), the
  cards, the zoned axis in two browser zones, all 13 mark specs, five charts × 7 loads
  × 2 widths with 0 failures, and the base differential on master and on 14405a12.
  Found: P31. Evidence: `$CLAUDE_JOB_DIR/tmp/live16-shots/` (seen by me).
- **P31 — What P11 found.**
  - At 390, in a five-pane layout, a `grid` pane 139 px wide has a plot about 2 px
    wide: axes and a colour bar, no cells, and the lasso cannot be used.
  - A `grid`'s colour-bar top label is drawn over the toolbox's clear icon.
  - A scatter's last x tick is cut at the canvas edge ("0.8" reads "0."). The cause
    was not a margin: its view panel's narrow switch, read from the content box that
    the switch's own padding changes, flipped it narrow and wide every frame, and the
    chart resized with it.
  - A point that sits under a rule cannot be hovered: the rule takes the hover.
  - At 390 the gallery's first selection adds "N of M marked", which wraps the toolbar
    and moves the wall down 26 px (P18 fixed the same thing on a chart).
  - A pie with no axes has no ✕ since P30, so a marking seeded from its `highlight:`
    cannot be cleared from that pie.
- **P32 — An aggregated channel's column is not a key.** Found in P30's demo: a pie of
  `theta: {field: <col>, aggregate: count}` on a marking keyed by that column lit the
  slices whose count equalled a marked value, and a click wrote the count. The answer
  names an aggregate after its field, so a field a channel aggregates neither lights a
  chart nor is written by it; the chart links by its other fields.
- **P33 — The gallery's first open, measured.** A benchmark of the #857 cost table at
  4c382ab4 found it no longer described the code: first open 15–23% slower than
  before pr5 (the extra in P4's per-column work: the facet columns read twice, one
  stable argsort per column), and 2.5–3× slower with `ordinal` x/y (a per-row
  `_as_marking` map in the category wire, ~12 s with no progress line on 10M rows).
  The category wire skips the map for number, bool, instant and duration dtypes
  (parity with the old `_cat` as oracle); each facet column is read once and the
  tiles ordered once; the cache stays byte-identical (golden digests from bec9ccae).
  The runbook table is re-measured for both axis types (943bb39c, 1f9d2605, 6e385141).

## Verification

- Every phase starts from a test that reddens on the code before it. A guard is proven
  by mutating its line. Parity tests use an oracle (pandas for sort and stack; `canon`
  for table keys).
- Targeted tests, `ruff` / `ty` / `pnpm typecheck`.
- Review rounds with the four lenses in parallel, then CI on the final sha.
