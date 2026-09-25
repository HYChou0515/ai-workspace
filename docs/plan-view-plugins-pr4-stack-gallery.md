# Plan — PR 4: stack, diff and facet at scale

Part of [plan-view-plugins.md](plan-view-plugins.md). The decisions are Q3, Q11, Q12 and
Q13. This PR stacks on PR 3 and is #848's acceptance at the scale the user named:
hundreds to thousands of groups. The mechanism stays generic. "Wafer" appears only in
fixtures.

## Done means

- A `grid` spec with a cross-group `aggregate` stacks N groups into one lattice.
- The same spec with a diff shows group A − group B.
- A `facet:` spec over 1000+ groups opens as a gallery:
  - sorted over **all** groups;
  - virtualized, with pages fetched as the user scrolls;
  - a click enlarges one group at full resolution with exact tooltips;
  - a selection over sorted positions writes the complete set into the marking,
    including groups not yet loaded.
- The source can be CSV or parquet.

## Start gate and hand-off

The PRs are stacked, and a later PR builds on the earlier one's interfaces. The rules:

- **Rebase, never merge.** When the PR below yours pushes, rebase onto its branch tip.
- **Freeze notice.** When you reach your freeze point, comment on the PR above yours
  with the sha and the list of frozen contracts. After that, a change to a frozen
  contract gets its own comment on every PR above yours, saying what changed and why.
- **Early phases** touch only code that no earlier PR defines. Anything else waits for
  the gate.

- **Gate: #856's freeze notice (P2).** That notice implies #855's freeze (P4 output
  format, P6 `grid` host), because #856 stacks on #855.
- **Early phases:**
  - P2's cache format and its round-trip tests, which are pure Python;
  - P1's raster core as a pure function. Its parity test needs #855's `grid` host, so
    that test waits for the gate.
  - P3's builder and P4's pager, which can start once #854 has frozen, because they only
    need the runner contract.
- **Freeze point:** none. Nothing stacks on this PR.

## Phases

**P1 — the shared raster core.**

- `(cells, colour scale) → ImageData`, with one colour-scale function.
- Thumbnails paint it on a bare canvas, and the full view embeds it in ECharts (PR 2 P6).
- The parity test: the same cells go through both paths and must produce identical
  pixels.
- A mutation probe edits the scale on one path and confirms the test reddens.

**P2 — the cache format.**

- Built once per `(source path, size, mtime, spec transform hash)`.
- Contents:
  - an index: per-group key, sort values, and offset;
  - fixed-size per-group records: 1 byte per cell for categories, 256-level quantized
    for continuous values, and a header that holds the scale's range.
- Tests pin the round trip and that any key component changes the cache path.

**P3 — the builder.**

- Runs as a sandbox command that reads CSV or parquet once and writes the cache to
  `.home/.cache/views/`, the infra area (Q12).
- Reports progress lines, which the view shows during a first open.
- Enforces the LRU cap, default 500 MB and a spec-level knob, on every build.
- Sized by rule of thumb, not measured (Q11): about 3–5 s for 1000 × 5000 from CSV.
  The phase's test proves correctness, not speed.

**P4 — the pager.**

- A sandbox command with no pandas import. It slices `[i, j)` records out of the cache
  and returns base64.
- A missing cache, for example after a reap, is rebuilt transparently.
- The per-call argv stays tiny: the cache key plus a range.

**P5 — stack and diff.**

- A cross-group `aggregate` over `groupby: [x, y]` returns one lattice.
- Diff is two aggregates over the two groups the spec names, subtracted.
- Both go through PR 2's `query` and reuse its output format.

**P6 — the gallery.**

- `facet:` renders a virtualized grid of thumbnails. Only visible rows mount, and pages
  are fetched as they approach the viewport.
- The page size derives from record size: target about 1–2 MB per page, so tens of
  groups at 50k cells.
- Sorting uses the index already in hand, with no rebuild.
- Enlarging fetches the one group's exact values.

**P7 — selection over positions.**

- Box selection and range selection (e.g. ranks 1–30) are over **sorted positions**, not
  mounted elements. They resolve to group keys from the index and write the full set to
  the marking.
- A test selects across unloaded pages and asserts the complete set.

**P8 — docs and runbook.**

- The facet, stack and diff reference in the chart docs.
- A `docs/migrations.md` entry. It is needed because the scratch volume now holds view
  caches, bounded per sandbox by the LRU cap and reaped with the sandbox. The entry
  covers:
  - scratch sizing;
  - the knob;
  - the check that confirms it: open a gallery and see `.home/.cache/views/` in the
    sandbox dir.

## Verification

- Targeted tests, the four gates, and typecheck.
- Live check in a real browser on generated fixtures:
  - 1000 groups × 5000 cells as CSV;
  - the same as parquet;
  - 200 groups × 50 000 cells.

  The checks: first open shows progress; scrolling fetches pages; sort changes cost no
  rebuild; a rank-range selection lights the linked scatter from PR 3; a reap followed
  by a reopen rebuilds.
- Base differential on PR 3's tip, where a `facet:` spec is rejected by `validate`.
