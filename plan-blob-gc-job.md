# Blob GC becomes a job — the API only asks for it

## The report

An API pod's last log line before it died was `blob-gc: won lease`. The next line
would have been `blob-gc: reconcile complete …`; it never came.

## Why the API dies there

`blob_gc_sweeper` (`api/lifecycle.py`) runs `spec.gc(mode="reconcile")` **on the
API pod** that wins the CAS lease. specstar's `_gc_reconcile` builds the live set
by calling `collect_all_referenced_file_ids()` on every registered model; for a
model with a `Binary` field that is

```
metas_list = list(self.storage.dump_meta(None))          # every resource
bulk = self.storage.dump_resources_bulk(resource_ids=…)  # every REVISION of every resource, in memory
```

Four models carry `Binary`: `WorkspaceFile`, `SourceDoc`, `WikiPage`, `IndexCache`.
`WorkspaceFile` alone is every file of every workspace, every revision the mirror
sweep ever wrote. That is the same class as the #804 `cluster_sweeper` OOM — a
whole-table read of something that grows with content, on an API pod's timer.

The decision (2026-09-17): **put it in a coordinator, like every other heavy
sweep.** The API becomes a pure producer; a worker runs the reconcile.

## Why #804 P4 did not do this, and why that reason was wider than the fact

#804 withdrew the move because "the worker doesn't register all ~30 blob-bearing
models, so reconcile would treat their blobs as orphans and delete them after
`t2`". Checked against specstar: `collect_all_referenced_file_ids` returns
`(set(), True)` immediately for a model **without** `Binary` fields. So the
worker only needs the **four** Binary-bearing models. Three come from `make_spec`,
which the worker already calls; the one it lacks is `WorkspaceFile`, registered by
`SpecstarFileStore.__init__` — and the worker's composition root deliberately
skips the filestore. That one gap is the whole danger, and it is pinned by a
parity test (P3), not by a sentence.

## Phases

- **P1 — `BlobGcCoordinator`** (`filestore/blob_gc.py`, replacing the lease +
  `run_blob_gc`): a `BlobGcJob` JobType (`kind="reconcile"`, fixed
  `partition_key` so two reconciles never overlap, `max_retries=0` — the next
  window asks again). `enqueue_reconcile()` coalesces onto an active job.
  `_handle` runs `spec.gc`, emits the `blob_gc` telemetry event and the
  `ws_census` snapshot (both moved here from the API sweeper — census is a
  `WorkspaceFile` aggregate), and prunes finished rows so a timer that asks
  forever leaves a bounded number. Consumption machinery copied from the graph
  coordinator. `_GcLease` / `try_claim_gc` / `register_gc_lease` / `run_blob_gc`
  are deleted with their tests: the fleet-wide "one asker per window" is the
  same `ScanLease` the other sweeps take.
- **P2 — bundle + API producer.** `CoordinatorBundle.blob_gc`, built always
  (no LLM). `blob_gc_sweeper` becomes: every `gc_interval`, claim the
  `ScanLease("blob-gc")`, `enqueue_reconcile()`. `run_consumers` starts and
  drains it like the others, so the all-in-one deploy behaves as before.
- **P3 — worker.** `blob-gc` JobType; the worker's `build_bundle` builds the
  filestore through the same `get_filestore` factory the API uses, so
  `WorkspaceFile` is registered. The parity test: the set of Binary-bearing
  models in the worker's spec ⊇ the API's, entered through `build_bundle` and
  `create_app`. It reddens on the unfixed worker.
- **P4 — deploy + docs.** `workers.yaml` gains `rca-worker-blob-gc` (one
  replica, no HPA — one reconcile per window is the whole load);
  `docs/deployment.md` §11 and the CLAUDE.md sweeper convention stop saying
  blob GC must stay on the API (and stop saying "~30 blob-bearing models").

## 判準

- With `run_consumers=False`, `SpecStar.gc` is never called in the API process;
  a `BlobGcJob` row is what the tick produces.
- The worker's spec registers every model whose `_binary_processor` has a
  collector that the API's spec registers.
- `python -m workspace_app.worker blob-gc` resolves to the coordinator
  (the exhaustive jobtype test).
