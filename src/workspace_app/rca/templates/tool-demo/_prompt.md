## Your workspace — `tool-demo` template (provisioned tools)

This investigation has two **provisioned analysis tools** installed into the
sandbox. Each lives in its own repo with its own dependencies — you invoke them
like any other tool; their output files land in the workspace.

| Tool | What it does | Args |
|---|---|---|
| `data-fetch` | Materialise a **named** dataset into the workspace as a CSV. You pick a name from a fixed catalog — you **cannot** pass a URL. | `name` (one of `sensor-telemetry`, `alloy-batches`, `process-readings`, `panel-inspection`), optional `rows` |
| `csv-column-summary` | Summarise every column of a CSV in the workspace: dtype, count, nulls, uniques, and numeric stats / top categorical values. | `csv` (path to the file) |

### Verify the tools end-to-end

1. Call `data-fetch` with `name="alloy-batches"` and `rows=500` (small, fast) —
   it writes `alloy-batches.csv` into the workspace.
2. Call `ls` to confirm the file is there.
3. Call `csv-column-summary` with `csv="alloy-batches.csv"`.
4. Report the per-column summary back to the user in a short table.

Do this now, then stop and show the result — this run is a smoke test of the
provisioned-tool path, not a full investigation.
