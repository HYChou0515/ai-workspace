# Blob GC becomes a job — the API only asks for it

## The report

An API pod's last log line before it died was `blob-gc: won lease`. The next line
would have been `blob-gc: reconcile complete …`; it never came.

## Why the API dies there

`blob_gc_sweeper` (`api/lifecycle.py`) ran `spec.gc(mode="reconcile")` **on the
API pod** that won the CAS lease. specstar's `_gc_reconcile` builds the live set
by calling `collect_all_referenced_file_ids()` on every registered model; for a
model whose type can hold a `Binary` that is

```
metas_list = list(self.storage.dump_meta(None))          # EVERY ResourceMeta of the model, at once
bulk = self.storage.dump_resources_bulk(resource_ids=…)  # None on disk/Postgres (S3 only) ⇒
for meta in metas_list:                                   #   revisions streamed one resource at a time
    for _info, data_io in self.storage.dump_resource(meta.resource_id): …
```

`ResourceMeta` carries `indexed_data`, so a `DocChunk` / `ClusterMember` table
whose rows were never rewritten lean holds every embedding vector in that list
at once; `WorkspaceFile` is one meta per file of every workspace (a draft
rewrite — one revision each). That is the same class as the #804
`cluster_sweeper` OOM — a whole-table read of something that grows with
content, on an API pod's timer. (The first draft of this plan said "every
revision in memory at once"; the veracity pass showed the bulk path is
S3-only.)

The decision (2026-09-17): **put it in a coordinator, like every other heavy
sweep.** The API becomes a pure producer; a worker runs the reconcile.

## Why #804 P4 did not do this — and what "which models" really means

#804 withdrew the move because "the worker doesn't register all ~30 blob-bearing
models, so reconcile would treat their blobs as orphans and delete them after
`t2`". The first draft of this plan narrowed that to *four* models (the ones
with a declared `Binary` field) on the strength of reading
`collect_all_referenced_file_ids`, which returns `(set(), True)` when a model has
no collector. The parity test (P3) reddened naming **nine** collector-bearing
models the worker lacked, so the predicate was probed directly against
specstar's `BinaryProcessor`:

| field type | gets a runtime collector |
|---|---|
| `list[str]` | yes |
| `dict[str, str]` / `dict[str, Any]` | yes |
| `str \| int` | yes |
| `str \| None` | yes |
| all-scalar struct (`str`, `int`) | **no** |

So the scanned set is **nearly every model**, registered all over `create_app`
(`workspace-file` by the filestore, `-sandboxactivity` by the sandbox layer,
`conversation-todos` by a route module, …) — #804 P4's objection in its real
form, and not a list a second composition root could keep in step by hand. The
registered-name comparison the shipped test makes lists **eleven** models the
plain worker bundle lacks.

## Phases

- **P1 — `BlobGcCoordinator`** (`filestore/blob_gc.py`, replacing the lease +
  `run_blob_gc`): a `BlobGcJob` JobType (`kind="reconcile"`, fixed
  `partition_key` so two reconciles never overlap, `max_retries=0` — the next
  window asks again). `enqueue_reconcile()` coalesces onto an active job.
  `_handle` runs `spec.gc`, emits the `blob_gc` telemetry event and the
  `ws_census` snapshot (both moved here from the API sweeper — census is a
  `WorkspaceFile` aggregate), and prunes finished rows BEFORE the pass so a
  timer that asks forever leaves a bounded number whatever the pass does.
  Consumption machinery copied from the graph coordinator. `_GcLease` /
  `try_claim_gc` / `register_gc_lease` / `run_blob_gc` are deleted with their
  tests: the fleet-wide "one asker per window" is the same `ScanLease` the
  other sweeps take.
- **P2 — bundle + API producer.** `CoordinatorBundle.blob_gc`, built always
  (no LLM). `blob_gc_sweeper` becomes: every `gc_interval`, claim the
  `ScanLease("blob-gc")`, `enqueue_reconcile()`. `run_consumers` starts and
  drains it like the others, so the all-in-one deploy behaves as before.
- **P3 — worker.** `blob-gc` JobType. **Deviation from the first draft** (which
  said "add the filestore to `build_bundle`" — that would have covered one of
  eleven): the worker consumes from the API's **own composition** —
  `workspace_app.__main__.build_app(settings, config_dir)` is extracted from
  `main()` (everything up to `create_app`, never served) and
  `worker.build_coordinator` uses it for the JobTypes in
  `API_REGISTRY_JOBTYPES`, so the registries are equal by construction (the
  Django management-command / Celery-worker shape). And a **runtime guard**:
  every ask carries `payload.registry` = the asker's registered model names; a
  runner that lacks any of them (or gets a claimless hand-made row) refuses the
  pass — `RegistryMismatch`, the job reads FAILED, the log names what is
  missing. A partial-registry consumer is a visible GC outage, never a silent
  loss of blobs. **Second deviation (review round 1, all four lenses):**
  "equal by construction" was false — four coordination models
  (`_SandboxAddress`, `_ScheduleIndex`, `_TriggerWindow`, `_GoalStretch`) were
  registered only inside the **lifespan**, which the worker never enters, so
  in a pod-split deploy the worker refused every pass. The first parity test
  was green because its oracle never entered the lifespan either. Now: the
  lifespan registers **no** model (those four, and the four it duplicated as
  belt-and-suspenders, live in `create_app` post-apply, unconditionally — a
  registry that depends on which features are on is one more way for asker
  and runner to diverge), and the parity test enters the API's lifespan, lets
  its sweeper produce the real `BlobGcJob`, asserts the lifespan added nothing,
  and feeds that row's `registry` to the worker's `_check_registry`. The
  runner also refuses a row off the one partition (the auto-CRUD route can
  create one under any key), and telemetry after the pass can no longer fail
  a pass whose deletes are done.
- **P4 — deploy + docs.** `workers.yaml` gains `rca-worker-blob-gc` (one
  replica, no HPA — one reconcile per window is the whole load; memory sized
  for "every revision of every blob-capable model at once", and a shorter
  window does NOT reduce that); `docs/deployment.md` §11, `kubernetes/README.md`,
  `migrations.md` §5.5, and the CLAUDE.md sweeper convention stop saying blob
  GC must stay on the API (and stop saying "~30 blob-bearing models").

## 判準

- With `run_consumers=False`, `SpecStar.gc` is never called in the API process;
  a `BlobGcJob` row is what the tick produces, behind the `__scan__:blob-gc`
  window lease.
- The blob-gc worker's spec registers every model the API's ask names — the
  ask produced by an API whose lifespan RAN — entered through
  `worker.build_coordinator`; and the lifespan registers nothing.
- A runner missing a claimed model refuses; a claimless row is refused; a row
  off the one partition is refused; a runner holding every claimed model runs.
- A telemetry/census failure after the pass leaves the row COMPLETED.
- `python -m workspace_app.worker blob-gc` resolves to the coordinator
  (the exhaustive jobtype test) and has a Deployment (the derived manifest test).

## 執行結果

Branch `blob-gc-job`: P1–P4, two docs commits, then the review-round-1 fix
commits (count them with the log, `c7cbecde..HEAD`).

| claim | how it was checked |
|---|---|
| pure producer never runs `gc` in-process | `test_a_pure_producer_asks_for_the_reconcile_and_runs_none_of_it` spies `spec.gc`; row PENDING, 0 calls |
| one asker per window | `test_a_pod_that_loses_the_window_lease_asks_for_nothing` pre-claims a window an hour ahead |
| worker registry ⊇ the live API's ask; the lifespan adds no model | `test_the_blob_gc_worker_holds_every_model_the_apis_ask_names` (API under `TestClient`, `run_consumers=False`, off-hours on; the row's `registry` fed to the worker's `_check_registry`). Reddened on the pre-fix code naming `-goalstretch -scheduleindex -triggerwindow`. **Mutation** (first version): `API_REGISTRY_JOBTYPES = frozenset()` reddens it naming 11 models (`-eventwatermark -sandboxactivity -turnactivity -userquota -workspacedirs -workspacedisk conversation-goal conversation-todos turn-epoch work-calendar workspace-file`; 9 of them bear a collector) |
| refusal on a partial registry / claimless / off-partition | `test_a_runner_missing_a_claimed_model_refuses_the_pass` (FAILED, `gc` 0 calls, log names `workspace-file`) + claimless + off-partition + holding-all |
| telemetry after the pass cannot fail it | `test_a_telemetry_failure_after_the_pass_does_not_fail_the_pass` (census raises → COMPLETED, `blob_gc` event still recorded) |
| reclaim still works end to end through the queue | `test_reconcile_reclaims_a_deleted_files_blob_but_keeps_referenced` (disk blob store, `now` seam; `(quarantined, deleted) == [(1, 0), (1, 1)]` — pass 2's extra quarantine is pass 1's own job-log blob, which the queue puts under an explicit key; `live == [1, 1]`) |
| the delete cascade tolerates an item with no schedule row | `test_deleting_an_item_that_never_declared_a_schedule_succeeds_when_the_index_is_registered` — a pre-existing 500 (48f09a55, 2026-09-09) the registration move exposed: the tests never registered the model |
| rows bounded, even when every pass fails | two prune tests (COMPLETED and FAILED paths) |
| every JobType has a Deployment | `tests/deploy/test_worker_manifests.py` (derived; it reddened first) |

Sentences found false on the pre-push veracity pass and corrected before push:
"only the four models with a `Binary` field" (see the table above); "every
version the mirror ever wrote" (`_put_record` is a draft `modify` — one revision
per file); "shorten the window so fewer revisions accumulate" (a pass reads every
meta there is).

Review round 1 (conformance / veracity / regression / defect, in parallel, own
worktrees). Worst finding per lens: all four found the lifespan-only
registrations (the pod-split worker refuses every pass — severity: the feature
does not work where it is deployed). Veracity also found "every revision in
memory at once" false (the bulk path is S3-only; what is held at once is every
`ResourceMeta`, `indexed_data` included). Regression: the ws_census trend now
depends on a pass running (documented); the auto-CRUD route (#723 class, new
instance) — partition check added, on-demand runs remain #723's scope. Defect:
telemetry after the deletes could fail the row; `rca-worker-blob-gc` carried
none of what `build_app` touches at boot (scratch mount added, the rest named
in the Deployment comment). Every fix here replaced or added a mechanism
(registration site, partition guard, try/except), so a second round runs.

Gates: `ruff check` / `ruff format --check` / `ty check` clean; targeted tests
(`tests/filestore/test_blob_gc.py`, `tests/api/test_blob_gc_sweeper.py`,
`tests/test_worker.py`, `tests/test_coordinators.py`, `tests/test_factories.py`,
`tests/deploy/test_worker_manifests.py`) green; `mkdocs build --strict` exit 0.

Follow-ups, not in this PR: a streaming reconcile upstream in specstar (the only
thing that makes the pass's memory not "the whole table"); whether every worker
should boot from `build_app` and `build_bundle` retire (the second composition
root has cost parity bugs before — #506 worker parity, P7 `context_chars`).
