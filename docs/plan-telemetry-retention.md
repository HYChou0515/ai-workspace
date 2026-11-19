# Plan — keep telemetry events for 14 days

> Status: grilled 2026-11-19 (D1–D3 are the user's answers). Build via `/tdd`,
> flat phases, one commit each.

## Problem

A deploy found the `telemetry-event` table at about 200 GB, with nothing
ever removing a row.

Read from `origin/master` at `336a2d6d`:

- **Every SDK span is stored whole, twice.** `MonitorProcessor`
  (`monitor/processor.py`) records the OpenAI Agents SDK's `export()` of every
  trace and span, at its start and at its end, and `SpecstarMonitor.record`
  (`monitor/specstar_impl.py`) writes each one as a specstar resource.
- **A generation span carries the whole prompt.** `RunConfig.trace_include_sensitive_data`
  defaults to `True` and nothing here turns it off, so the LiteLLM path sets
  the span's `input` to the converted message list — the entire conversation
  sent to the model — and its `output` to the reply. A function span carries
  the tool's input and output (file contents, exec output). The longer a
  conversation, the more every call stores.
- **Nothing reads those fields.** The telemetry panel (`web/src/api/monitor.ts`)
  uses a span's `type`, `name`, `model` and `usage`; `/monitor/summary` reads
  the small `mirror` / `restore` / `ws_census` events.
- **Nothing removes a row.** The module's docstring says "Append-only, so
  consider pruning for very long runs"; no code does.
- **The order key is the row count.** `SpecstarMonitor.__init__` resumes
  `seq` from `count_resources`. Once rows are removed, the count falls below
  the largest `seq` already stored, so a restarted process hands out `seq`
  values the kept rows already hold — and `recent(limit=…)` (newest by `seq`)
  then shows old events instead of new ones.

The operator's immediate relief (a `TRUNCATE` of the three
`telemetry_event_*` tables, then a restart of the processes that write
telemetry) is outside this plan; this plan stops the table growing again.

## Decisions

| # | Decision |
|---|---|
| D1 | **Keep storing the full content** (prompt, reply, tool input/output) — the user chose keeping it for a bounded time over not storing it. |
| D2 | **Keep 14 days by default**, a deploy setting; `0` keeps everything. |
| D3 | **One rule for the whole table**: every `telemetry-event` row older than the window goes, whatever its kind — including the `mirror` / `restore` / `ws_census` rows `/monitor/summary` reads (the user: only the 14-day rule matters). |
| D4 | **The setting is `observability.telemetry.keep_days`**, beside `observability.llm_log.keep_days`, which already means "days kept, 0 = all". |
| D5 | **The purge is a job, not an API timer** (CLAUDE.md, the lifecycle-sweeper convention): the API asks once per window under a `ScanLease`, and the pass runs as a third kind on the `blob-gc` queue, beside `reconcile` and `disk-ledger` — the worker that already does storage upkeep, so no new JobType, worker or manifest. |
| D6 | **Rows go with `permanently_delete`.** `delete` is a soft delete: the row and its revisions stay, so no space comes back. |
| D7 | **Bounded per pass**: batches of rows older than the cutoff (oldest first, by the indexed `created_time`), until none are left or a time budget is spent; the next window carries on. A large backlog drains over several passes instead of holding the queue. |
| D8 | **`seq` resumes from the largest stored `seq`**, not the row count, so a purge never makes a restarted process reuse an order key. |

## Phases

- **P1 — `seq` resumes past the largest stored one (D8).** A test that
  removes old rows, starts a new monitor over the same spec, records an event,
  and asserts it is the newest in `recent(limit=1)` — red on the count.
- **P2 — the setting (D2, D4).** `observability.telemetry.keep_days: int = 14`;
  documented in `docs/configuration.md`.
- **P3 — the purge pass (D6, D7).** `SpecstarMonitor.purge_older_than(cutoff, …)`
  removes rows created before the cutoff, in batches, within a time budget,
  and returns how many it removed; tests for the boundary (a row exactly at
  the cutoff stays), the budget, and that the rows are gone permanently.
- **P4 — the job and its asker (D5).** A `telemetry-purge` kind on
  `BlobGcCoordinator` that runs the pass with `now − keep_days`, and an API
  sweeper that asks once per window under a `ScanLease`, at its first tick
  (as `disk_ledger_sweeper` does) so a rollout starts draining at once. Off
  when `keep_days` is `0`.
- **P5 — docs.** A `docs/migrations.md` entry (a default that deletes data is
  a behaviour change the operator must know about: what goes, when the first
  pass runs, and that on Postgres deleted rows' space is reused but not
  returned to the OS without `VACUUM FULL` or the `TRUNCATE` above), and this
  plan's As built.
