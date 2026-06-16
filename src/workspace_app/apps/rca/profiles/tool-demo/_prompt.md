## Your workspace — `tool-demo` profile (provisioned tool packages)

This investigation has two **provisioned tool packages** installed into the
sandbox. Each lives in its own repo with its own dependencies — you invoke
their commands like any other tool; their output files land in the workspace.

| Command | Package | What it does | Args |
|---|---|---|---|
| `data-fetch` | `data-fetch` (single-command) | Materialise a **named** dataset into the workspace as a CSV. You pick a name from a fixed catalog — you **cannot** pass a URL. | `name` (one of `sensor-telemetry`, `alloy-batches`, `process-readings`, `panel-inspection`), optional `rows`, `out` |
| `summarise` | `csv-column-summary` (multi-command — shares a venv with `plot`) | Summarise every column of a CSV: dtype, count, nulls, uniques, numeric stats / top categorical values. Returns JSON. | `csv` (path) |
| `plot` | `csv-column-summary` | Write per-column distribution + numeric correlation PNGs next to the CSV (`<name>.distributions.png` + `<name>.correlations.png`). | `csv` (path) |

### Verify the tools end-to-end

1. Call `data-fetch` with `name="alloy-batches"` and `rows=500` (small, fast) —
   it writes `alloy-batches.csv` into the workspace.
2. Call `ls` to confirm the file is there.
3. Call `summarise` with `csv="alloy-batches.csv"`.
4. Call `plot` with `csv="alloy-batches.csv"` — it writes the two PNGs.
5. Report the per-column summary, and tell the user the PNG files it wrote.

Do this now, then stop and show the result — this run is a smoke test of the
provisioned-tool-package path, not a full investigation.
