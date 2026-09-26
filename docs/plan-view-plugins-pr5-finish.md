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
    stacks by value. (Superseded: P36 row 11 lines up category axes too, and P40 row 18
    sums a stack's rows in the sandbox; the 0 filler and the log rule stand.)
  - (P8) "A scenario per feature" means one per feature **the model can show or read**:
    the gallery, the stack, a linked table, a saved selection (four scenarios). Box select
    (P3) is a gesture only a person makes, and a card thumbnail (P6) is drawn by the card
    whatever the model does, so neither has a model behaviour to score; a scenario for
    them would pass with the skill and without it, which `skill_eval --control` names as
    measuring nothing. Recorded here after round 16's conformance lens found the rule
    narrowed in commit 07a6b591 without a line in the plan.
  - (P30) A boxplot group is selected by its box (q1–q3), not by a whisker alone: how
    the builder read "by their drawn element" (1978b2dd, which states the rule and pins
    it with a whisker-only test, but gives no reason beyond it).
  - (P30) A pie selects by a click on a slice; the same slice again, or empty space,
    clears only what the click picked — other views' selections and the legend stay
    (1b3ecc6b; P34 row 1 makes "what the click picked" exact).
  - (P34) In compact layout a colour key (a colour bar, or a grid's category key) that
    would leave the plot less than half the chart's height is not drawn; the colours
    stay and the chart's note line says the key is hidden. In a tall narrow pane this
    hides a long key that used to fit under a plot a third of the chart's height: the
    plot comes first. A colour-split chart's series legend stays on top, untouched.
  - (P34) On an unmarked pie a legend toggle replaces the spec's `highlight:` dimming,
    as on a grid: what is lit is the person's latest gesture.
  - (P31) A pie with no axes draws a toolbox with only the clear tool (✕), which clears
    as ✕ does on every chart (P17: a marking its `highlight:` seeded too) and forgets
    the slice a click picked (658c8f09). P30 had taken the whole toolbox off, so a
    seeded marking could not be cleared from the pie.
  - (P31) A chart narrower than 320 px is laid out compact: ChartView's ResizeObserver
    sets a boolean (`compactAt`), so the option is rebuilt only when the width crosses
    320, and a switch is set in full (superseded by P34 row 2: a switch merges the layout
    alone, and in compact the layout also follows the height). The colour key stands under
    the plot at the left, the y axis's name above the plot, the tools shrink to fit
    one row. ECharts' `media` queries were not used: they change the option's shape
    for every consumer of it (ba036393).
  - (P31) `useContainerWidth` reports the border box from its observer, as its first
    measurement already did, for every consumer (the workspace shell, the editor tab
    strip, the header actions, the skills modal's footer, a view panel), not only the view panel whose
    narrow padding made the content box flip it (dd3b7fef).
  - (P35) Another view's write drops a chart's own selection, and also brings back the
    legend entries that chart had hidden (1aaa0b0f): what the chart shows is the marking.
  - (P37) Whether a selection went to the marking compares the source it was written as
    with the marking's entry, not with the view's current path, so renaming the view
    file does not flip "· by" off while the marking still holds that write (2bee96ab).
  - (P37 → P40) The rows of one colour at one stack slot draw as one segment, their
    sum: P37 did it in the browser (10cc4d93), P40 row 18 moves it to the sandbox as an
    implicit `aggregate: sum`, and refuses a stack coloured by a value (which rows form
    a segment is not defined). The "palette colour for a mixed sum" P38 left is gone
    with it.

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
- **P11 — Live check at 390 and 1440 wide, with a base differential on the branch before these phases (14405a12, P43, docs-only after P42).** Run
  #847's full scene (a grid, a scatter and a table on one marking, plus an unrelated
  view), the gallery (box select, sort, stack, A − B), the cards, and save-as-table.
  Update the docs (`view-plugin-chart.md`) and the #855 runbook entry if the operator
  must act.
  *Done 2026-09-25 at 1d7422c4* (`kind: local`, jail on, real Chromium, a scripted
  model that only picks the tool; every tool ran for real). Passed: the full scene at
  1440, and at 390 all but the lasso on the grid, which selected nothing (that is
  P31's first finding; picked rows, save and chip passed at 390) (lasso 72 lit at 1440 → table 72 of 432 and records 13 of 40, the unrelated bar
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
- **P34 — Review round 17's findings.** Each row names the rule the fix installs, so a
  sibling case is covered by the rule rather than by another patch:

  | # | Found | Rule installed |
  |---|---|---|
  | 1 | A pie remembers its clicked slice after another view rewrote the marking; its next click on empty space or on that slice clears the other view's selection | A click toggles only what it wrote: the pie keeps the marking it wrote, and "clear" / "toggle off" act only while the entry is still exactly that write; any other write forgets it |
  | 2 | Crossing 320 px (or a pane reporting 0 px) rebuilds the chart in full and drops its brush, count, legend state and a pie's pick; the pie's `clicked` survives the rebuild and swallows the next click | Only a change of doc or answer is a full rebuild; a layout switch replaces the layout components alone (grid, colour bar, legend, toolbox, axis names) and keeps selection, brush and legend state; a 0 px width is no width (keep the last); every full rebuild forgets what a click wrote |
  | 3 | In compact layout a category colour with many levels stacks its legend under the plot until the plot has negative height | The plot keeps at least half the chart's height; a colour key (category legend or colour bar) that would take more is not drawn, and the chart's note line says "colour key hidden (too short)" (a `grid` has no tooltip, so the note is what says it) |
  | 4 | The log-y stack filler reads the top-level encoding, so a `layer:` spec fills with 0 on a log axis | The filler follows the axis the chart built, not the spec's shape |
  | 5 | On a marking the view cannot write (no key column), a clicked pie slice or a lassoed grid shows a count and no highlight | What a selection that writes nothing picked is lit by the chart itself, as with no marking |
  | 6 | On an unmarked pie a legend toggle now replaces the spec's `highlight:` dimming | Kept, as the grid already does: the person's latest gesture is what is lit. Pinned by a test |

  Rows 1–5 each start from a test red at 0c12465a; row 6 is a pin, green there. Row 2 replaces a mechanism, so
  round 18 follows. Demo at 1440 and 390, seen: rows 1, 2, 3, 5; errorbar selection
  (P30) and the horizontal and log-y stacks (P29), which had no frame.
- **P35 — What P34's demo found.** Two wrong drawings and one stale selection that
  predate P34 (frames in
  `tmp/demo/p34/look-after-*`, `after-1440-row1-A-04`, seen):

  | # | Found | Rule installed |
  |---|---|---|
  | 7 | Raw rows stacked on a category axis overlap: rows of one series at one category are drawn over each other, so a horizontal bar of rows ends at 31.4 where the sum is 79.38 | Every row is one piece of its stack, on any axis: rows that share a slot and a series stack in order, as `lineUpStacks` already does off a category axis |
  | 8 | A log-y stack with gaps draws nothing for a series whose rows never sit on neighbouring slots (the null filler breaks the area at every gap) | A missing slot adds nothing to the stack (0); it is empty only where nothing lies beneath it on a log axis |
  | 9 | After another view rewrites the marking, a chart that wrote it still shows its own count, brush box or pick | The marking is what a linked chart shows: another view's write drops this chart's own selection (count, box, pick); a chart whose selection writes nothing keeps its own |

  Rows 7 and 9 change mechanisms, so round 18 covers them. Demo before/after at 1440 and
  390, seen.
  *As built:* row 7's premise was wrong — `lineUpStacks` did not stack a series' own
  rows off a category axis either (a second row at one x drew from 0; P29's "two rows
  at one x" test pinned that overlap, and now expects the sum). Each stacked series is
  split into pieces, the k-th row per slot, which stack in order on every axis
  (14b81f9f). Row 8: 802adbb2. Row 9 subsumes P34 row 1's `held()` — a pick is forgotten
  at the other view's write — and also restores legend entries the chart had hidden
  (1aaa0b0f) [mine, open to override]. Measured in the demo: stack ends equal pandas'
  group sums to within 0.32 at 1440 and 390, a pixel being 0.08–0.41 (before: 31.4 for 79.38).
- **P36 — What P35's demo found.**

  | # | Found | Rule installed |
  |---|---|---|
  | 10 | A chart whose selection writes nothing (no key column) said "1 selected · by group" once another view had written the marking: the columns are the marking's, not its selection's | "by <columns>" is said only of a selection that went to the marking; one that wrote nothing says "N selected". The gallery's and the table's counts are the marking's own, so they keep it (83c3f004) |
  | 11 | An area of raw rows stacked on a category axis had pieces with no fillers (a piece ran straight across categories where it had no row), and its points were drawn in row order, so an unsorted series zig-zagged | A piece is 0 at a category where it has no row, on any axis; on a log value axis it is empty where nothing lies beneath; points follow the axis's order (3f6bcc72) |

  Row 11 runs the line-up on category axes too (a mechanism change) — round 18 covers it.
  Demo before/after at 1440 and 390, seen: after, a's tops 5 4 8 2 7 and b's 7 6 10 4 9
  equal pandas' cumulative sums; bars byte-identical before and after.
- **P37 — Review round 18's findings.**

  | # | Found | Rule installed |
  |---|---|---|
  | 12 | P35 row 7 split a stack into one series per row-rank ("pieces"); a slot with 10,000 rows made 10,000 series (render 245 ms → 8,787 ms), a skewed category 500 series × 200 slots = 100,000 points (56 → 1,744 ms), and every brush rebuilds the option | A stacked series draws, per slot, the SUM of its rows as one point, which stands for all of them (a brush over it selects every one); one series per colour key, O(rows). Replaces P35's pieces [mine, open to override: the rows of one series at one slot are no longer separate segments] |
  | 13 | `toMarking` is one boolean, not tied to the marking: after detaching, a brush made detached, or a move to another marking, the chart still says "· by <cols>" (the other marking's columns), keeps its brush visual off and lights by the marking | Whether a selection went to the marking is read from what it wrote: the marking it wrote to (and the source it wrote as) must be the one the chart is on now; a detached selection wrote nothing |
  | 14 | On a log axis a row with no value beneath counted as "something beneath", so the filler above became 0 over nothing → Infinity | "Beneath" is a row below with a positive, finite value at that slot |
  | 15 | The chart's own write read as another view's when its view file's path changed under it | The write records the source it was made as, and is compared with that |
  | 16 | A second click on the same slice resets `clicked` with no test pinning it (a third click would clear again) | Pinned: click, click, click selects again |
  | 17 | The wide layout restates `NAME_AT.y`'s gap by hand | One constant, read by both |

  Rows 12 and 13 replace mechanisms, so round 19 follows. Performance is measured before
  and after on the regression lens's inputs; the demo (1440 and 390, seen) covers rows
  12 and 13, plus the 390 frames P34 row 2 and the horizontal stack lacked.
  *P37 as built:* row 12 10cc4d93 (`stackPoints`: one point per slot per colour key, the
  sum of its rows; tooltip "(sum of N rows)"; lit if any of its rows is lit), rows
  13/15/16 2bee96ab (`wrote` = {on, source, values}; `toMarking` derived; the source is
  compared with the marking's entry, not the view's current path, so a rename does not
  flip "· by" — [mine, open to override]), row 14 256276f5, row 17 d89c757d, N2 3d96261c
  (a wide chart's height change is pinned by a render count). Measured with the
  regression lens's probe, before → after (toOption / render): 10,000 rows in one
  category 10,000 series, 69–80 / 8,054–8,631 ms → 1 series, 9–10 / 6–7 ms; 500 rows in
  one of 200 categories 100,000 points, 39–55 / 1,746–1,894 ms → 200 points, 1 / 7 ms;
  3,000 rows at one time x plus 1,000 single-row x values, on a log y, 2,716–3,634 ms
  (not rendered) → 3–4 / 25 ms.
  Demo (1440 and 390, seen): G01's tooltip "value: 31.987 (sum of 1,066 rows)" (one
  row's 0.042 before); after reattaching, "50 selected" without "· by kind".
- **P38 — What P37's builder found.** On a log axis a mark's own point at or below 0
  went to ECharts as the layer held it, and ECharts drew an Infinity vertex (a line, a
  stacked area; a scatter and a bar were skipped by ECharts itself). Rule: 0 and below
  have no place on a log axis for any mark, as a rule's datum already had none
  (`pos`); the note line counts them per axis ("N values at or below 0 not drawn on
  the log y axis"), a rule's own values staying with the rule's note. Pre-existing
  since PR 2's review round 7 (dfa5ca6f, "points sent as held"). Known and left: a summed stack point whose rows differ in a colour-by-
  value takes the series' palette colour [mine, open to override].
- **P39 — What P38's demo found** (frames `tmp/demo/p38/`, seen: on the app's canvas a
  stacked series' band vanished over two intervals; a line's value between two left-out
  ones showed nowhere). Rules: in a stack a series' own 0 adds nothing, as a filler
  does — 0 where something positive lies beneath, empty only where nothing does; a
  negative still has no place; the note counts only what is left out. A line that hides
  its points shows a point whose neighbours are both empty, so every value the note does
  not count can be seen (a drawn or dimmed point is left as it is). P38's "not drawn on a
  log x" test used a scatter, which ECharts skips by itself; it uses a line now (round 19
  conformance N3). With a stack's 0 reaching `lineUpStacks` again, row 14's `> 0` is
  pinned again (the loggaps "…has 0" test reddens without it; rounds 19 N2 / F3).
- **P40 — Review round 19's findings.** Round 19 (four lenses at f3164e88) found the
  web-side sum of P37 row 12 leaking into every channel it did not plan for: a stack
  coloured by value drew its mixed sums with `fill="none"` (invisible), the sum's tooltip
  re-read every row on each mousemove (1M rows: ~0.25 s per move), "(sum of N rows)"
  counted rows the sum left out; plus two it did not cause.

  | # | Found | Rule installed |
  |---|---|---|
  | 18 | P37's sum is a second aggregation in the browser, beside the sandbox's `aggregate` (Vega-Lite's), and every channel needs its own rule for it | A stacked layer with no `aggregate` is summed **in the sandbox**, by its slot and colour: exactly an `aggregate: sum` on its value channel. The browser draws one row per slot and colour, as for any aggregated layer; P32's rule (a summed field is no key) makes a brush over a sum write its slot and colour, which the marking lights row by row. Other field channels (tooltip, text, size) are kept where a group shares one value, else empty. `stackPoints` and a point standing for a list of rows are removed. A stack coloured by a value (quantitative colour) is refused by `validate`, naming why: which rows form one segment is not defined. Replaces P37 row 12 [mine, open to override] |
  | 19 | With no marking store (a standalone preview), a brush was cleared the moment it was drawn: the store kept nothing, so the drop read the chart's own write as gone | Only a write the store took can be dropped: with no store, the chart's selection is its own |
  | 20 | The log note counted an errorbar's `y`/`y2` at or below 0, which were still sent to ECharts (Infinity); boxplot/errorbar summaries bypass `at` | Every value a log axis draws goes through `at`, summaries and `y2` included, and the note counts exactly what `at` left out |

  Row 18 replaces a mechanism (round 20 follows). Demo at 1440 and 390, seen: rows 18–19,
  and P39's two scenes (a stacked 0 on a log y; a lone point on a line).
  *P40 as built:* row 19 b06456cf (`WriteMarking` returns whether a store took the write;
  ChartView records `wrote` only then — the SDK's write type goes `void` → `boolean`); row 18
  ec115af6 (`query._stack_sum`: one row per slot and colour; other fields kept where the
  group shares one value, else empty; an all-missing group sums to 0, as `aggregate: sum`
  does; a stack with its own `aggregate` is also grouped by slot and colour only
  [mine, open to override]; each answer layer carries `measured`, which the browser reads,
  so there is no second copy of the rule; schema `stackColour` refuses a stack coloured by
  a value; corpus `wire-corpus/stack-sums.json` feeds both halves; the chart's own
  "N selected" now counts segments [mine, open to override]); row 20 8ce066d0
  (`Axis.leftOut()`; errorbar ends and boxplot summaries go through `at`; an errorbar
  end with no place runs its stem to the plot's foot, uncapped; a box with a summary left
  out is not drawn and has its own note [mine, open to override]; a `line` with
  `stack: true` keeps its 0 as a stack does). Measured end to end on 1,000,000 rows: the
  sandbox query 902–950 → 123–132 ms, the answer 13,023 → 4 KiB, toOption 312–335 → 0 ms.
  Demo (1440 and 390, seen): a box over one summed segment lights its 40 rows in the
  table ("40 of 135 rows"); a stack coloured by value shows the refusal; P39's joined
  band and lone dots. Known and left: a field the summed rows do not share shows "—" in
  the tooltip, which reads like a missing value.
- **P41 — Review round 20's findings.** Round 20 (four lenses at 6432260f) found that
  P40's sum dropped every field outside slot and colour: a stack whose `keys:` name a row id
  wrote `{}` on a brush (clearing every linked view), a marking by id could not light it, and
  a `highlight:` on another field was refused or silently lost. **Decision [user,
  2026-09-26]: a stack links by slot and colour only.**

  | # | Found | Rule installed |
  |---|---|---|
  | 21 | A stack's `keys:` / `highlight:` naming fields the sum drops | A stack links by its slot and colour only [user]: `validate` refuses a stack whose `keys:` name any other field, or whose `highlight:` reads any other field (`where:` columns, `values:` columns), saying why and that single rows link when not stacked; a `where:` on the value field tests each segment's sum, and says so. A marking on other columns does not light a stack. And whatever reaches it, a selection that can name no key marks nothing — never `{}` (the table's rule) |
  | 22 | `_shared` numbered only groups that occur while `aggregate` grouped every category combination (`observed=False`): with categorical columns a segment took another's tooltip, and phantom 0 rows appeared | One grouping, the observed one, for the sum and for what is kept |
  | 23 | A kept uint64 ≥ 2^63 crashed the query (cast to Int64) | Kept integers keep their signedness (`UInt64`) |
  | 24 | The lone-point pass drew a stack's filler as a point, and read the category dimension of a horizontal line | Only a row's own point is a lone point; the value dimension is the value axis's |
  | 25 | A stack whose value channel is temporal or a category summed nanoseconds / text | A stack's value channel is quantitative; anything else is refused |
  | 26 | Two channels aggregating one field by different ops: the first op won, and the tooltip labelled max showed the mean | One field, one op: two different ops on one field are refused |
  | 27 | `write` returned true when `ifEmpty` found the marking occupied and wrote nothing | The store's set says whether it wrote; `write` returns that |
  | 28 | The horizontal slot's `ordinal` half and the value kind line were unpinned | The ordinal horizontal case is in the stack corpus (both halves); the value-kind line is removed (row 25 makes it unreachable) |

  Also: a stack on a log axis sums its negatives into the segment, as a sum does (the
  note counts what the axis leaves out, not what a sum absorbed) — documented, not
  changed. Row 21 changes what `validate` accepts, so round 21 follows.
- *A note on three commit bodies:* 802adbb2, 1aaa0b0f and 3f6bcc72 name the commit their
  red-before run used on the builder's branch (e4bbdfba, 8bc4b8ee, 381f7608). Those are
  not on this branch; their code is 14b81f9f's, 802adbb2's and 83c3f004's (the third
  differs only in docs). And ed3164f3's "red before … (3)" and "`v <= 0` counted as
  `v < 0` -> 1 red" were counted before the rule test was added; on the committed test
  file they are 4 and 2 (round 19 veracity F2).

## Verification

- Every phase starts from a test that reddens on the code before it. A guard is proven
  by mutating its line. Parity tests use an oracle (pandas for sort and stack; `canon`
  for table keys).
- Targeted tests, `ruff` / `ty` / `pnpm typecheck`.
- Review rounds with the four lenses in parallel, then CI on the final sha.
