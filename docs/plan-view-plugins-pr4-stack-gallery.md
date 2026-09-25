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
  - an index: the facet columns, and per group its key (one text value per facet
    column, so a marking gets `column → values` back) and sort values. Offsets are
    derived from the index, never stored;
  - fixed-size per-group records: 1 byte per cell. A category code, or a continuous
    value quantized to 255 levels; code 255 is a missing cell. The header holds the
    scale's range or labels;
  - for continuous scales, a float64 section with each group's exact values, for
    P6's enlarge;
  - a random build id per write. A page read through an index from an earlier build
    fails instead of slicing the new file (inode and mtime cannot tell: ext4 reuses
    inodes at once).
- The header is strict JSON, since the browser parses it. The format takes plain
  Python values only and refuses the rest by name; converting pandas/numpy values is
  P3's job.
- Tests pin the round trip and that any key component changes the cache path.
- **Built:** `chart_view.facet` in the chart plugin's sandbox half
  (`view-plugins/chart/sandbox-src`): `write_cache`, `read_index`, `read_groups`,
  `read_records`, `read_exact`, `CacheUnusable`. It imports only the standard
  library, so the pager never loads pandas, and it runs under the half's own CI
  job (`chart-plugin-test`, 100% coverage).

**P3 — the builder.**

- Runs as a sandbox command that reads CSV or parquet once and writes the cache to
  `.home/.cache/views/`, the infra area (Q12).
- Reports progress lines, which the view shows during a first open.
- Enforces the LRU cap, default 500 MB and a spec-level knob, on every build.
  - It counts `*.vcache` **and** `*.tmp` (`TMP_SUFFIX`): a SIGKILLed build leaves its
    temp file. It removes a `*.tmp` only when stamped more than a grace period (1 h)
    from now, either way, because removing a live build's temp file fails that
    build's `os.replace`. "Now" is the file system's own clock, so on NFS a pod clock
    that is off does not age a live build's temp file.
  - Recency is the mtime the pager sets on every read (`os.utime`); atime is
    unreliable on NFS.
  - **Built:** `chart_view.facet.cap.enforce_cap`, and the builder
    `chart_view.facet.build.build_facet_cache(frame, facet, x, y, value, sort, path,
    x_type, y_type, progress)`.
    - Keys are `canon()` of each facet value, so they match a linked view's marking.
      A missing key (NaN, NA) is refused, never keyed `"<NA>"`.
    - Cell x / y are asked of the chart itself: the wire kind `query._kinds` gives a
      grid's x / y channels, then `encode_column` of that kind, decoded. A
      thumbnail's `lattice` gets exactly what the full view's gets.
    - A row with no x or y is left out, as `lattice` leaves it out, and the progress
      lines say how many.
    - A group all of whose rows are left out stays, as an empty thumbnail, so a
      marking that names it still finds it.
    - Two rows in one group's cell, or a sort value that varies inside a group, is
      refused by name; aggregating belongs to the transforms.
    - A bool value is a `true` / `false` category.
    - Still to wire: reading the frame through `read_source` and the spec's
      transforms, and the `facet_build` launch command with the cap on every build.
      That waits for P6, where `facet:` joins the spec schema.
- Converts what the format refuses, in one place, with a test per rule:
  - key values to text, the same way PR 2's `query` stringifies the values a linked
    view compares against;
  - dates and times to a number (UTC epoch ms), so they sort in time order across tz
    offsets and stay exact in `JSON.parse`; `NaT` to null;
  - numpy scalars to Python values, and `pd.NA` to `None`.
- Sized by rule of thumb, not measured (Q11): about 3–5 s for 1000 × 5000 from CSV.
  The phase's test proves correctness, not speed.

**P4 — the pager.**

- A sandbox command with no pandas import. It reads records out of the cache and
  returns base64.
  - A page is a list of positions, not a range: P6 sorts from the index, so a sorted
    page is scattered positions (`read_groups`).
  - The exact-values call (`read_exact`) can return `±inf`, which JSON cannot carry;
    the output encodes it.
- A `CacheUnusable` (missing after a reap, cut short, corrupt) is rebuilt
  transparently.
- A page or exact read names the build its positions were sorted from. If the cache
  is any other build, the pager raises `StaleIndex` rather than `CacheUnusable`: the
  gallery refetches the index. Rebuilding again would mint yet another build and fail
  the page a second time. A cache of the same build that is cut short is still
  `CacheUnusable`, because refetching would find the same broken file.
- **Built:** `chart_view.facet.pager` (`index_payload`, `page_payload`,
  `exact_payload`). Answers use PR 2's wire shapes (`q8`, width-1 `cat`, `f64`).
  They are wired as the chart bundle's launch commands (`chart_view.facet.cli`):
  - `facet_index {"key"}`
  - `facet_page {"key", "build", "positions"}`
  - `facet_exact {"key", "build", "position"}`

  The cache root is `~/.cache/views`, since the isolated launcher's HOME is the
  sandbox's `.home`. Exit codes:
  - 0: the JSON answer;
  - 2: a wrong call;
  - 3: the cache cannot be used; build it again;
  - 4: stale; refetch the index.

  `chart_view.cli` imports nothing heavy at module level, so these commands never
  load pandas (checked in a fresh process).
- The per-call argv stays tiny: the cache key plus a page's positions (tens of ints).

**P5 — stack and diff.**

- A cross-group `aggregate` over `groupby: [x, y]` returns one lattice.
- Diff is two aggregates over the two groups the spec names, subtracted.
- Both go through PR 2's `query` and reuse its output format.
- **Built:** no new code. PR 2 already has `aggregate` over `groupby` and `diff`
  (`chart_view/transforms.py`). `tests/facet/test_stack_diff.py` pins both on a `grid`
  spec through `spec_errors` and `query.build`:
  - three groups on one 2 × 2 lattice come back as one 4-row lattice of per-cell
    means;
  - `diff` of W3 minus W1 comes back cell by cell.

  Swapping `diff`'s `of` / `minus`, or dropping `groupby`, reddens them.

**P6 — the gallery.**

- `facet:` renders a virtualized grid of thumbnails. Only visible rows mount, and pages
  are fetched as they approach the viewport.
- The page size derives from record size: target about 1–2 MB per page, so tens of
  groups at 50k cells.
- Sorting uses the index already in hand, with no rebuild.
- Enlarging fetches the one group's exact values.
- **Built, sandbox side:**
  - `facet:` in the one spec schema:
    `facet: {field: <col> | [<cols>], sort?: {field, order?}, cache_mb?}`. It needs
    `mark: grid`, and the refusal says so. The corpus has `ok-facet-gallery` and
    two `bad-facet-*` files. The web reader agrees on all three (its corpus test,
    run locally, passes).
  - `facet_build {"spec"}`:
    - builds once per key, then reuses the cache and marks it recently used. The key
      is the normalised source path, its size and mtime, plus only what shapes the
      bytes:
      - the facet columns and the sort field;
      - x / y / color field and type;
      - the transform.

      The sort order, titles, colour schemes and `cache_mb` are left out, so sorting
      the other way costs no rebuild.
    - colours as the chart does: `query._kinds` decides ramp (`q8`) or categories,
      so a `type: nominal` number column is categories on the thumbnail too;
    - refuses an encoding `aggregate` and points to a `transform:` aggregate whose
      `groupby` includes the facet columns;
    - bounds the dir by `cache_mb` (default 500) on every build. The dir is shared
      by every gallery in the sandbox, so one spec's cap bounds them all;
    - answers `{key, build, groups, cells, built}`;
    - refuses an entity source, since there is no file version to key a cache on.
  - The progress lines go to stderr. `useSandboxRun` hands them back with the answer
    rather than streaming them, so "first open shows progress" still needs a
    streaming path, or a build long enough to warrant polling.
- **Built, web side:**
  - `gallery.ts` (pure): `sortedPositions`, `groupsPerPage`, `thumbnail`,
    `rangeMarking`, `groupsLit`.
  - `FacetGallery.tsx`, which `ChartView` renders for a `facet:` spec; `query` is
    disabled for it.
    - It runs `facet_build` then `facet_index`.
    - Only the pages within a screen of the viewport mount (one screen of lookahead
      each way), each asking `facet_page` for its run of sorted positions.
    - The sort order toggles over the index in hand.
    - A thumbnail is `thumbnail()`, the full grid's own calls, and is pinned
      pixel-for-pixel by the Q13 parity test for both colour schemes. Each tile
      paints once per (column, lit), not on every scroll.
    - Enlarge opens a dialog that fetches `facet_exact` and shows the value under
      the pointer. The pointer is mapped to a cache cell through `gallery.cellAt`,
      which is the lattice's own placement (sorted axes, filled gaps, nulls
      dropped).
    - Recovery is an epoch carried in every facet call's arguments. The facet
      commands accept an optional integer `epoch` and ignore it. The args are the
      query key, so a new epoch asks the whole chain (build, index, pages, exact)
      again as new queries.
      - Waiting on a cached answer's identity would not work: `useSandboxRun`'s
        `refetch` returns nothing to await, and TanStack's structural sharing keeps
        the same data object for the same JSON.
      - A failure (exit 3 or 4) at epoch `at` asks for `max(epoch, at + 1)`. Pages
        failing together move it once, a late failure from an old epoch moves
        nothing, and a re-render moves nothing.
      - After 2 recoveries the gallery stops and shows why.
    - With a multi-column facet, the marking holds each column's values separately
      (`column → set`, #856), so selecting (L1, 1) and (L2, 2) also lights (L1, 2)
      and (L2, 1). That is the platform's matching rule, not the gallery's.

**P7 — selection over positions.**

- Box selection and range selection (e.g. ranks 1–30) are over **sorted positions**, not
  mounted elements. They resolve to group keys from the index and write the full set to
  the marking.
- A test selects across unloaded pages and asserts the complete set.
- **Built:** click selects one group, and shift-click selects the run of sorted
  positions between it and the last click. "from rank / to rank" selects ranks a–b.
  - Each writes `rangeMarking` of every group in the run, loaded or not, to the
    view's marking, with the view file as source.
  - "Clear selection" writes `{}`.
  - Groups the marking holds are lit by the platform's `isLit`, and the header
    counts them ("N of M marked").
  - The test selects ranks 1–300 of 1000 with only the first page loaded, and
    asserts all 300 keys.
  - A rubber-band box over the grid is not built; the rank range covers the same
    need over sorted positions.

**P8 — docs and runbook.**

- The facet, stack and diff reference in the chart docs.
- A `docs/migrations.md` entry. It is needed because the scratch volume now holds view
  caches, bounded per sandbox by the LRU cap and reaped with the sandbox. The entry
  covers:
  - scratch sizing. A continuous cache is about 9 bytes per cell (1 quantized + 8
    exact), so 200 × 50 000 cells is about 90 MB, against a 500 MB default cap;
  - build cost, measured end to end through the bundle's `facet_build` on the dev
    box (32 cores), not a CI number:
    - 1000 × 5041 cells as CSV: 3.6 s at a 0.95 GB peak RSS;
    - the same as parquet: 3.0 s at 1.27 GB;
    - 200 × 50 176 as parquet: 5.4 s at 2.29 GB.

    A reopen reuses the cache in about 0.3–0.4 s at 0.12 GB. `facet_index`,
    `facet_page` and `facet_exact` each take about 0.05 s at 22 MB. The peak grows with
    rows (about 0.2–0.25 GB per million) and sits inside the sandbox's own cgroup
    limit, so a large source needs a sandbox sized for it. The build is one sandbox
    command, so it also runs under the per-command time cap (60 s by default). These
    figures are after P9's column path; the row path's are in P9;
  - the knob;
  - the check that confirms it: open a gallery and see `.home/.cache/views/` in the
    sandbox dir.
- **Built:**
  - `SKILL.md` gains a Facet section and a "many groups that share a shape" line.
    Its example is test-checked like the others; the body grows from 5945 (#856's) to 6968
    characters, measured.
  - `docs/view-plugin-chart.md` gains a 縮圖牆 section on how the gallery works,
    linking to SKILL.md for the syntax, as that page's no-third-copy rule asks.
  - `docs/migrations.md` #pr-857 covers:
    - scratch sizing and the shared cap;
    - the measured build cost and the sandbox memory it needs;
    - the entity refusal;
    - sandbox-host shipping with the API, and what an API-only rollout looks like;
    - the checks.

**P9 — what the live check found.**

The Verification live check, run after P8, found four defects that happy-dom could
not show. Each UI fix started with a test that reddened on the unfixed code and was
mutation-probed. The builder's fix is a second implementation, so it is pinned the
other way: the parity test holds it to the row path, every guard in it is
mutation-probed, and its speed is the live measurement in P8.

- The builder read the frame a row at a time: 29.9 s, 28.8 s and 68.7 s on the
  three fixture shapes (measured on fixtures of the same rows and cells, generated
  before the ones P8's table used). The third was killed at the 60 s per-command cap
  on every open, so that gallery could never be built.
- Tile layout: the next row's thumbnails covered each tile's label and ⤢.
- Virtualization: the scroller grew to its content inside the host's height:auto
  pane, so every tile mounted and every page was asked.
- The enlarged view did not close on Escape, and it covered the toolbar.
- **Built:**
  - `_by_arrays` does the builder's work on whole columns. The row path
    (`_by_rows`) stays for dtypes the column path cannot vouch for (such as
    mixed-type object, categorical, timedelta, nullable sort columns), and is the
    oracle of `tests/facet/test_build_parity.py`: same written bytes bar the build
    id, same progress, same refusal, on every dtype the column path takes.
  - Rows of tiles are a thumbnail, a fixed label row and a gap apart.
  - The scroller is bounded to 80vh, with no minimum (a one-row gallery drew a
    240 px box with one).
  - The enlarged view takes focus without scrolling the host, closes on Escape
    without the Escape reaching the host's document-level modal listener, and
    gives focus back to the ⤢ that opened it.
  - Review round 1 found the column path placing a cat-axis level with no text (an
    inf x on an ordinal axis) as a cell, and keeping two codes with the same level
    text apart; cells are now the levels as dict keys, as `_axis` reads them. It
    also found a time sort value identified by nanoseconds, where the definition
    compares milliseconds; a parity case near 2251 pins it.

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
- **Done** (Chromium, `kind: local`, the app on the branch; fixtures 1000 × 5041 as CSV
  and as parquet, 200 × 50 176 as parquet):
  - First open shows "Building the gallery…" then "N groups", from cold in 6.7 s,
    4.4 s and 12.8 s.
  - Scrolling asks further pages (`facet_page` 1 → 5 on the 1000-group gallery).
  - Flipping the sort reorders with no new `facet_build` or `facet_index`, and no
    "Building…".
  - Ranks 1–30 in a gallery beside the summary scatter, both on `marking: wafers`:
    the gallery reads "30 of 1000 marked" (sorted by fail_rate descending), and the
    scatter keeps the points at the top of its fail_rate axis solid and dims the rest,
    as seen in a screenshot of each end (the points were not counted).
  - Removing `.home/.cache/views` (what a reap does to it) and reopening rebuilds. Removing
    it while the gallery is open: the next page answers exit 3, the gallery rebuilds, and
    every tile paints.
  - Base differential: `spec_errors` on the same spec with PR 3's
    `spec.schema.json` (from `2a4b897f`) swapped in refuses it with
    `'facet' was unexpected`; with this PR's it is accepted. That is the schema
    `validate` reads, not a run of PR 3's app.
  - Found and fixed on the way, each with a test that reddened on the unfixed code and
    mutation-probed:
    - the next row's thumbnails covered each tile's label and ⤢ (0 of 44 reachable at
      1400 px, 0 of 8 at 390 px; 44 and 8 after);
    - the gallery's scroller grew to its content inside the host's height:auto pane,
      so every tile mounted and every page was asked at once (bounded to 80vh: one page,
      200 tiles);
    - the 200 × 50 176 build was killed at the 60 s cap on every open (see P9);
    - the enlarged view did not close on Escape, and covered the toolbar.
  - Seen, not in this PR: under `kind: local`'s jail, two commands in one sandbox at once
    can fail with `mount: …/dev/zero: mount point does not exist` (one exec removes the
    `/dev` bind targets another is mounting). A retry opened normally.
