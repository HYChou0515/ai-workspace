# data-fetch

An **example analysis tool** for the RCA sandbox's tool-provisioning mechanism:
it materialises a **named** dataset into the workspace as CSV.

## What it does

The agent never supplies a URL or a schema — it picks a `name` from a fixed
catalog. Each name maps to a bundled **scikit-learn** dataset that the tool
**augments** (bootstrap-resample + per-column jitter + synthetic
id/categorical/datetime/label columns) into a large, mixed-dtype table —
**25k rows × 20+ columns by default** — disguised as a domain dataset.

- **Fully offline** — generates from bundled sklearn data; no network, no
  LLM-supplied URL (the model can only pick a name from the catalog, exposed to
  it as an enum).
- Carries its **own heavy deps (scikit-learn / pandas / numpy)** in its **own
  repo**, installed into the sandbox at provision time — the host app never
  inherits them.

Catalog (`--list`): `sensor-telemetry` (breast_cancer→36 cols), `alloy-batches`
(wine→24), `process-readings` (diabetes→24), `panel-inspection` (digits→26).

## Run it standalone (uv)

```bash
uv sync
uv run data-fetch --list
uv run data-fetch sensor-telemetry                       # → sensor-telemetry.csv (25000 × 36)
uv run data-fetch alloy-batches --rows 50000 --out /data/alloy.csv
uv run data-fetch sensor-telemetry --json
uv run pytest                                            # 9 tests
```

## How the sandbox provisions it (the pattern)

```yaml
name: data-fetch
setup:                       # run INSIDE the sandbox at startup (if allowed):
  - git clone --depth 1 https://example.com/data-fetch /opt/tools/data-fetch
  - uv sync --project /opt/tools/data-fetch
invoke:                      # what the agent's tool call executes (args appended):
  - uv
  - run
  - --project
  - /opt/tools/data-fetch
  - data-fetch
```

The agent sees a clean `data-fetch(name: enum, rows?: int, out?: str)` tool;
under the hood it's `exec` of `invoke` in the sandbox, and the CSV lands in the
workspace (the sandbox cwd). Pair it with **csv-column-summary** for a two-step
flow: materialise a named dataset, then summarise its columns.
