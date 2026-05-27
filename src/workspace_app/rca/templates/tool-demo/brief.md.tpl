# $title

Owner: $owner · Severity: $severity · Status: $status · Product: $product

## Goal

Smoke-test the **provisioned analysis tools** wired into this sandbox:

1. `data-fetch` — pull a named sample dataset into the workspace as CSV.
2. `csv-column-summary` — summarise that CSV's columns.

The agent should fetch `alloy-batches` (a few hundred rows is plenty), then
summarise it and report the per-column breakdown. Success = both tools ran in
the sandbox and the summary came back.

> $description
