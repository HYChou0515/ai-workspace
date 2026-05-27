# data-fetch

An **example analysis tool** for the RCA sandbox's tool-provisioning mechanism:
it downloads a **named** dataset into the workspace (streaming).

## Why a name, not a URL

The agent **never supplies a URL** — it picks a `name` from a configured
catalog (`name → url`). The URLs live in config, not in the model's output, so a
wrong / hallucinated URL is impossible; the worst the model can do is name a
dataset that doesn't exist, which fails cleanly. When this is wired as a sandbox
tool, the `name` parameter is exposed to the agent as an **enum** of the catalog
keys.

Configure the catalog per deployment via `DATA_FETCH_CATALOG` — inline JSON or a
path to a JSON file:

```bash
export DATA_FETCH_CATALOG='{"reflow-incidents":"https://intranet/data/reflow.csv"}'
```

It carries its **own dependency (`httpx`)** in its **own repo**, installed into
the sandbox at provision time — the host app never gains an HTTP client.

## Run it standalone (uv)

```bash
uv sync
uv run data-fetch --list                     # available dataset names
uv run data-fetch reflow-incidents           # download into the cwd (= workspace)
uv run data-fetch reflow-incidents --json
uv run pytest                                 # tests (offline, mocked transport)
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
env:                         # the catalog is config, injected into the sandbox:
  DATA_FETCH_CATALOG: '{"reflow-incidents":"https://intranet/data/reflow.csv"}'
```

The agent sees a clean `data-fetch(name: enum, out?: str)` tool; under the hood
it's `exec` of the `invoke` command in the sandbox, and the file lands in the
workspace (the sandbox cwd). Pair it with `csv-column-summary` for a two-step
agent flow: fetch a named dataset, then summarise its columns.
