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

Byte-identity does not follow from the quoting rules alone, because `parseCsv` is
lossy in two ways: it drops `\r`, and it cannot tell a trailing newline from its
absence. Left alone, opening a CRLF file rewrites every line ending, and a file
with no final newline grows one. So `serializeCsv` takes the original text and
derives both styles from it; the round-trip is pinned by a test using CRLF input
with no trailing newline.

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

## Selection, clipboard and history

Phases 1-4 give you a grid you can type in one cell at a time. This part makes it
behave like a spreadsheet: select a block, copy it, paste it — including to and
from Excel — and undo when the paste was wrong.

### Decisions

**The clipboard is TSV on `text/plain`.** That is what Excel and Google Sheets
put there, so copy-out and copy-in both work with no conversion step and no
bespoke format. It costs nothing to implement either: `parseCsv` / `serializeCsv`
already take a delimiter, so the clipboard is the same code with `"\t"`.

**One rectangular range, not disjoint selections.** Ctrl+clicking several
separate blocks is a different data model (a list of ranges) and every operation
— copy shape, paste anchor, clear — has to answer "what does this even mean" for
it. Out of scope; the range covers the cases that matter.

**Paste grows the grid, and never tiles.** Anchored at the selection's top-left;
if the block overflows the sheet, rows and columns are added. Excel grows too,
and a paste that silently drops its last rows is the worst kind of data loss.
Excel also TILES a small clipboard into a larger selection — that one is a
surprise more often than a feature, so a paste writes the block once.

**Paste takes over whenever the clipboard holds more than one cell**, whatever is
selected: the common move is to copy a block in Excel, click ONE cell here and
paste, and a selection-size rule would refuse exactly that. A single pasted value
goes into the selected cell while selected, and into the text while editing.

**Two modes, like Excel: a click SELECTS, a double-click EDITS.**

This reverses the original decision, which was to have no mode at all — every
cell a live input, plain arrows left to the caret, and selection only via
shift-click or Shift+Arrow. That was wrong in use, for a reason the design never
predicted: **you could not copy a single cell.** Clicking put you in a text
input, and the "one cell keeps its native copy" rule then handed Ctrl+C to an
input with no text selected — so the most ordinary spreadsheet action in
existence did nothing.

So: a single click selects. Double-click, F2, Enter, or simply typing enters edit
mode on that cell (typing replaces, as Excel does). Enter / Tab commit and move,
Esc reverts. While selected, plain arrows move the selection and Shift+Arrow
extends it — which is what having a mode buys, and why Excel has one.

Copy therefore follows the MODE, not the size of the selection: selected ⇒ the
grid copies the cells (one or many); editing ⇒ the input copies text, so half a
value is still copyable. The old size-based rule is gone, not layered under this
one — two rules for one question is how one of them ends up lying.

**Row and column selection.** Clicking the row-number gutter selects that row;
clicking a column header selects that column, header cell included, since the
header really is a line of the CSV. Shift-click extends by whole rows / columns.
Renaming a column is a double-click on its header — the same click-selects,
double-click-edits rule as any other cell, rather than a second convention for
the header band.

**Selection is in DISPLAY coordinates.** With a sort active, a rectangular
selection on screen maps to scattered rows in the file — copy and paste operate
on what you see, and the write maps back through the file index, the same rule
the cell editor already follows.

**Undo is sheet-local, and says so by disappearing.** It is a stack of the
sheet's own edits, not a file history. When the byte editor takes over, or the
file changes underneath, the stack is dropped rather than kept around pretending
it still lines up with the content — a "redo" that reapplies a block onto a file
someone else rewrote is worse than no redo at all.

### Degradation

| situation | behaviour |
|---|---|
| paste into a read-only sheet (either axis) | refused, like every other edit |
| clipboard holds a single value, selection spans a block | writes that one value once at the anchor — no tiling |
| pasted block is ragged (fewer cells in some lines) | short lines pad, so the grid stays rectangular |
| Delete / Backspace on a range | clears the contents; it does NOT remove rows |
| undo after the byte editor or an outside change | the stack is empty; Ctrl+Z does nothing rather than reapplying stale state |

### Phases

Ordered so nothing destructive lands before the thing that can take it back.

**Phase 5 — range selection and copy.** Click, shift-click, drag and Shift+Arrow
define a rectangle; the selection is visible; Ctrl+C writes TSV. Non-destructive
from end to end, which is why it goes first.

**Phase 6 — undo / redo.** Ctrl+Z / Ctrl+Shift+Z over the sheet's own edits —
which retroactively covers the cell edits and the row/column operations from
Phases 1-2, the ones that have had no way back all along.

**Phase 7 — cut and clear.** Ctrl+X and Delete on a range. Destructive, so it
lands after undo exists.

**Phase 8 — paste.** Ctrl+V, growing the grid. The most destructive operation in
the feature, and the last one added.

**Phase 9 — the two modes.** A click selects, a double-click (or F2 / Enter /
typing) edits. Plain arrows move the selection, Shift+Arrow extends. Copy follows
the mode. This is the foundation the next phase needs, and it retires the
size-based clipboard rule.

**Phase 10 — row and column selection.** Click the gutter for a row, the header
for a column; shift-click extends. Column rename becomes a double-click on the
header, so the header obeys the same rule as every other cell.

## Verification

- Each phase runs through `/tdd` (failing test first) and gets its own commit.
- Tests are vitest. The pure functions carry the weight: `serializeCsv`
  round-trip against `parseCsv`, and every structural edit.
- Component tests cover keyboard navigation, the read-only gate and each row of
  the degradation table.
- Layout-dependent behaviour (virtualisation, frozen header) is not measurable
  in happy-dom and must be checked in a real browser. Measured in headless
  Chromium against a 10,000-row file: first paint 64 ms; 102 cell inputs in the
  DOM at the top and 117 after scrolling 60,000 px (not 30,000); the header stays
  at y≈35 throughout; the window follows the scroll (row 2497 at scrollTop
  60,000); a cell 2,500 rows down edits normally; sorting keeps row 1 pinned and
  offers "Apply this order to the file".

  That pass is not optional decoration — it caught a defect no unit test could:
  the header's sort control was **unclickable**, because the cell input is
  `width: 100%` and painted over it, so a real click landed on the input. The
  component test passed because Testing Library dispatches events at the element
  without hit-testing. The header is now a flex row for exactly this reason.
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
