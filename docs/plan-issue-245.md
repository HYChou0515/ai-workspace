# Plan — Issue #245: per-workspace storage quota + blob GC

Per-workspace total-size quota (protect the shared disk root from a single
workspace filling it) + reclaim orphaned blobs so the quota stays honest.
Split from #219 (single-file cap already shipped there; this issue owns the
total quota + GC).

## Background facts (verified, not assumed)

- **`workspace_id` == one investigation/item.** The filestore scopes every call
  to a workspace; `_require_item(slug, item_id)` resolves it in the upload route.
- **specstar v0.11.10 ships built-in ref-count blob GC** (PR #389 / issue #370):
  `SpecStar.gc(*, mode="reconcile"|"incremental", t1="1h", t2="24h", now=None)`.
  - `reconcile` (the only deleting pass): live set = union of referenced
    `file_id`s across **all models registered on this SpecStar** → quarantines
    new orphans (t1 grace) → restores still-referenced (self-heal) → permanently
    deletes quarantined blobs past t2. Backfills bookkeeping ⇒ **no migration**.
  - `decref` is wired into `permanently_delete` automatically; we don't touch it.
  - Safety: a blob is never deleted while any revision (incl. soft-deleted) in any
    registered model references it; delete needs a *complete* scan.
- **GC must run where ALL blob-referencing models are registered.** `get_spec` →
  `make_spec` registers the KB models and `SpecstarFileStore(spec)` registers
  `WorkspaceFile` on the **same** spec → the main app process has the complete
  model set. A slim pod with a partial model set must NOT run `reconcile` (it
  would compute an incomplete live set and could delete a referenced blob).
- **Usage needs no schema change.** A stored `WorkspaceFile` keeps its
  `content` Binary's `size` inline (only the bytes are offloaded). Adding
  `"content.size"` to the model's `indexed_fields` makes it aggregatable;
  `exp_aggregate_by(by=workspace_id, {"used": Sum(QB["content.size"])},
  query=workspace_id==wid)` returns the per-workspace logical total. Verified:
  100 B + 250 B → `GroupRow(key='w1', used=350)`.
- **`Sum` is computed in Python** (only `Count` group-by pushes to SQL), so it
  works on every backend once `content.size` is in `indexed_data`.

## Locked decisions (grill)

1. **Metering = per-workspace, logical** (sum of live `WorkspaceFile.content.size`).
   Logical (not physical) because content-addressed dedup makes physical ≤ logical
   (quota is conservative), and a blob shared across workspaces can't be charged to
   one owner. GC converges physical toward logical over time.
2. **Quota config** `filestore.workspace_quota: int` = **20 GiB** default, `0`
   disables. **Default ON** (the issue's point is out-of-box protection; 20 GiB is
   generous enough that normal use never hits it).
3. **Gate at the user-facing upload/edit endpoints only.** The sandbox `mirror`
   (raw `self._fs.write_from_path`, sandbox_sync.py:93) and agent-internal writes
   are NOT gated — never lose agent work (grill choice **B**). Overwrite uses a
   **delta** (`used − old_size + new_size > quota`), so replacing a big file with a
   smaller one never falsely rejects. Streaming upload rejects **mid-stream** once
   the staged bytes would exceed the remaining quota (no full multi-GB stage just
   to reject). Concurrency: best-effort (no per-workspace lock; a small race
   overshoot self-corrects on the next write).
4. **Over-quota → HTTP 507** Insufficient Storage (distinct from the single-file
   cap's 413), body carries `{used, quota, attempted}` so the FE can explain it.
5. **FE: a usage bar** ("Used X / Y") in the upload area **and** the 507 error,
   reusing the #219 streaming-upload UI. The bar is in scope (lets users see they're
   near full *before* they complain). i18n (zh-TW + en) per the de-jargon convention.
6. **GC home = main app lifespan** `_blob_gc_sweeper`, periodic `spec.gc(mode=
   "reconcile")` off-loop (`asyncio.to_thread`), guarded by a **CAS lease** (one
   pod per sweep; matches the #227 CAS idiom) so multi-pod doesn't N× the full
   scan or race deletes. `incremental` is NOT run (reconcile both quarantines and
   deletes; v1 needs only the periodic reconcile).
7. **Backfill old rows** via the established specstar migrate path — register a
   `Schema.step(None, _reindex)` for `WorkspaceFile` so the operator-run
   `POST /WorkspaceFile/migrate/execute` re-extracts `content.size` onto pre-index
   rows. NOT a startup auto-migrate (would slow boot on a large store).
8. **GC cadence/grace config** `filestore.gc_interval` = 1h, `filestore.gc_t1` =
   1h, `filestore.gc_t2` = 24h (specstar defaults), all tunable. t1=1h is safe:
   `write_from_path` finalizes the blob and creates the referencing `WorkspaceFile`
   in the same call, so a fresh blob is referenced immediately.

## Phases (flat integers, TDD red→green per phase)

**Phase 1 — Usage measurement.** Add `"content.size"` to `WorkspaceFile`
`indexed_fields`; `SpecstarFileStore.workspace_usage(workspace_id) -> int` via
`exp_aggregate_by(Sum)`. Register the `Schema.step(None, _reindex)` for backfill.
Tests: empty=0, single, multi-file sum, scoped per-workspace, overwrite reflects
new size, delete drops usage, backfill via `rm.migrate` picks up `content.size`.

**Phase 2 — Quota config + upload gate.** `filestore.workspace_quota` in the
config schema + threaded through `create_app` (mirror `max_file_size`). New
`QuotaExceeded` in `filestore/protocol.py`. Gate in `_stream_upload_to_store`
(single) + the folder-upload loop (cumulative) with overwrite delta + mid-stream
early reject → **507**. Tests: under-quota OK, over-quota 507, overwrite delta
(smaller replacement OK), folder cumulative, mid-stream reject without full stage,
`quota=0` disables, **mirror is NOT gated** (regression for choice B), agent
cold-write not gated.

**Phase 3 — Usage endpoint.** `GET /a/{slug}/items/{item_id}/files/usage` →
typed pydantic `{used: int, quota: int}` (pydantic response-model convention).
Tests: reports used+quota, reflects writes/deletes, quota=0 surfaces as disabled.

**Phase 4 — GC sweeper.** `_blob_gc_sweeper` in the lifespan calling
`spec.gc(mode="reconcile", t1, t2)` off-loop on `gc_interval`, behind a CAS lease.
Config knobs (`gc_interval`/`gc_t1`/`gc_t2`). Tests (inject `now`): orphan after a
delete is reclaimed past t2, a still-referenced blob (shared with a KB doc or a
second path) survives, t1 protects a fresh blob, lease lets only one runner act,
sweeper swallows/per-round-isolates errors like the other sweepers.

**Phase 5 — FE.** Usage bar (used/quota, useQuery on the usage endpoint,
invalidate on upload success/failure) in the AgentPanel attach + FileTree upload
areas; 507 → "out of space" message in the streaming-upload UI; i18n keys
(zh-TW + en). vitest: bar renders used/quota, updates after upload, 507 shows the
message, quota=0 hides the bar.

**Phase 6 — Gate.** Full suite + 100% coverage (`coverage run -m pytest &&
coverage combine && coverage report --fail-under=100`), `ruff check` +
`ruff format --check`, whole-project `ty check`, `web` typecheck + build.

## Out of scope (explicit)

- per-user / per-app / global-disk ceilings (per-workspace only; future layers).
- Physical/dedup-accurate accounting (logical only).
- Gating the sandbox mirror or agent-internal writes (choice B).
- Running `incremental` GC (reconcile suffices for v1).
