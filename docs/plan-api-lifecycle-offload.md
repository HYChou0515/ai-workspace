# Plan — 把 API lifecycle 裡「不是 pod-local 的工作」搬離 API pod（PR #804）

## 背景

2026-09-14 prod 每顆 API pod 零流量也長：1.5G 開機 → 4G/12min，60–90 分一次 OOM，每顆 pod 一條
user-space busy thread。元兇（prod 證實）是 `api/lifecycle.py` 的 `cluster_sweeper`（#506 P8）：
每顆 API pod 各自每 900s 把每個 collection 的全部 `ClusterMember`（含向量）讀進記憶體，再對每個
未投影候選逐條打 embedding。「冪等所以不做選主、每顆 pod 都跑」把 N 倍的全表讀當成免費。

P1–P3（已推、CI 中）把它改成 card-gen worker 上的 `cluster_sweep` job，API 只剩 enqueue。

用同一把尺再掃 lifecycle —— **全店/全表的工作、或每顆 pod 重複做同一件事、而它並不依賴
pod-local 狀態** —— 還有四個，決定跟 #804 一起做完、不拆：

| # | 現況 | 為什麼不該在那 | 搬去哪 |
| --- | --- | --- | --- |
| A | `blob_gc_sweeper` → `spec.gc(mode="reconcile")`：搶到 lease 的那顆 API pod 每小時掃遍**所有 model 的所有 revision**，把每個 live blob id 收進一個 Python `set`，再走一遍所有 blob | 全 store 讀進記憶體；跟 cluster_sweeper 同類，只是有 lease、頻率低 | **不搬（見下）** |
| B | `user_schedule_loop`：**每顆 pod** 每 tick 透過 files facade 讀索引裡每一頁的 `schedules.json` | CAS 只去重「觸發」不去重「讀」；N pod × N 頁 × 每分鐘 | 同一份 window-claim ledger 加一個「掃描 lease」，一個 window 一顆 pod 掃 |
| C | `trigger_sweeper`：每顆 pod 每 tick 重讀所有 profile 的 `triggers.json` + 逐 trigger specstar 讀 | 同 B 的形狀，成本較小 | 跟 B 併成一條 loop，同一個 lease |
| D | 開機 `seed help collection`：`Ingestor.ingest` = store + **同步 index**（chunk + embed）在 API 上做 | 上傳路徑的慣例是 store + 排 index job；這裡沒照做，變成 readiness 延遲（本機 fresh store 49s） | `store` + `index_coordinator.enqueue` |

**留在 API、有理由的**：`idle_killer` / `mirror_sweeper` / `sweep_uv_cache`（pod-local 沙盒 session
與 scratch，#345）、`goal_offhours_sweeper`（要起 turn，turn engine 只在 API；查詢是索引）、
`code_sync_sweeper` / `reflect_sweeper`（純 producer）、`index_sweeper`（窄索引查詢）、
`notification_delivery_sweeper`（索引 + 批次上限的純轉送）。

## 判準（之後任何 lifecycle sweeper 都照這條）

> lifecycle 裡的 loop 只能是兩種：**pod-local**（只碰這顆 pod 自己的 session / scratch），
> 或 **純 producer**（列出該做的事、enqueue 給 worker，每 tick 的讀是索引查詢）。
> 全表讀、打模型、走整個 store 的 —— 一律是 job，跑在 worker 上。
> 唯一例外：blob GC（它的正確性需要 API 才有的完整 model 集；靠 lease 讓一顆 pod 跑）。

這句話落到 `CLAUDE.md` 的 Key conventions（P7）。

## 已拍板的決定

0. **A 不搬。** 動手前讀了 `filestore/blob_gc.py` 的 module docstring：**GC 必須跑在所有會引用 blob
   的 model 都已註冊的 spec 上**。specstar 的 `_gc_reconcile` 只從 `self.resource_managers`（已註冊的
   model）收 live set；worker 的 spec 沒有 filestore ⇒ 沒有 `WorkspaceFile`，而 `make_spec` 之外還有
   30 個 `add_model` 註冊點（filestore、monitor、各 job model、lifespan 的 coordination model）。搬到
   worker ⇒ 那些 model 的 blob 全被當孤兒 ⇒ t1 後 quarantine、t2 後**永久刪除**。這是資料遺失，不是
   效能。API 上的它有 lease（一顆 pod、一小時一次），不是 per-pod 重複那一類，記憶體是 pass 結束就
   釋放的暫時集合 —— 留著是對的。要搬的前提是 specstar 的 GC 改成按 table 探索而非按已註冊 model
   （specstar 的功能，不在這個 PR）。以下 1–2 因此作廢，留作紀錄。
1. ~~**A 走「API producer tick → `maintenance` job → 專屬 worker」，不走 CronJob。**~~（作廢）
   repo 的 CronJob 先例（`cronjob-graph.yaml`）是 `curl POST /api/graph-job`，後面本來就要有
   worker；既然 worker 一定要有，API 留一個純 producer tick（跟 cluster sweep 同形、coalesce）
   就能保住 `filestore.gc_interval_sec` 的原意（「多久跑一次；0/None = 關」），不必改 k8s
   CronJob、不必動 config 語意。CronJob 是 wall-clock 排程（每週六 03:00）才需要的東西。
   先前「改成 CronJob 最乾淨」的說法在此收回。
2. ~~**A 開新的 JobType `maintenance`（自己的 worker），不掛在 kb-import 上。**~~（作廢） blob GC 是
   filestore/specstar 全域的維護，沒有 KB 的 domain home；`maintenance` 是誠實的名字，之後同類
   的維護工作（例如 #778 幽靈列 sweep）有地方去。cluster sweep 掛 card-gen 是因為 reconcile
   本來就住那裡 —— 兩者判準一致：**跟它同 domain 的 worker；沒有就開自己的**。
3. **B + C 併成一條 loop、一個 lease。** 兩者本來就共用 `trigger_check_interval` 與同一個
   window-claim ledger（`SpecstarTriggerStore.try_claim(key, window)` 就是「一個 window 一人贏」
   的 CAS）；用合成 key `__sweep__:scheduled-work`、window = `floor(now / interval)` 當掃描
   lease。輸家整個 tick 不做（不 load、不讀檔）。贏家死在半路 ⇒ 那個 window 的掃描沒了，
   下個 window 別人接手 ⇒ 最多遲到一個 interval，跟現在 `trigger_check_interval` 的語意
   （「最晚會遲到多久」）一致。**每個 schedule / trigger 自己的 window claim 保留**（跨重啟的
   冪等性靠它）。
4. **D 的 `seed_help_collection_best_effort` 多一個 `index` seam**：lifespan 傳
   `index_coordinator.enqueue`；不傳（scripts / tests）就是原本的 inline `ingestor.index`。
   「best-effort、embedder 掛了不擋開機」這個性質由 job 天然給。

## Phases（接續 P1–P3；flat integer）

### P4 — ~~`maintenance` coordinator + `blob_gc` job；API 變 producer~~（作廢，見決定 0）

- `src/workspace_app/maintenance/coordinator.py`（新）：`MaintenanceJob(Job[MaintenancePayload,
  MaintenanceArtifact])`，`MaintenancePayload.kind = "blob_gc"`，`partition_key = "blob_gc"`
  （全域序列化）。handler 呼叫既有 `filestore.blob_gc.run_blob_gc(spec, t1, t2, ttl_ms, monitor)`
  （lease 照舊：擋 manual POST 與 loop 撞在一起）+ 既有的 `filestore.census()` → `monitor.record
  ("ws_census")`。`register_gc_lease(spec)` 搬進 coordinator ctor。
  `enqueue_blob_gc()` 對 active `blob_gc` job coalesce（照 `enqueue_cluster_sweep`）。
- `coordinators.py`：`CoordinatorBundle.maintenance`；`build_coordinators(..., gc_t1, gc_t2,
  gc_lease_ttl_s, monitor)` —— **兩個呼叫者都接**（`create_app`、`worker.build_bundle`；
  worker 端 monitor 用 `SpecstarMonitor(spec)`，跟 `__main__` 同款，census 才會落到 API 讀得到
  的同一個 store）。
- `worker/__init__.py`：`_JOBTYPE_ATTR["maintenance"] = "maintenance"`。
- `kubernetes/base/workers.yaml`：`rca-worker-maintenance`（照 `rca-worker-kb-import` 的形狀，
  1 replica、無 HPA —— 它一小時一件事）。
- `api/lifecycle.py`：`blob_gc_sweeper` 改成純 producer（每 `gc_interval` 呼叫
  `app.state.maintenance_coordinator.enqueue_blob_gc()`）；`register_gc_lease`、`run_blob_gc`、
  `filestore.census()`、`monitor` 從 lifecycle 消失；`gc_t1/gc_t2` 參數消失。
- **Tests（先紅）**：
  1. `enqueue_blob_gc()` + `aclose()` ⇒ 一個孤兒 blob 被 quarantine / 舊 quarantine 被刪
     （用 specstar in-memory backend 造孤兒；斷言 `GcStats` 或 blob 不在 `iter_active`）。
  2. 連叫兩次 ⇒ 一個 job（突變：拿掉 guard 必紅）。
  3. `create_app(run_consumers=False, gc_interval=…)` 進 lifespan ⇒ 留下一個 pending
     `blob_gc` job、**沒有** `blob_gc` 的 monitor 記錄（API 沒自己做）—— 這條在未修版本上紅。
  4. worker 真入口 `build_bundle(settings)`：`settings.filestore.gc_t1` 改成一個奇怪值 ⇒ job 跑出來
     的 `GcStats` 反映它（釘 worker 那條接線；突變：拿掉 kwarg 必紅）。
  5. `select_coordinator(bundle, "maintenance")` 回 maintenance coordinator。

### P5 — 掃描 lease：`user_schedule_loop` + `trigger_sweeper` 併成一條、一個 window 一顆 pod

- `workflow/triggers.py`：`TriggerSweeper.tick()` 前先 `claim_scan(window)`；輸 ⇒ 整個 tick 不做。
  `UserScheduleSweeper.tick()` 同。兩者共用一個 `ScanLease(store, key, interval)` 小物件
  （`claimed(now) -> bool`，底層就是 `store.try_claim(key, window)`）。
- `api/lifecycle.py`：兩個 task 合成 `scheduled_work_sweeper`：一次 claim，贏了才跑
  `trigger_sweeper.tick()` + `user_schedule_sweeper.tick()`（各自仍 `suppress(Exception)`）。
- **Tests（先紅）**：
  1. 兩個 `TriggerSweeper` 共用同一個 store（兩顆 pod），同一 window 各 `tick()` 一次 ⇒
     注入的 `load` 只被叫 **1** 次；下一個 window 再叫 1 次。
  2. `UserScheduleSweeper` 同：兩個 sweeper、同 window ⇒ 索引的 `items_with_paths` 被叫 1 次。
  3. 贏家 tick 丟例外 ⇒ 同 window 沒人再掃、下個 window 有人掃（遲到 ≤ 1 interval 的契約）。
  4. 既有 lifespan / triggers 測試全綠（單 pod 下永遠贏，行為不變）。

### P6 — help seed：store + enqueue index

- `kb/help_collection.py`：`_ingest_help_docs(ingestor, cid, user, index=None)`；
  `index` 給了就 `ingestor.store(...)` 後逐 doc `index(doc_id, cid, requested_by=user)`，
  沒給就 `ingestor.ingest(...)`（原路）。`seed_help_collection_best_effort` 透傳。
- `api/lifecycle.py`：傳 `index=app.state.index_coordinator.enqueue`。
- **Tests（先紅）**：
  1. `create_app(run_consumers=False)` 進 lifespan ⇒ help collection 的 SourceDoc 存在、
     `status == "indexing"`、有一個 pending index job、**沒有** DocChunk（API 沒自己 embed）。
  2. `run_consumers=True`（既有測試）⇒ 最終 `ready` + 有 chunk（行為不變）。
  3. `seed_help_collection(...)`（同步路徑，scripts 用）不傳 `index` ⇒ 立刻 `ready`。

### P7 — 帳本 + 慣例 + review

- `docs/migrations.md` §5.5：
  - ~~`filestore.gc_*`~~（P4 作廢，GC 不動，沒有帳要記）。
  - `server.trigger_check_interval_sec`（既有，語意再擴大）：現在也是掃描的 window；單 pod 不變。
  - 開機 help seed（無選項）：只 store、變動文件排 index job；`run_consumers: false` 而沒 index
    worker 的部署 help 文件停在 `indexing`（那種部署本來所有 KB 索引都不動，同一個洞）。
- `CLAUDE.md` Key conventions 加一條上面的判準（lifecycle = pod-local 或純 producer；blob GC 是例外及理由）。
- `docs/configuration.md`（`trigger_check_interval_sec` 多 pod 語意）、
  `docs/subsystems/boot-and-config.md`（lifespan 敘述：seed 只 store、sweeper 兩類）。
- 對抗式 review 一輪（換鏡頭：符合度 / 真實性 / 回歸），有發現就砍 CI 重推。

## 不做的

- **blob GC 留在 API**（決定 0）。

- 不動 `mirror_warm` 的每 tick 走檔案樹（pod-local，cost ∝ 這顆 pod 的 warm item，是 #345 的設計）。
- 不把 `goal_offhours_sweeper` 搬走（它要 turn engine）。
- 不新增 CronJob、不改 `filestore.gc_interval_sec` 的語意。
- 不處理 #723（job 的 auto-CRUD 路由人人可 POST）：`maintenance-job` 跟 `graph-job` 一樣曝露，
  lease + coalesce 讓多 POST 無害，但誰都能觸發一次全 store GC 這件事跟 #723 一起解。

## 驗證

- 每個 phase：targeted tests + `ruff check` / `ruff format --check` / `ty check`，commit 一次。
- 全部推完：真入口起一顆 `run_consumers: false` 的 API（本機）⇒ 看到 `cluster_sweep` job
  pending、help SourceDoc `indexing`、沒有 DocChunk；再起 `python -m workspace_app.worker
  card-gen` + `index` ⇒ 兩者都被吃掉。
- CI 綠 + review 乾淨才報。
