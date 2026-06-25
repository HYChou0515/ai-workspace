# Plan — Issue #227: fan-out large index jobs (RabbitMQ 406 consumer-timeout)

## Problem

`pika.exceptions.ChannelClosedByBroker (406) — delivery acknowledgement on
channel 1 timed out`. RabbitMQ's broker-side **consumer ack timeout** (default
1,800,000 ms = 30 min) closes the channel when a delivered message is not acked
in time. specstar's RabbitMQ consumer acks only **after the whole job handler
finishes**, so any job that runs longer than 30 min trips it — and the unacked
message is then requeued and re-run from scratch forever (and may double-run).

The proven trigger: a **100-page PDF** whose per-page VLM `describe` (~90 s/page)
crosses 30 min at ~page 20. This is **independent of the AMQP heartbeat**
(`amqp_heartbeat_seconds`), which only keeps the TCP connection alive.

## Fix shape (locked via /grill-me)

Don't raise the timeout (fragile; just moves the ceiling). **Make every job
short** by fanning a big job out into many small jobs, each well under 30 min,
sharing one job body and dispatching by a `kind` discriminator in the handler.

Two stages can be the long pole, and they differ by source:
- **parse-bound** (PDF VLM-describe per page) and
- **embed-bound** (100k-row CSV / giant JSON → thousands of chunks).

So each small job is **end-to-end over its own unit range**: it
parses + chunks + embeds *its* units and writes *its own* `DocChunk`s. That
distributes whichever stage is slow.

### Generic parser-unit seam (P1–P3)

`IParser` gains:
- `count_units(source, *, filename, mime) -> int` — **cheap** count of
  independently-parseable units (no VLM / embed). Default `1` (whole file = one
  unit ⇒ never fanned out). The split job uses it to decide fan-out.
- `parse(..., unit_range: tuple[int, int] | None = None)` — parse only units in
  the half-open `[start, end)`. `None` = whole file (existing behaviour).

The unit must also be the **chunk-source granularity** so embed distributes too:
- **PDF**: unit = page (already one Document per page). `count_units` = page count.
- **CSV / Excel**: unit = row. `count_units` = row count.
- **JSON**: `.jsonl` unit = line; `.json` with an array root unit = top-level
  array element (cheap count); non-array root ⇒ `count_units` = 1 (no fan-out).
- Everything else (text / DOCX / HTML / single image / slides) keeps the default
  `count_units` = 1 → single job, unchanged.

`seq` is derived from the global unit index so independent jobs need no
cross-job ordering coordination.

### Join = A (fan-out + CAS), queue-agnostic

`partition_key` serialization is **SimpleMessageQueue-only**; the RabbitMQ
backend ignores it (specstar contract violation — file upstream). So the join
must not depend on it.

New specstar resource **`IndexRun`** `{doc_id, collection_id, total,
done: set[int], failed: set[int], finalized: bool, status}`:

1. `kind=split`: `_delete_chunks(doc_id)` once → `count_units()` = N → create
   `IndexRun(total=N)` (committed **before** any process job) → enqueue N
   `kind=process` jobs (one per unit batch). If N≤1 or unsupported ⇒ a single
   process job covering the whole file (degenerate, == old behaviour).
2. `kind=process` (batch `i`): parse+chunk+embed units in its range → write
   deterministic `DocChunk`s (id by `(doc_id, seq)`; retries overwrite) → stage
   its clean per-unit text → **idempotently CAS-add `i` to `done`**.
3. **Finalize trigger** is NOT "whoever added the last element" (that races and
   loses on crash). It is: condition `len(done ∪ failed) == total` **AND** a
   **CAS-claimed `finalized` flag** (exactly-once; re-claimable by any later
   finisher or the sweep). The claim winner enqueues a `kind=finalize` job.
4. `kind=finalize`: reassemble `SourceDoc.text` from staged per-unit text (in
   order) → flip status `ready` (or `error` if `failed` non-empty) → wiki hook.

**Failure branch**: a periodic **safety sweep** adds dead-lettered batches to
`failed` (so `done ∪ failed` can fill) and runs the same finalize gate; it also
rescues docs stuck in `indexing` with no active jobs.

### partition_key principle

Set `partition_key` **only where serialization is genuinely required**:
- Index `process` jobs ⇒ `partition_key=None` (parallel fan-out; stays parallel
  even after a future specstar fix that honors the key on RabbitMQ).
- Sanity `cell` jobs ⇒ keep `partition_key=model` (Ollama serves one model at a
  time — a real serial requirement).
- Wiki ⇒ keep `partition_key=collection_id`.
- Index `split` ⇒ keep `doc_id` as harmless future-proofing; same-doc
  coalescing is actually enforced by an "IndexRun active per doc" guard.

The CAS join is correct whether process jobs run parallel or serial.

### Sanity (P7)

`battery` no longer runs every cell inline; it fans out **one `cell` job per
cell**. No join: each cell upserts its own `SanityResult` row that the FE matrix
reads independently.

## Flat phases

- **P1** — `IParser` seam: `count_units` (default 1) + `parse(unit_range=)`.
- **P2** — PDF `count_units` (page count) + `parse(unit_range)` over pages.
- **P3** — CSV / Excel / JSON `count_units` + `parse(unit_range)`.
- **P4** — `IndexRun` resource + CAS helpers (idempotent add; finalize-claim gate).
- **P5** — `IndexJob.kind` (split/process/finalize) wiring in `IndexCoordinator`
  (delete-once, fan-out, idempotent chunks, text reassembly, ready/error, wiki).
- **P6** — safety sweep (dead-letter → `failed`, finalize gate, stuck-doc rescue).
- **P7** — Sanity `battery` → per-`cell` fan-out.
- **P8** — per-parser batch-size config + docs; full 100% gate + ruff + ty;
  commit → PR → CI green & no conflict → merge. File the specstar
  partition_key-on-RabbitMQ issue.

## Test discipline

coverage.py directly (parallel + combine + `report --fail-under=100`); CI runs
`-m "not integration" -n auto`. ruff check + format; ty check. ABC over Protocol.
Vertical TDD slices (one test → one impl).
