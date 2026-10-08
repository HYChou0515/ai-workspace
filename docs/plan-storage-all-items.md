# Plan — Storage you can see: every item you own, its size, and Delete

## Problem

"我的資源 → 儲存空間" lists the rows of the disk ledger (`_WorkspaceDisk`,
`quota/disk_ledger.py`), not the items a person owns. The ledger is written only
by live activity — a growing write through the file facade, a delete, or the
mirror sweep of THIS pod's warm sandboxes — and only for people with a disk
limit. So:

- an item untouched since the ledger shipped (2026-08-01), or since its owner
  was given a limit, has **no row**: it is not listed and counts 0 toward the
  person's total and their limit;
- an item whose sandbox was reaped keeps the **last** number measured while it
  was live — a figure that can be stale (bytes deleted by `exec` before the
  reap are never reported);
- a person with **no limit** sees no storage at all (`disk_tracked=false`).

Reported 2026-09-04 (memory `project_my_resources_storage_completeness`) and
again 2026-10-08: 「storage quota 竟然只統計 active sandbox?」.

## Locked decisions (from /grill-me, 2026-10-08)

**user** = the user answered it; **mine** = an implementation choice listed for
overturning.

| # | Question | Decision | Source |
|---|---|---|---|
| 1 | What a person's storage covers | **Every item they own** — idle, reaped, never opened — with its real size: a live item as its sandbox measures it (as today), an item with no live sandbox as its durable copy measures it (the source the per-item quota already uses when the item is cold, `facade.py:991`). | user |
| 2 | Without a limit | **Still tracked and shown.** "就算沒有quotation限制 也應該顯示他用多少". | user |
| 3 | Delete | Every row keeps its **Delete** (the item-delete cascade that is already there). Everything else on the page as today. | user |
| 4 | The total and the limit | **The same ledger, so they become right too**: items that counted 0 start counting; a person with a limit can be over it the moment this ships (writes refused, deletes always allowed). Said in the runbook. | user |
| 5 | Who keeps the ledger complete | A **reconcile pass**: every item in every registered app, owner by the gate's own rule (`apps.resolve.debtor_of`), and for every item whose sandbox is not live anywhere (`_SandboxActivity`) the durable size — a missing row is created, a stale size refreshed, a wrong owner corrected, a row of an item that no longer exists dropped. Live items are left to the mirror (their durable copy is additive while live — #538). | mine |
| 6 | Where it runs | A job on the **blob-gc worker** (it already boots the API's whole composition — filestore, ledger, item models), as a second payload kind; the API only produces it, once per window, behind a `ScanLease` (CLAUDE.md sweeper rule, #804). No new JobType. | mine |
| 7 | How often | `resources.disk_reconcile_interval_sec`, default **21600** (6 h); `0` turns it off (`create_app` defaults it off, so a test app does not reconcile at startup). The producer asks at its FIRST tick — that pass is the backfill. A reaped item's size can lag by up to one interval. A worker that cannot reach an `nfs_tree` durable tree REFUSES the pass rather than book 0s. | mine |
| 8 | Writes for everyone | The "only when this person has a disk limit" gates go (`_person_disk_gate`'s record, `_record_usage`): a growing facade write costs one ledger upsert for everyone (the limit check itself still runs only for capped people). | mine |
| 9 | Rows of items that no longer exist (the pre-cascade ghost rows `plan-delete-item-cascade` deferred) | Dropped by the pass — only on `find_work_item`'s word that the item is gone. The user: 「沒有這些東西 所以沒關係」 (there are none in this deploy). | user |

Overturns `docs/plan-sandbox-resource-quota.md` P6's "第一次量測前會低估" /
"略微過期 … 即時加總所有 item 太貴" and §3.1 fix 3 "帳本只在『這個人真的被設了
disk 上限』時才寫" (marked there).

## Phases

- **P1** — ledger for everyone: remove the cap gates on recording; the panel
  shows storage whether or not a limit is set (`disk_tracked` goes).
- **P2** — the reconcile pass (`quota/disk_reconcile.py`): pure function over
  (items, liveness, durable size, ledger) → upserts / forgets; tests against
  the real ledger and filestore.
- **P3** — run it: blob-gc job kind `disk-ledger`, the `ScanLease` producer,
  the interval knob; worker test through the real coordinator.
- **P4** — docs (`migrations.md` entry, `configuration.md`, the k8s worker
  manifest's opt-in NFS mount), review rounds, CI.
