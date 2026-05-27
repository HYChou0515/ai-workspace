# csv-column-summary

An **example analysis tool** for the RCA sandbox's tool-provisioning mechanism.
It reads a CSV and prints a per-column summary — dtype, non-null count, nulls,
uniques, and numeric stats (min/max/mean/std) or top categorical values.

It lives in **its own repo with its own dependency (`pandas`)**. The host app
never installs that — the tool is cloned + `uv sync`'d **into the sandbox** at
provision time, so the agent gets the tool while the app's dependency tree stays
clean.

## Run it standalone (uv)

```bash
uv sync
uv run csv-column-summary sample.csv          # human-readable
uv run csv-column-summary sample.csv --json   # for the agent
uv run pytest                                 # tests
```

## How the sandbox provisions it (the pattern)

A tool definition the RCA app would carry (declarative; gated by an agent's
`allowed_tools`):

```yaml
name: csv-column-summary
# run INSIDE the sandbox when it starts (only if this tool is allowed):
setup:
  - git clone --depth 1 https://example.com/csv-column-summary /opt/tools/csv-column-summary
  - uv sync --project /opt/tools/csv-column-summary
# what the agent's tool call actually executes (args appended):
invoke:
  - uv
  - run
  - --project
  - /opt/tools/csv-column-summary
  - csv-column-summary
```

So the agent calls a clean `csv-column-summary(path, json=…)` tool; under the
hood it's `exec` of the `invoke` command in the sandbox. `git clone` / `cp` /
`pip install` / `uv sync` are all just shell steps in `setup`, so any tool repo
shape works — and its dependencies are confined to the sandbox.

## Example output

```
8 rows · 5 columns

• board_id  [str]  count=8 nulls=0 unique=8
    top: MX7-001×1, MX7-002×1, MX7-003×1, MX7-004×1, MX7-005×1
• line  [str]  count=8 nulls=0 unique=2
    top: Line3×5, Line1×3
• void_rate  [float64]  count=7 nulls=1 unique=7
    min=1.3 max=3.4 mean=2.18571 std=0.933503
• reflow_zone3_temp  [float64]  count=8 nulls=0 unique=8
    min=240.2 max=245.4 mean=243.537 std=2.19541
• result  [str]  count=8 nulls=0 unique=2
    top: pass×5, fail×3
```

