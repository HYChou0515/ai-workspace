# plan-sci-plot.md — Scientific plotting tool (#285)

A dedicated, extensible **scientific-plotting catalog** the agent can call to
turn messy tabular data into domain charts, plus a **VLM visual self-correction
loop** that auto-fixes presentation issues before returning.

Grilled + locked via `/grill-me`. Phases are a **flat integer sequence** (no
`1a`/`1b`).

## Locked decisions

1. **Form** — a new **sample-tool package `sci-plot`** (sandbox-side render
   engine), agent-invoked. Renderers are pure functions so a workflow / report
   generator can reuse them later. Mirrors `csv-column-summary`: own
   `pyproject.toml` + venv (matplotlib/seaborn/pandas/numpy), 3-stage CLI
   dispatcher, absolute imports only (ruff `TID252`).
2. **Command granularity** — a **single `plot` command + internal renderer
   registry**. Each chart is an `IChart` ABC subclass with its own `Options`
   model; the `plot` command's `params_json_schema` is a **discriminated union
   on `chart`** auto-assembled from the registry. Adding a chart = add one
   renderer + register it.
3. **Input** — `data` accepts a **workspace file path** (csv/tsv/excel/json/
   parquet) **or inline JSON** (records `[{...}]` or columns `{col:[...]}`). One
   shared normalizer → `pandas.DataFrame`.
4. **Coercion / roles** — types & shape are **liberally coerced** (numeric-ish
   strings → numbers, date-ish strings → datetime, wide→long where needed, drop
   all-NaN). **Column roles are optional**: explicit wins; omitted +
   unambiguous → inferred; **omitted + ambiguous → return a structured
   "needs you to specify X; available columns = …" (exit 0, guidance not
   crash)** so the agent re-calls. Roles map columns → semantic slots; the loop
   **never** changes them.
5. **Extension contract — thick framework / thin renderer.** The framework runs
   one *parameterized* pipeline for every chart: read (file|inline) → coerce per
   declared role kinds → resolve roles (explicit/infer/ask) → apply house style
   → `chart.draw(df, roles, options)` → `savefig` → return path. A renderer only
   declares `roles` (which columns it needs + their kinds) + its own `Options` +
   `draw()`. `draw()` keeps **full post-processing freedom** (build die grid,
   compute cumulative %, `|`-collapse labels, suppress points) and may override
   frame bits (equal aspect, hide axes, custom colorbar). The framework is
   uniform in *mechanism*, per-chart in *specification* — charts do **not** all
   take the same input.
6. **Surfacing — inline in chat, generic mechanism.** A command's structured
   output listing image paths (`{"images":[...]}`, plus compat with the existing
   `{"plots":[...]}`) is surfaced as an **image display on `ToolEnd`**; the FE
   `ToolCallCard` renders them via the existing `fileUrl()` (push paths, **no
   base64**). Any image-producing tool benefits (retro-fixes
   `csv-column-summary`'s buried PNGs).
7. **Scope** — enabled in **RCA + Playground** `allowed_tools`.
8. **VLM visual feedback — closed loop, backend-orchestrated.** Picked over a
   describe-only or pure-quality-gate role. The agent-facing `plot` becomes a
   **backend orchestrating tool** (it can reach the VLM; the sandbox cannot):
   `render(sandbox) → VLM detect → deterministic adjust → re-render`, **N = 2**
   correction passes max.
   - VLM (small local **qwen2.5vl via Ollama through LiteLLM**, streaming) does
     **detection** against a **fixed checklist** (blank, label overlap,
     truncated labels, tiny/illegible text, elements clipped out of frame,
     missing legend/colorbar) → structured booleans + free `notes`.
   - A **deterministic adjuster** maps each detected issue → a **presentation
     knob** tweak (figsize / DPI / tick rotation / font size / legend placement
     / margins / point-suppression threshold). **Never** touches semantic role
     columns. VLM `notes` are a **soft hint** to the adjuster (advisory only;
     the deterministic rule still decides the actual change).
   - Bounds: converge when no blocking issue or no improvement; on
     non-convergence return the **best** attempt + the remaining issues noted in
     the result (so the agent/user see "auto-fixed X, still Y"). No infinite
     loop, never silently "better".
   - The VLM's free critique of *content* quality may ride along in the result
     for the agent to read, but does not drive the auto-loop.
   - Model is a **pluggable external dependency**: needing multimodal capability
     ≠ needing hosted AI. Reuse the existing describer infra (`get_kb_describer`
     / read_image path).

### v1 catalog (4 domain charts — semiconductor wafer/die)

- **box_scatter** — one color + one x-region per group, y = numeric value, box +
  scatter overlaid. **>1000 points/group → draw only outliers** (else rendering
  explodes); ≤1000 → all points. Threshold is an `Options` knob.
- **grouped_line** — y = numeric, x = a multi-level hierarchical key (item_id →
  item_type → item_cat …, unbounded levels). `line_level` selects which level is
  the **line boundary** (each group at that level = one line). Tick labels use
  the **`|`-collapse algorithm**: for any level, a label value that spans
  multiple consecutive x positions collapses to a single `|value|` bracket
  centered over its span (adjacent dedup); a value unique to its position is
  shown per-tick without `|`. Decided dynamically from the data, not by level
  depth.
- **wafermap** — die grid inside a wafer circle, die colored by value.
  `color_mode`: `uni` (sequential, value ≥ 0, e.g. defect count) | `bi`
  (diverging, e.g. measurement). `Options`: vmin/vmax/center, colormap, wafer
  diameter, die size, notch, partial-die (die layer above wafer; boxes poking
  past the wafer edge are acceptable). Exact geometry/coloring calibrated with
  the user during impl.
- **defectmap** — wafer + die base (shared with wafermap) + each defect plotted
  at its coordinate as a **small red square** (marker/size/color via `Options`).

Generic `bar` / `histogram` are deferred freebies (trivial once the framework
exists), not v1 scope.

### Output / file semantics

- Default output written under `/charts/<chart>_<timestamp>.png`. **Timestamp,
  not a hash**; duplicate-collision handling deferred ("有重複再說").
- Caller may pass an explicit `output` path. One figure per call. PNG default
  (raster is required for the VLM to inspect); SVG optional knob (skips the loop).

## Testing strategy

- **sci-plot package** has its own `tests/` (own pytest, NOT in the backend 100%
  gate — `source = ["src/workspace_app"]`; ruff/ty exclude `sample-tools/**`).
  TDD the **pure logic** hard (normalizer/coercion, role resolve + ask,
  `|`-collapse, point-suppression decision, adjuster rules) — deterministic.
  Renderers get **structural assertions** (expected #axes/artists, colorbar
  present, labels set, aspect, file written) + a render-without-error smoke. **No
  pixel diffing.**
- **Backend** code added to `src/workspace_app` (package registration, generic
  surfacing plumbing, VLM orchestrator) runs under the **100% coverage gate**
  with fake VLM / fake render. **FE** uses vitest.
- **DoD**: a **live qwen canned check** for the VLM loop (fake-LLM tests ≠
  feature works).

## Phases (flat)

- **Phase 1** — `sci-plot` package + thick framework + `box_scatter`. Package
  self-tests green.
- **Phase 2** — register in `tooling/packages.py`, prebuild, add to RCA +
  Playground `allowed_tools`; backend wiring tests (100% gate).
- **Phase 3** — generic inline image surfacing (ToolEnd image display + tooling
  detect + FE ToolCallCard). box_scatter shows inline; retro-fix
  csv-column-summary. Backend 100% + vitest.
- **Phase 4** — `grouped_line` (`|`-collapse pure function + `line_level`).
- **Phase 5** — `wafermap` (uni/bi color, partial-die geometry Options).
- **Phase 6** — `defectmap` (defect coords → red squares; shares wafer base).
- **Phase 7** — VLM review closed loop (backend orchestrator, N=2, soft hints,
  return-best + remaining; live qwen check).
- **Phase 8** — `docs/sci-plot.md` manual + full suite/100% gate/ty/ruff/vitest/
  build + live check; commit → PR → CI green + no conflict → merge.
