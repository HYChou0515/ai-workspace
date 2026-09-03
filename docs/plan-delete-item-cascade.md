# Plan — deleting an item deletes what the item owns

## Problem

"When the disk quota is full, users can only go into each item and delete
files one by one." The investigation found why: item deletion EXISTS — specstar
auto-generates soft and permanent DELETE routes for every WorkItem, and the
chat rail's ⋯ menu already calls `/permanently` behind a confirm — but it
deletes **only the item row**. Everything the item owns becomes an orphan:

| Orphaned today | Where |
|---|---|
| Durable file rows + their blobs | `WorkspaceFile` / `_WorkspaceDirs` (`filestore/specstar_impl.py:51-81`); blobs stay live because revisions stay |
| NFS-tree snapshot / host archive | `{nfs_root}/{workspace_id}/…`; `sandbox-host` `{archive_root}/{item_id}/` |
| Live sandbox dir | `{sandbox.root}/{item_id}/` — never killed |
| Address / activity rows | `api/sandbox_address.py`, `api/sandbox_activity.py` |
| Conversations, workflow runs | `Conversation.item_id`, `WorkflowRun.item_id` |
| **The disk-ledger row** | `_WorkspaceDisk` — frozen at its last measured size, **charged to the owner forever** |

The ledger part is the sting: `DiskLedger.forget()` exists and is called from
nowhere, and once the item row is gone `find_work_item` → `_facts_of` yields
`owner=""`, so no code path can ever update or remove the row again
(`api/app.py:594-605,637-638,699-701`). **Deleting an item makes the owner's
quota permanently worse** — the exact opposite of the reason anyone deletes.
That is why "delete files one by one" is the only working refund path today.

Secondary damage: `MyResourcesPage` renders a ghost row (empty slug/title,
link to nowhere) for a deleted item's ledger entry; RCA/PM (`ide`/`views`
apps) have no delete affordance at all.

## Locked decisions (from the grill, 2026-09-03)

| Question | Decision |
|---|---|
| Semantics | **Hard delete, cascade, no trash/undo.** Matches the existing button's contract ("This can't be undone"); a soft-delete trash would refund quota while the bytes still occupy disk — a lie in the ledger. The zip export (`files/zip_download.py`) is the escape hatch, offered in the confirm dialog. |
| Surfaces | (1) **Fix the existing rail ⋯ Delete** (chat-surface apps) by repointing it at the cascade; (2) **add Delete to `MyResourcesPage` per-item disk rows** — the one screen that shows which item eats the quota, and the screen `ide`/`views` items appear on (the `:194-196` comment declining a delete there predates cascade deletion — its premise is gone). No AppDashboard card menu this round. |
| Ghost-row sweep (pre-existing orphans) | **DEFERRED — operator's explicit call**: wait for an actual case before building the background sweep. New deletions stop creating ghosts (the cascade includes the ledger), so the population cannot grow. |
| Who may delete | Unchanged: owner or superuser (`perm/checker.py` `_OWNER_ACTIONS` already covers `delete`/`permanently_delete`). |

## Design

### One route owns the cascade, and ORDER is the design

`DELETE /a/{slug}/items/{item_id}` (item_routes, beside the existing generic
`close`), authz owner/superuser, then:

1. **Stop the turn machinery first**: cancel any local in-flight turn,
   `turn_engine.forget(item_id)` — and refuse the delete politely if a turn
   cannot be stopped (report, don't half-delete).
2. **Kill the environment**: `registry.close_session`-equivalent sweep —
   write-back SKIPPED (we are deleting; writing back first would be wasted
   I/O and re-inflate the snapshot), `sandbox.kill` (rmtree of the shared
   dir, `.ready` unlinked first), **clear the address row** (close
   deliberately keeps it; delete must not), `activity.forget`.
3. **Delete the durable storage**: every `WorkspaceFile` row via
   `permanently_delete` (what makes blobs collectable by the existing GC —
   soft-deleted rows keep revisions, hence blobs, forever), the
   `_WorkspaceDirs` row, the nfs_tree subtree when that backend is active,
   the host archive dir (via the sandbox-host API if one exists; else
   flagged as a follow-up with the gap stated in docs).
4. **Delete the item's records**: Conversations (+ messages) and
   WorkflowRuns for the item, permanently.
5. **Refund the quota**: `disk_ledger.forget(item_id)` — the never-called
   method finally gets its caller.
6. **Delete the item row LAST**, permanently. Everything above needs the row
   (owner resolution, facts); deleting it first is exactly how today's
   orphans are made.

Partial failure leaves the item row in place, so the operation is retryable
and the ledger is never stranded — the row is the transaction marker.

### Close the old footgun

The FE repoints to the cascade route. The raw specstar
`DELETE /{model}/{id}/permanently` for WorkItem models is then a
second, broken door — **block it** (per-model route-template exclusion if
specstar supports it; otherwise refuse via the permission layer with a
message naming the cascade route). Two doors where one orphans storage is a
guaranteed regression; the generic soft-delete route stays untouched (no FE
caller; its pre-existing 410-on-soft-deleted behaviour is out of scope).

### What deletion does NOT touch

- **The KB**: knowledge promoted from the item's chats (close-item flow) is
  collection-scoped, not item-scoped. Deleting the item never deletes
  knowledge — stated in the confirm dialog.
- Other items, the user's other ledgers, group permissions.

### Multi-pod hazards to verify (not assume)

- **Resurrection by another pod's mirror**: the shared dir is per-item on a
  shared volume; killing it removes `.ready`, and the mirror's sandwich +
  per-item resilience should make a remote pod skip, not re-create. A test
  must prove a post-delete mirror sweep re-creates nothing (the #366/#492
  lessons: records vs reality).
- **Turn on another pod**: cross-pod cancel is pod-local (#349). The window
  is narrowed by deleting the item row (new sends 404) and accepted as-is
  beyond that — same exposure `close` has today; stated in the route's doc.

### FE

- Rail ⋯ Delete → the cascade route; confirm upgraded from `window.confirm`
  to a dialog that says what dies (files, conversations, workflow runs —
  with the live usage number), links "先下載 zip", and states KB knowledge
  survives.
- `MyResourcesPage` per-item disk row gains the same delete (+dialog), and
  the page invalidates its query so the gauge drops immediately — the
  user-visible proof the refund worked (做完=看得到).
- Ghost rows (pre-existing orphans): rendering left as-is, deferred with the
  sweep.

## Phases (one commit each)

1. **Backend cascade route** — the ordered sweep, red-first per step;
   includes the resurrection test and the partial-failure/retry contract.
2. **Footgun closure** — block the raw permanent route for WorkItem models;
   test through the real HTTP route.
3. **FE** — repoint rail button, MyResourcesPage delete, upgraded dialog,
   query invalidation. FE tests via vitest (dialog contract, refund visible).
4. **Docs + review + PR** — API doc for the route, MyResourcesPage comment
   replaced (its premise changed), knob-scope-style table of "delete paths ×
   what each cleans" (close vs delete vs kill_idle), adversarial review, PR
   body last. No migrations.md §5.5 entry (no new config option); the
   deferred ghost sweep gets a tracking issue instead.

## Test plan (red first, targeted only)

- Route: owner deletes → 200; non-owner → 403; unknown item → 404; item row
  gone only after every step (fault injection at step 3 leaves the row).
- Storage: durable rows permanently gone (blob GC collects — asserted via
  revisions, not GC timing), sandbox dir gone, address cleared, activity
  forgotten, conversations/workflow runs gone, `disk_ledger.total_for(owner)`
  drops by the item's share.
- Resurrection: a mirror sweep after delete re-creates no rows.
- Footgun: raw `/permanently` on a WorkItem → refused, names the new route.
- FE: dialog lists the three categories + zip link; confirming calls the
  cascade; gauge query invalidated.

## Verified ground truth (file pointers)

- Existing delete routes + FE button: specstar `crud/core.py:579-596`;
  `ChatListRail.tsx:362-374` → `useChatActions.ts:26-29` → `real.ts:247-251`.
- Orphan mechanism: `DiskLedger.forget` unused (`quota/disk_ledger.py:119-126`);
  owner resolution dies with the row (`api/app.py:594-605,637-638,699-701`).
- Close ≠ delete: `api/item_routes.py:351-441` + `api/registry.py:658-767`
  (keeps the address row on purpose; never touches the ledger).
- Blob liveness: GC rescans revisions; only `permanently_delete` frees them
  (`filestore/specstar_impl.py:390-397`, `filestore/blob_gc.py`).
- Quota gate refuses turns/growth, never deletes (`api/turn_gate.py:89-120`,
  `files/facade.py:631-664`).
