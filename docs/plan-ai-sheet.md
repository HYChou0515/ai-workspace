# Plan — `*.ai.csv` spreadsheet editing

## Goal

`*.ai.csv` / `*.ai.tsv` open in the workspace IDE as an **editable spreadsheet**:
click a cell and type, Tab/Enter to move, add and remove rows and columns,
rename a column, sort, frozen header, tens of thousands of rows without
stalling.

Today `.csv` / `.tsv` render as a read-only `DataGrid` capped at 500 rows, and
`Edit` flips to the raw Monaco byte editor — so editing tabular data means
editing text.

The `.ai.` marker is the **opt-in**: it says "this CSV is meant to be edited as a
spreadsheet". A plain `.csv` keeps today's cheap read-only preview, so nothing
regresses for files that are only ever looked at.

One file, no side file. What is on disk is an ordinary CSV that Excel, pandas,
the KB ingest and the agent all read without special handling.

## Non-goals

- **Formulas.** Cells hold values that someone typed or an agent wrote.
- **xlsx interop.** See the note at the end.
- **Any backend change.** `.ai.csv` is bytes to the server, like every other
  file.

## Renderer routing

| path | resolves to | note |
|---|---|---|
| `wafers.ai.csv`, `wafers.ai.tsv` | **new** `sheet` entry, `/\.ai\.(csv\|tsv)$/i` | must be anchored **before** the existing `csv` entry, which also matches by extension |
| `plain.csv`, `plain.tsv` | unchanged `csv` (read-only `DataGrid`) | |
| `views/board.ai.yaml` | unchanged `aiview` | untouched by this plan |

Verified against the real regexes, including that the new rule does not capture
`views/*.ai.yaml`.

`registry.ts` is the single place this is declared; its own docstring states that
adding a preview type is one appended entry plus a component, and that
everything else (dispatch, padding, edit toggle) derives from the list.

## Design decisions

**The grid is the editor, not a preview.** Every other structured type
(`json` / `jsonl` / `yaml` / `csv`) is a read-only projection whose `Edit`
toggle drops to the byte editor. A spreadsheet inverts that: the grid itself is
where you type. The raw byte editor stays reachable — it is the escape hatch
when a file is malformed enough that the grid cannot help — so the repo's
"any file is editable" property holds.

**Saving is the same as every other file.** Cell edits go through the existing
`useFileBuffer` `setText` / `save`, so the dirty marker, unsaved-state
indicator and save shortcut behave exactly as they do in Monaco. No bespoke
save path, no autosave surprise.

**Sort changes the view, not the file.** `TableView` already states this rule
for its own sort/filter/column toggles ("local + ephemeral to the open panel").
Rewriting row order on disk as a side effect of clicking a header would be an
invisible whole-file change. Reordering the file is available as an explicit
action.

**Presentation state is not persisted.** Column widths, freeze and sort live in
the open panel only. This is what keeps the design a single file with no meta
file: nothing needs to be remembered between sessions.

**Serialisation must round-trip.** `serializeCsv` is the exact inverse of the
existing `parseCsv`: quote a field only when it contains the delimiter, a quote
or a newline; escape `"` as `""`; tab delimiter for `.tsv`. A file opened and
saved without edits must come back byte-identical, or the grid silently rewrites
files just by being opened.

## Degradation

Each row is a test.

| situation | behaviour |
|---|---|
| a row has fewer or more fields than the header | render it, mark it, name the row — never blank the pane. One bad row must not cost the whole file (the lesson from **#646**) |
| the file is not decodable text | fall back to the byte editor with a plain explanation, rather than an empty grid |
| the file is empty | an empty grid with one editable cell, not an error |
| the member has no write permission | the grid is read-only: no cell editing, no row/column affordances (`useItemCanWrite`) |
| the file changes underneath an open grid | say "this file changed outside the editor" and let the user choose reload or keep; never silently merge, never silently discard unsaved cells |

## Phases

**Phase 1 — the editable grid.**
`serializeCsv` as a pure, round-tripping inverse of `parseCsv`. Cell editing;
Tab / Shift-Tab / Enter / Shift-Enter navigation; Esc cancels an edit. Saving
through `useFileBuffer`. Read-only members gated by `useItemCanWrite`.

**Phase 2 — structural edits.**
Add and remove rows, add and remove columns, rename a column — each as a pure
function over `string[][]` so the behaviour is testable without a DOM.

**Phase 3 — large files, frozen header, sort.**
Virtualised rows (render only what is visible), so the 500-row cap that the
read-only `DataGrid` needs does not apply here. Frozen header. Header-click
sort, view-only, plus the explicit "apply this order to the file" action.

**Phase 4 — robustness.**
Every row of the degradation table, including outside-change detection via the
existing `file_changed` broadcast (`useEntityLiveSync` is the same shape).

## Verification

- Each phase runs through `/tdd` (failing test first) and gets its own commit.
- Tests are vitest. The pure functions carry the weight: `serializeCsv`
  round-trip against `parseCsv`, and every structural edit.
- Component tests cover keyboard navigation, the read-only gate and each row of
  the degradation table.
- Layout-dependent behaviour (virtualisation, frozen header) is not measurable
  in happy-dom and must be checked in a real browser.
- Before delivery: `pnpm run typecheck`, whole-project `uv run ty check`,
  `ruff check` + `ruff format --check`. The full suite and the 100% coverage
  gate run in CI.

## Why not xlsx

The obvious question for anything spreadsheet-shaped. Measured while scoping
this:

- The FE has no xlsx library and no grid library today, so the format costs a
  new dependency for reading and writing before any feature exists.
- An xlsx is a zip of XML: the agent cannot grep it, put it in a prompt or diff
  it, and git history becomes a blob. A CSV is text for every reader.
- Read-modify-write through a JS xlsx library **silently drops** what the
  library does not model (charts, pivot tables, conditional formatting). A user
  edits one cell, saves, and loses a chart — silent loss inside a single binary
  file, unrepairable by hand.
- Recalculation, if it were ever needed, requires LibreOffice; only
  `libreoffice-impress` and `-draw` are installed on the current host (which is
  also why the deck tool works), so `libreoffice-calc` would have to be added to
  the sandbox image.

CSV is the format. If Excel interop is wanted later, the shape that fits is a
one-way export, planned separately.

## Found while scoping

**#646** — any CSV row whose field count differs from the header aborts KB
ingestion with an unactionable `AttributeError: 'NoneType' object has no
attribute 'strip'`, surfaced verbatim to the user. Pre-existing and independent
of this feature; reproduces with a plain ragged CSV.
