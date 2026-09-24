# Plan — PR 2: the chart plugin

Part of [plan-view-plugins.md](plan-view-plugins.md). The decisions are Q2, Q3, Q4, Q7,
Q9, Q18 and Q20. This PR stacks on PR 1 and ships `view-plugins/chart/`, the reference
plugin with all four parts.

## Done means

- The AI writes a `view: chart` spec, calls `show_file`, and the card opens a **live**
  chart:
  - all eleven marks, three axis types, `aggregate` / `filter` / diff transforms,
    tooltip, box brush, lasso and legend click;
  - brushing is local to the view until PR 3.
- `highlight:` in the spec is already drawn on open.
- `show_file` refuses a broken spec and summarises a good one.
- The source is CSV, parquet, or entity records.

## Phases

**P1 — the chart plugin's sandbox bundle.**

- Add a prebuilt bundle with its own python, pandas and pyarrow (the carrier has no
  pyarrow), launched through PR 1's **isolated** template. A user-site pandas therefore
  cannot reach it (master plan check 2, verified by PR 1's test).
- Shipped as a first-party `{bundle: …}` plugin:
  - baked into sandbox-host `builtin/` next to `sample-tools/`, which touches
    `sandbox-host/Dockerfile`;
  - copied into the local merged root for `kind: local`.
- The PR body's carry-over list names `sandbox-host/`, because a merge that misses it
  leaves prod with a chart view whose every call fails, and the error names the plugin.

**P2 — one spec schema, two readers.**

- A JSON Schema for the Vega-Lite subset plus our keys (`source`, `keys`, `marking`,
  `highlight`) lives once, in the plugin.
  - The Python `validate` reads that file.
  - The TypeScript renderer reads the same file.
- A **parity test** runs one corpus of valid and invalid specs through both and requires
  the same verdict. It is never two schemas kept alike by hand.
- Unknown keys and unsupported marks (`geoshape`, `image`, `trail`, `tick`,
  `errorband`) are rejected.

**P3 — sandbox readers.**

- `source:` can be CSV, parquet, or `entity: <type>`.
- Entity records come through `read_entity_records(type)` in a small platform-owned
  Python SDK, vendored into plugin bundles at build time.
- A parity test uses `EntityStore.query` (`entity/store.py`) as the oracle on the same
  fixture records.

**P4 — sandbox transforms and output.**

- Transforms: `filter`, `aggregate` (`mean` / `sum` / `count` / `min` / `max` / `rate`
  over `groupby`), and diff (the same aggregate over two groups, subtracted).
- Scatter above the spec's `bin_threshold` (default 10 000 points) is binned in the
  sandbox and flagged as binned in the output.
- Output is compact. Arrays go column-wise, and per-cell categories and quantized values
  are binary and base64-encoded.

**P5 — `validate`.**

- Runs the schema check.
- Checks that the source exists and the named columns exist.
- Evaluates `highlight:`. A `where:` predicate goes through pandas `query`. The spec is
  rejected if it matches zero rows or all rows, with the count in the message.
- Prints the one-line summary PR 1's `show_file` hook appends.

**P6 — the renderer.**

- Translates the spec into an ECharts option, tree-shaken, with only the components used.
- Covers:
  - eleven marks;
  - quantitative, categorical and temporal axes;
  - tooltip;
  - the legend;
  - `brush` with `rect` and `polygon`.
- The `grid` mark uses the shared raster core (Q13): `(cells, scale) → ImageData`,
  embedded as a `graphic` image, with lasso hit-testing on cells done by us.
- Data comes through `useSandboxRun("chart", "query", …)`.
- Loading, error, and "binned" states are visible.

**P7 — highlight.**

- The resolved highlight set comes back from `query`.
- Matching marks are emphasized and the rest dimmed, using ECharts' own
  `emphasis`/`blur` states rather than a second colour path.

**P8 — the AI side.**

- `plugin.json` `views`:
  - `` `chart`: a relationship, trend, distribution or comparison you are claiming ``
  - `` `chart` with `mark: grid`: a value's pattern over a 2-D lattice ``
- `skill/SKILL.md` is **one file**, with the spec reference inline, so an operator's
  edit reaches the next turn with no Refresh (master plan check 5). It covers how to
  write a spec (source, keys, highlight) and when a claim
  needs one. It is generic, with no domain knowledge (Q6).
- `view-plugins/chart/scenarios/` ships unscored for operators (Q19, user). It covers:
  - claims that should call `write_file` + `show_file`;
  - claims that need `mark: grid`;
  - requests that must not show anything.

  `view_plugin tune chart` runs it (PR 1 P11).

**P9 — docs and runbook.**

- The chart spec reference.
- A `docs/migrations.md` entry. It is needed because the default image now ships a
  plugin that adds a skill and an index line to every eligible app's prompt, which
  changes cost and behaviour. The entry covers:
  - which prompts grow;
  - how to disable it (remove the plugin from the dir);
  - how to retune its skill for their model (`view_plugin tune chart`);
  - the check that confirms it is live (`GET /api/view-plugins` lists `chart`).

## Verification

- Targeted tests, the four gates, and `pnpm run typecheck`.
- Live check with a real model or the demo-without-a-model harness:
  - put a CSV and a parquet fixture in a workspace;
  - have the agent write and `show_file` a scatter with `highlight`, a `grid` with
    `aggregate`, and a spec with a typo'd column;
  - the first two open live, and the third shows no card and the reply names the
    column.
- Base differential on PR 1's tip, where `view: chart` is "Unsupported view kind".
