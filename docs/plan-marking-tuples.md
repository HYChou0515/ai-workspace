# Plan — a marking remembers the picked rows (#861)

#855 (merged at `e45a8e6b`) stores a named marking as **column → set of values**, and
each view tests each column on its own. A marking over two or more columns therefore
lights every combination of their values: boxing four gallery tiles (g3, 8), (g4, 1),
(g5, 1), (g5, 2) lights nine, and Save as table, the message chip and
`.markings/<name>.json` all carry the nine. #855's plan P27 kept this and only labelled
it ("by group, item"). The user reversed that on 2026-09-27 after the demo **[user]**.

Sources are tagged: **[user]** decided by the user in the 2026-09-27 grill-me, with the
reference products they chose to follow; **[mine, open to override]** decided by the
builder.

## Decisions

- **D1. What lights is what was picked [user].** A marking stores the picked **key
  tuples** — `keys: [group, item]` and rows `(g3, 8), (g4, 1), …`. A single-key marking
  behaves exactly as today.
- **D2. A view coarser than the marking lights the element that contains a picked row
  [user, following Spotfire].** A view that has only some of the marking's keys (a
  per-group summary file, a milestone chart beside an issue table) projects the tuples
  onto the keys it has: `(g3, 8), (g4, 1)` onto `group` is `{g3, g4}`, so those whole
  bars light. This is today's behaviour for such views. Spotfire propagates marking
  between related tables through the key relation; Power BI does so only with a
  bi-directional relationship.
- **D3. An aggregated bar shows the picked part [user, following Power BI's
  cross-highlight].** The bar keeps its full height, dimmed; in front of it a lit bar
  whose value is **the same aggregate over the picked rows only** (count 10 with a1, a2
  picked → a lit bar of 2). For a mean the lit bar is the picked rows' mean, drawn at its
  own height even when it is taller than the dimmed one **[mine, open to override —
  neither Power BI nor Spotfire documents that case]**.
- **D4. Other aggregated elements light whole [user, following Spotfire].** A heatmap or
  grid cell, a box, an errorbar, a pie slice, a point of a mean line: lit, with its own
  colour and shape unchanged, when it contains a picked row; dimmed otherwise. Spotfire:
  "if items in other visualizations include any of the marked data rows, they become
  marked too … the marked items keep their original colors".
- **D5. Counts say how many were picked [user, following Spotfire's status bar].** *[As built, for the user to confirm: a chart's "N selected" counts the selected rows, as a single-layer chart did on #855 and as Spotfire's status bar counts marked rows (a row known by its keys, so a row two layers draw counts once) — review #862 found that counting distinct picks changed a single-key chart's count (D1); the chip counts picks, a gallery its lit tiles, a table its lit rows.]* Every
  view keeps its line format with the true number: "4 of 48 marked · by group, item",
  "4 selected · by group, item", "filtered by m1 · 4 of 18 rows · by group, item". The
  message chip becomes "m1 · 4 · by group, item" (not a per-column count).
- **D6. No cap on how many are picked [user, following Spotfire].** The AI gets one line
  (name, count, path) and reads the file when it needs the rows. Power BI caps a
  selection at 3,500 points; a cap here would bring back "what lights is not what was
  picked".

## Built this way [mine, open to override]

- **Store shape.** `Marking = { keys: string[]; tuples: Set<string> }` where a tuple is
  its values joined by a separator no value text contains (U+001F), so matching is one
  hash lookup. `isLit(row, marking)` projects onto the keys the row has: no shared key →
  not lit (callers keep drawing such a view undimmed); every key shared → exact tuple
  membership; some keys shared → membership in the projection (D2), computed once per
  marking and key subset. One implementation in `web/src/lib/markings.ts`; the chart plugin calls it through the SDK. The sandbox's `lit_rows.py` is a second
  implementation, tested against a Python copy of the rule — not the corpus parity this line
  first promised (review #862 A3/A7); the two differ on a row whose key cell is empty (see
  Known and left).
- **Writing.** A selection writes the tuples of its `keys:` columns over the picked rows
  (`projectOntoKeys` returns tuples). A table keeps #855's "a table only decides for
  rows it has": ticking writes the table's own tuples and keeps the held tuples none of its
  rows carries. The gallery's rank range writes each group in the range as one tuple.
- **Partial bars (D3) without a round trip per brush.** The sandbox answer for an
  aggregated bar layer gains, per bar, the decomposable partials of its rows grouped by
  the marking's keys — count, sum, min, max (mean = sum / count). The browser sums the
  lit partials on every marking change; the sandbox is asked again only when the
  marking's **key set** changes (a different view wrote it), never when its values do.
  A median bar, or a bar layer whose partials would exceed 200,000 entries, lights whole
  per D4 and its note line says why.
- **Where D3 applies.** Bars and stacked bar segments only — the one mark both reference
  products document partial highlighting for. Area, pie and the rest follow D4.
- **File and wire format.** `.markings/<name>.json` becomes
  `{"name", "sources", "keys": [...], "rows": [[...], ...]}`; the cross-tab message and
  `MarkingInput` carry `keys` and `rows`; `SentMarking.counts` becomes `count`; the
  digest hashes keys + sorted rows. A chip saved before this change fails Save as table: "the marking's file does not hold a
  marking" while its file is still in the old shape, "has changed since this message was
  sent" once the name was sent again (review #862 A1). No reader accepts
  the old `columns` shape — two formats would be two rules.

## Formats (fixed before the phases split, so the halves are built to one contract)

- **Browser** (`web/src/lib/markings.ts`): `Marking = { keys: readonly string[];
  tuples: ReadonlySet<string> }`. `keys` are **sorted** — two views writing the same
  columns in a different `keys:` order hold one marking — and a tuple is the row's
  values in that order joined by U+001F. "by …" says the keys in that order.
  `markingFrom(keys, rows)` / `markingRows(m)` convert to and from `string[][]`.
- **Cross-tab** (`markingsSync`): `{kind: "set", name, keys, rows: string[][] | null,
  source}` (`rows: null` clears).
- **Send** (`MarkingInput`): `{name, source, keys: string[], rows: string[][]}`.
- **File** (`.markings/<name>.json`): `{"name", "sources", "keys", "rows"}`, keys
  sorted, rows sorted and distinct.
- **Chip** (`SentMarking`): `{name, path, count, keys, source, error, digest}` —
  `count` is the number of picks (distinct rows of the file), `keys` what "by …" says.
- **Digest**: sha256 of `json.dumps({"keys": keys, "rows": sorted distinct rows},
  ensure_ascii=False, separators=(",", ":"))`.
- **Save as table** body: `{name, view, marking: {keys, rows} | null, stamp, digest}`;
  the sandbox `marking_rows` command takes `{"view", "keys", "rows"}` or
  `{"view", "marking": <path>}`.

## Phases

- **P1 — The platform marking holds tuples.** `markings.ts` (types, `isLit`,
  `projectOntoKeys`, store, `markedBy`, count), `markingsSync.ts`, `useMarking`, SDK
  re-exports. The wire corpus gains tuple cases; every P27 combination test is rewritten
  to D1 and still has a red run against the old code.
- **P2 — Tables.** `useTableMarking` (entity `table`, `csv-table`): read by tuple, write
  tuples, the counts of D5.
- **P3 — Chart plugin, per-row and whole-element lighting.** `markingLit`,
  `selectionMarking`, `toMarking`, `markedCount`, `highlightMarking`, `stillWritten`; the
  gallery's `rangeMarking`, `groupsLit`, `cellsLit`; D2 and D4.
- **P4 — Partial bars (D3).** Sandbox partials in `query.py`, the web draw (dimmed full
  bar, lit bar from the axis with the rest dimmed on top), the fallback and its note. Both
  halves parity-tested (`bar-partials.json`).
- **P5 — Backend and Save as table.** `api/markings.py` file shape and digest,
  `SentMarking`, the chip (D5), `lit_rows.py` tuple matching, `marking_table.py`.
- **P6 — The AI and the docs.** `SKILL.md`, the views index line, `docs/view-plugin-chart.md`,
  `docs/view-kind-authoring.md`; `docs/migrations.md` entry (the `.markings/` shape
  changes and old chips can no longer be saved).
- **P7 — Live check and demo.** The #855 demo scenes that exposed this (gallery box of
  four, linked table, chip, Save as table) plus a partial-bar scene, at 1440 and 390,
  frames seen before merge.
- **P8 — Review rounds** (conformance / veracity / defect / regression), then CI on the
  final sha.

## As built

- **P4 [mine, open to override, from the builder]:** `partials` is its own sandbox
  command, not an argument of `query` — re-running `query` would redraw the chart and
  drop the brush just drawn. The schema has no median op; every op it has (count, sum,
  mean, min, max, rate) folds, so "lights whole" is left for an op that cannot be split
  and for more than 200,000 parts. A split bar is drawn as two series in one slot (the
  lit part from the axis, the rest dimmed on top), so layout, legend, colour and brush
  mapping are unchanged. Only on a plain number value axis: a log axis has no 0 to start
  a part at, so its bars light whole (no note). A stack segment whose picked part does
  not fit inside it (a mixed-sign sum, a mean stack) lights the stack whole, with a note.
  A selection across layers that carry different key subsets writes picks over the keys
  every naming layer gave a value.
- **P5:** a chip stored before #861 reads back with no keys and a count of 0; it shows its
  name alone. The sandbox refuses the old `columns` shape rather than guessing.
- **SDK:** the marking shape is SDK surface, so the view SDK major moved to 2; a parity
  test ties the scaffold's and the first-party plugins' `"sdk"` to `sdkVersion.ts`.

- **Review round 1 (#862):**
  - A table coarser than its marking (a per-group table on (group, item) picks) keeps writing at
    the marking's keys while its ticks say only what the held picks say: unticking a group
    removes that group's picks and keeps the rest; ticking a group with no pick writes at the
    table's keys (defect A1: it replaced the marking and dropped picks it did not show, a
    regression from #855).
  - A chart's "N selected" counts the selected rows that name a whole pick, as on #855 (the P43
    rule), not the distinct picks (regression A1: a single-key chart said "2 selected" for 8
    brushed points, where D1 says a single key behaves as before). The gallery's "N of M marked"
    counts tiles and the chip counts picks, as D5 says; a table counts its lit rows.

- **Review round 2 (#862):**
  - A coarser table's tick of a group with no pick, written at the table's keys, keeps the
    held picks of groups it does not show (as whole groups, as #855 carried them) — the tick
    path of round 1's A1.
  - Only a table that LACKS one of the marking's keys takes the coarser path: a table that has
    them all but writes fewer (`keys:` a subset) wrote plainly at its keys (round 1 had made its
    ticks a silent no-op; round 3 added the carry below).
  - A chart counted rows summed within a layer and the most of any layer across them, so a line
    with its points counts each row once (was 6 for 3 rows, as on #855).
- **Review round 3 (#862):**
  - Round 2's largest-layer count under-counted layers that draw different rows (each its own
    `transform:`): two layers of three rows each said 3 while the marking held 6. A row is known
    by its keys (the marking's own rule), so the count is, per pick, the most rows any one layer
    selected for it, summed: a line with its points counts 3, disjoint layers 6, overlapping
    boxes over {r1, r2} and {r2, r3} count 3.
  - A table with every key but a `keys:` subset now keeps the held picks of groups it does not
    show, as whole groups, as the coarser table does.
- **Review round 4 (#862):** no finding in the chart count (per pick, the most rows of one layer
  is never below the largest layer's total, and equals it for one layer). The table header and
  the chart doc said "a held pick no row of this table carries stays picked" for every write at
  the marking's keys or some of them; at fewer keys that holds per group the table shows, not
  per pick — a finer pick in a shown group goes with that group's tick, as #855 decided a shown
  value. The sentences now say so and two tests pin it (untick-all keeps only unshown groups; a
  finer pick in a shown group goes). A table writing other keys replaces the marking, now in the
  runbook.

## Known and left (each (B): rare input or cosmetic)

- A row whose key cell is empty: the browser reads it as coarser and lights it when its group
  holds a pick; the sandbox (`lit_rows`, `partials`) does not. So a table can count such a row
  that Save as table leaves out. Already so on master; rare in key columns.
- A mean (or min/max) bar whose picked value is longer than the bar hides the dimmed bar behind
  it; the bar's own value stays in the tooltip.
- The tooltip's "picked: N" is the unrounded number.
- Key and row order sort by UTF-16 code unit in the browser and by code point in Python; only
  astral characters in names differ, and no digest depends on the browser's order.
- A selection across layers that carry different key subsets writes the keys every naming layer
  gave a value, so brushing points and a per-group bar together makes the picks per group.
- Commit bodies that say otherwise (they cannot be rewritten after the push): b3e92c59 and
  c3fcc2f3 say every count is picks (a table counts its lit rows; a chart counts rows, above);
  51c20d5c says an old chip gets "has changed" (see above); 4dc433dc says Save as table saves
  "those four rows" (it saves every source row of the four picks); 1d090865 says a
  bar "keeps its full length, dimmed, with a lit bar in front" (as built, the lit part is
  drawn from the axis and the dimmed rest on top of it); 756406e5 says a chart takes the most
  of any layer and a table with every key writes plainly (round 3 replaced both, above);
  f32d1d5e says unshown picks stay while a table writes the marking's keys or some of them (per
  group the table shows, round 4).
- A `keys: [group]` table beside (group, item) picks decides every group it shows: ticking another
  group drops a finer pick in a shown group that no row of the table carries (keeping it at
  `[group]` would light the whole group).
- A row is known by its keys: two different source rows two layers draw with the same key values
  count once in "N selected" (the answer carries no row identity across layers; the marking
  already holds them as one pick).

## Done means

Boxing four tiles lights four, counts four, sends four to the AI and saves four tiles'
rows; a per-group bar beside it shows the picked count from the axis with the rest dimmed on top; a
per-group summary chart lights the groups that contain a pick.
