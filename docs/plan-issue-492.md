# Plan: durable sandbox 資料安全 — NFS 樹 + host-side rsync (#492 / #493)

> **#492**：「rollout restart hosted sandbox 時，資料會幾乎全部消失」。
> **#493**：agent/sandbox 四個症狀（504 假死、60s 被砍、idle 慢回無 streaming、
> 檔案時有時無）。
>
> 診斷後兩者共享一個根因家族：**hosted sandbox 是 ephemeral（無 PVC），
> durable 只靠一條 best-effort、逐檔 HTTP、序列、會 hang 的背景 mirror 撐著，
> 而讀取路徑用 pod-local session 解析 handle（沒 sticky 就閃現）。** 於是
> durable 長期落後、被 live 讀遮住，rollout 一次殺光所有 sandbox 就「現形」。
>
> 使用者要的兩項保證：
> 1. **同一時間只有一個 sandbox，且所有 pod 都解析到它**（specstar handle 為權威）。
> 2. **persistence layer 落後 sandbox 不超過 X 秒 / Y turn。**
>
> 這份 plan 把 workspace 持久層從「specstar-blob（DB metadata + blob）」換成
> **「NFS 檔案樹 + host-side rsync」**，從根本做到這兩項保證，而不是再修 mirror。

---

## 0 · 鎖定的決策（`/grill-me`）

| # | 決策 | 結論 |
|---|---|---|
| Q1 持久層形式 | DB-blob vs 檔案樹 | **NFS 檔案樹取代 specstar-blob**（僅 workspace 檔；KB `SourceDoc`/`DocChunk`、wiki `WikiPage` 各自的 store 不動）。理由：逐檔 HTTP+DB 寫是慢與 hang 的根源 |
| Q2 隔離約束 | PVC 會不會破 uid 隔離 | **不會**——PVC 是 **archive**，由 **root 身分的 host** 寫；per-item uid 從不直接碰它。live 目錄仍本機 ephemeral + 0700 + per-uid。之前否決的是「live 目錄放 PVC」，此處是「archive 放 PVC」，兩回事 |
| Q3 root_squash | NFS 預設 squash root | **免解**——archive **不保留 ownership**（rsync `-rlptD`，不帶 `-o -g`）；restore 時本機 copy 完 `chown -R` 成 item uid（`IsolatedProcessSandbox._provision` 已在做）。archive 檔可 nobody:nobody，隔離由本機那份 0700 維持。不需 `no_root_squash` |
| Q4 sync 位置 | app-side vs host-side | **host-side rsync**（host 掛 NFS，本機磁碟↔NFS 直傳）。bulk 資料完全不走 app↔host 網路 → 同時消滅 HTTP hang（效率問題）與 DB 逐檔寫（read_timeout 問題） |
| Q5 收斂權威 | 誰保證「只有一個」 | **address store**（`api/sandbox_address.py`，沿用）。handle 仍 pod-direct（POD_IP）。host 變 **item-aware**（`create(item_id)`），能按 item 命名 NFS archive + restore |
| Q6 讀取路徑 | 一律 NFS vs warm/cold | **保留 warm/cold**。warm **經 address store 解析那唯一 handle → live sandbox（即時）**；cold → 直接讀 NFS 樹。**這是(1)的根本修法**：拔掉 `peek_handle` 現在「先回 pod-local session」的閃現病根。理由：一律 NFS 會逼「讀取新鮮度」依賴「persist 間隔」→ 得壓到 <1–2s 才不卡；**把新鮮度（warm-live-read）與 durability（persist 間隔）解耦**才對 |
| Q7 persist 觸發 + 刪除 | 何時同步、刪除語意 | **turn-end `rsync --delete`（刪除只在這裡 reconcile；靜止 + `.ready` gate）**；**turn 中途每 30s upload-only**（只加不刪，純 durability checkpoint）；idle-reap／shutdown 各補一次 time-boxed `--delete`。X = **30s** |
| Q8 併發正確性 | 無 sticky 下 --delete 安全嗎 | **安全，且不需全域 quiesce**。rsync 來源是 sandbox 的**實體現況目錄**（併發 turn 的檔都在裡面），不是舊的 per-pod `_versions` 快取——真相來源是實體目錄。代價僅 NFS 短暫 churn，warm 讀看的永遠是 live 正確的 |
| Q9 `.ready` | 新模型還需要嗎 | **保留，改 gate 「persist」**：只有 restore 完成（`.ready` 在）才允許 persist。防「半 restore 的本機空目錄被 `--delete` 回 NFS 洗掉 archive」。語意比舊 sandwich 單純 |
| Q10 遷移 | 既有 blob 怎麼搬 | **M2 dual-read + 惰性回填**：`NfsTreeFileStore` 讀 miss → fallback 讀 specstar-blob 並順手複製到 NFS；寫一律進 NFS；背景 sweep 補完。specstar 當**唯讀安全網**，NFS 樹驗證完整前絕不退役。零停機、可回退 |

### 兩項保證如何達成

- **(1) 只有一個、大家都指到它**：寫/exec 走 `_acquire`（已對）；**讀取 warm 改走 address store**（Q6，這是缺的那半）。所有路徑經同一權威 → 閃現消失。
- **(2) 落後 ≤ X/Y**：turn-end persist（≤ 1 turn）+ 30s upload-only checkpoint（turn 中猝死 ≤ 30s）。host-local rsync 便宜到能負擔勤 persist，且**不會 hang**（不走 app↔host 網路）。

---

## 1 · 架構

```
app pod                                   sandbox-host pod (root, item-aware)
┌───────────────────────────┐            ┌────────────────────────────────────┐
│ ChatTurnEngine            │            │ live workdir: 本機 ephemeral        │
│  └ turn-end → registry    │  persist   │   {local}/{item}/root/  (per-uid    │
│     .flush(item) ─────────┼──HTTP ctl──▶│   0700)                             │
│ registry                  │  (小訊號)  │      │ rsync -rlptD [--delete]        │
│  └ _acquire: address CAS  │            │      ▼                               │
│  read/write (warm, 同源): │            │  NFS archive: {nfs}/{item}/...       │
│   resolve_io_handle →     ┼── op ─────▶│  (nobody:nobody, 純檔案樹)          │
│   address → live sandbox  │            │      ▲ rsync + chown 成 item uid     │
│  read/write (cold, ¬P):   │            │      │ (.ready gate)                 │
│   NfsTreeFileStore ───────┼────────────┼──────┘  restore on cold create      │
│    直接讀 {nfs}/{item}/   │  (app 也掛 NFS RO/RW)                             │
└───────────────────────────┘            └────────────────────────────────────┘
        │                                              ▲
        │ M2 遷移：讀 miss → fallback specstar-blob ───┘ (惰性回填 + 背景 sweep)
        ▼
   specstar-blob FileStore（唯讀安全網，驗證完整前不退役）
```

### 1.1 資料流

- **cold create（restore）**：host `rsync {nfs}/{item}/ → {local}/{item}/root/` + `chown -R uid` + 寫 `.ready`。
- **讀寫同源解析（`resolve_io_handle`）**：讀**與**寫走**同一個** resolver（`registry.resolve_io_handle`），所以永不「寫進 sandbox、讀到舊 NFS」。分三層：① 本 pod live session；② http → address store 的唯一 handle（**非 owner pod 也導向那一個 live sandbox**）；③ local 共享卷 → id 推導 handle。回 `None` = **全域 cold**。
- **warm read/write**：resolve 回 handle → 讀/寫那個 live sandbox（即時、同源）；下次 persist 帶回 NFS。
- **cold read/write（含上傳 endpoint）**：**僅當 resolve 回 `None`（全域 cold）**才直接讀寫 `NfsTreeFileStore`（`{nfs}/{item}/`，atomic temp+rename）；M2 期間讀 miss → fallback specstar-blob + 回填。
- **persist**：
  - turn-end / idle-reap / shutdown：`rsync --delete`（reconcile，含刪除），`.ready` gate + time-box。
  - turn 中途每 30s：`rsync`（upload-only，無 `--delete`）。

#### 1.1.1 為什麼 cold-write 只在 `¬P` 才安全（`Q→P`）

令 **P** = 「address store 有此 item 的 handle」、**Q** = 「有 live sandbox」。`_acquire` 是唯一生 sandbox 處，且**先 create 再 publish、輸 CAS 就自殺**，故 **`Q→P`**（活著⇒DB 有 handle），且對「我方 reap 不 forget」「host TTL reap」皆免疫（只造成 P 真 Q 假）。逆否 **`¬P→¬Q`**：**DB 空 ⟹ 一定沒有 live sandbox** ⟹ cold-write 進 NFS 絕對安全（沒人會 `--delete` 它）。所以 cold 判定**只看 DB 空不空**，不靠探活——`resolve_io_handle` 回 `None` 就是 `¬P`。這修掉了舊 `peek_handle` 在非 owner pod 上「per-pod 認 cold → 直寫 NFS → 被 turn-end `--delete` 抹掉」的失血洞。

#### 1.1.2 `P 但連不上 handle` 的處置（busy vs gone）

resolve 回了 handle（P），但那個 sandbox 連不上時，**讀寫做同一件事**，並靠錯誤型別分流（`http_client` 解開 `TransportError` 的等號）：

| 症狀 | 診斷 | 動作 |
|---|---|---|
| **timeout**（read timeout） | pod 忙、還活著 | **`SandboxBusy` → retry**：每次 read deadline 遞增、backoff 遞增（皆 capped），用光 → **fail loud**（**不** rebuild=避免 split-brain、**不** cold-write=避免被 `--delete` 抹） |
| **連不上**（connect fail） | pod 被刪 | `SandboxNotFound` → **rebuild**（`rebuild_io_handle` → `_acquire` 從 NFS restore + 重 publish） |
| **連得到、無資料夾**（404） | 被 reap | `SandboxNotFound` → **rebuild**（同上） |

`_alive` 也據此把 `SandboxBusy` 視為「活著」（busy≠dead），修掉 #493 g1「暫時性錯誤誤判成 dead → 開第二個 sandbox」。

### 1.2 為什麼這樣就不會再「資料全沒」

| 舊失血點 | 新模型如何堵住 |
|---|---|
| durable 只靠逐檔 HTTP mirror，會 hang（`read_timeout=0`） | persist 是 host-local rsync，**不走 app↔host 網路**，不會 hang |
| chat turn 結束不 flush | **turn-end persist**（Q7）+ 30s checkpoint |
| 讀取用 pod-local session → 無 sticky 就閃現 | 擁有者 pod：session handle→**live sandbox**（最新）；非擁有者 pod：無 session→**直接讀 NFS 樹**（host 保持 ≤30s 新鮮，本機掛載、零跨 pod HTTP）。**不再是永遠 stale 的 specstar snapshot**，最壞 ≤30s（Q6/使用者接受） |
| mirror 從一次 walk 推論刪除 → 連到錯/空 sandbox 洗 durable | 刪除改由**擁有者 host 對自己本機目錄** `rsync --delete`，靜止 + `.ready` gate；**不可能連到錯 sandbox** |
| per-pod `_versions` 基準不可靠 | rsync 真相來源是**實體目錄**，非記憶體快取 |

---

## 2 · Phases（flat integer、TDD）

### #492 核心：NFS 持久層重構

- **P1 · `NfsTreeFileStore`**（純 app-side，零 infra 依賴） ✅
  實作 `FileStore` Protocol over 檔案樹：`write/read/read_to_file/write_from_path/ls/delete/exists/is_dir/listdir/size`。路徑對映 `workspace_id + path → {nfs_root}/{item}/{path}`（用**真實 `/`**，不需 specstar 的 U+2215 swap）。**path-traversal 安全**（拒 `../` 逃出 item 目錄）、**atomic write**（temp + rename）。tmpdir 單元測試。

- **P2 · M2 遷移安全網** ✅
  一個 `FileStore` wrapper：讀先打 NFS，miss → fallback specstar-blob **並惰性複製到 NFS**；寫一律 NFS。背景 `_nfs_backfill_sweeper`（lease-gated，比照 blob_gc）把未被讀到的 stragglers 補完，全部驗證後才可手動退役 specstar。單元測試涵蓋 miss/hit/回填/寫入。

- **P3 · sandbox-host item-aware + rsync ops**（`sandbox-host/`，跨服務改動） ✅（+Q9 readiness gate）
  - `create` 收 `item_id`（HTTP 目前忽略 sandbox_id）；handle 仍 `pod_url + remote_id`。
  - cold create 時 `restore`：`rsync {nfs}/{item}/ → local` + `chown -R uid` + 寫 `.ready`。
  - 新 `POST /sandboxes/{id}/persist`（body: `{delete: bool}`）→ `rsync -rlptD [--delete] local → {nfs}/{item}/`，`.ready` gate。
  - host 掛 NFS mount。**真 rsync 的 integration 測試**（root-gated，比照既有 isolated_process 測試）。

- **P4 · app 接線（含(1)讀取修法）** ✅
  - **讀取修法（實作結果，優於原構想）**：durable 改成 NFS 樹（`FILESTORE_KIND=nfs_tree`）後，facade `_warm` 對非擁有者 pod（HTTP `handle_for_id`→None、無 session）回 None → 直接讀**本機掛載的 NFS 樹**（host persist 保持 ≤30s 新鮮）；擁有者 pod 仍讀自己的 live sandbox。這一步同時堵掉「永遠 stale 的 specstar snapshot」與跨 pod HTTP hang。
    - **拒絕的替代**：把 `peek_handle` 改 async、非擁有者讀也解析 address store 的唯一 handle → 會把每次非擁有者讀變成「跨 pod HTTP `download` 到別台的 sandbox」，正是本重構要消滅的 hang / 504 來源。NFS 樹 fallback（本機讀、≤30s、使用者已接受）嚴格更優，故不改 async。
  - cold 讀寫走 `NfsTreeFileStore`（P1）+ M2 wrapper（P2）。✅
  - `registry.flush` / `mirror_warm` / `kill_idle` / `enforce_quota` / `close_all` / `close_session` 的「write-back」改呼叫 **host persist**（P3），退役舊的逐檔 `SandboxSync.mirror` HTTP loop（`host_managed_durable` gate；`kind:local`/mock 保留原路徑）。✅ P4b
  - `restore` 改由 host 端 rsync（P3），`_acquire` 在 host-managed 模式跳過 app 端逐檔 upload。✅ P4b

- **P5 · persist 觸發策略** ✅
  - **turn-end reconcile**：`ChatTurnEngine` 加 optional async `on_turn_end` hook，在 `_run_turn` finally 於 `on_complete` 後 await；`ChatSendService` 注入 registry、傳 `on_turn_end=lambda: registry.flush(item, delete=True)`；KB chat 傳 None（engine 維持 app-agnostic）。
  - **30s 週期 upload-only**：`mirror_warm` sweeper 改呼叫 host persist(`delete=False`)，間隔 30s。
  - idle-reap / shutdown：`delete=True` + time-box。
  - `HttpSandbox` 給**有限 `read_timeout`**（擋殘餘 warm-read HTTP hang；bulk 已不走 HTTP）。

- **P6 · quota + GC 收尾** ✅（by construction）
  #245 workspace quota 改 `du` on `{nfs}/{item}/`（取代 specstar `Sum` aggregate）——已由 P1 `NfsTreeFileStore.workspace_usage`（du）+ P2 `MigratingFileStore.workspace_usage`（legacy∪primary `stat_all`）交付，facade duck-type 之，含單元測試。**退役 workspace blob GC**：`nfs_tree` 下 `WorkspaceFile` 不再是 specstar blob 模型，故 `spec.gc(reconcile)` 自然掃不到 workspace blob（emergent no-op，無碼可刪）；M2 期間 legacy `SpecstarFileStore` 仍註冊 `WorkspaceFile` → blob GC **繼續保護** legacy workspace blob（正確，遷移完成前不可退役）。KB/wiki 的 blob GC 完全不動。

- **P7 · k8s + ops env** ✅
  NFS PVC（`ReadWriteMany`）掛 sandbox-host + app；`deploy/sandbox-host.example.yaml` 加 NFS volume。**順手補症狀 2/3 的 ops env**（見下）進範例 + `config.example.yaml` 註解醒目化。

- **P8 · integration 測試（證明保證成立）** ✅
  - 模擬 host rollout：turn 中殺 sandbox → 斷言 durable 遺失 ≤ 30s、restore 後全回。
  - 非擁有 pod 讀：斷言解析到同一 handle、無閃現。
  - 併發 turn + `--delete`：斷言不誤刪對方檔案。
  - M2 惰性回填：斷言 specstar → NFS 零遺失。

### #493 其餘三症狀（獨立、較小，可平行）

- **P9 · 症狀 1（504 假死）** — 獨立小 PR，與 NFS 無關 ✅（backend detach+heartbeat+ingress；frontend 重連+gateway 容忍）
  - `send_into` 改 **fire-and-forget**：`enqueue` 後 route 立刻回 202，不再 await turn future。
  - 前端 504／送出失敗**不當 turn 失敗**（不關 `streaming`、不關 #202 store-poll fallback）。
  - `subscribe_sse` 加**週期 heartbeat**（防 ingress idle 斷 SSE）；前端非 Abort 的 stream error 走 backoff **自動重連 + rehydrate**。
  - ops 止血：ingress `proxy-read-timeout`。

- **P10 · 症狀 2（60s exec 被砍）** — 純 ops（併入 P7 範例 + 文件） ✅
  真兇 = 獨立 sandbox-host 服務自己的 `exec_timeout=60.0` 預設，使用者調的 config 全沒抵達 host。
  - **設 sandbox-host pod env**：`SANDBOX_HOST_EXEC_TIMEOUT=3600`、`SANDBOX_HOST_LOG_TIMEOUT=1500`。
  - `deploy/sandbox-host.example.yaml` 補這兩個 env（目前缺 = 誤導來源）；`config.example.yaml` 的 `sandbox_host:` 註解標明「app 不讀、要設 host pod env」。
  - （DX）host 啟動時把生效的 timeout 印進 boot log。

- **P11 · 症狀 3（idle 慢回無 streaming）** — ops + 小 code ✅
  真兇 = host `idle_ttl=1800s(30min)` 比 app `idle_timeout=8h` 早 16× 砍活 session。
  - **ops** ✅：`SANDBOX_HOST_IDLE_TTL` 拉到 > app 的 8h（已隨 P7 進範例：`deploy/sandbox-host.example.yaml` = `36000`s / 10h）；讓「當機備援」回到它該有的角色。
  - **code** ✅：cold-wake 的 app-side restore 期間 emit 進度事件——`SandboxSync.restore` 加 `on_progress(done, total)`，經 `registry.ensure_handle → _acquire → sync.restore` 串到 `AgentToolContext.on_restore_progress`（runner 設，比照 `on_exec_output`）→ 新 `RestoreProgress` SSE 事件（ephemeral，不入 transcript，比照 `FailoverSwitch`）→ 前端 `agentLog.reduceAgent` 記到 `log.restore` → `TurnStatus` 顯示「還原工作區… N/M」取代空白 running 卡片（restore 一結束即由 tool/message/next-turn 清掉）。host-managed（http）的 restore 由 host-rsync 完成、夠快，故此進度事件只在 app-side（local/VM）restore 路徑觸發——正是較慢那條，符合 P4「錦上添花」定位。

---

## 3 · 風險與緩解

| 風險 | 緩解 |
|---|---|
| 遷移丟資料（正是我們要修的災難） | **M2**：specstar 當唯讀安全網，NFS 驗證完整前絕不退役；每步可回退 |
| NFS 暫時不可用 | persist 失敗 → 下輪重試；只要 host pod 活著，live 目錄仍有資料。極端（NFS 掛 + host 同時死）才丟，可接受 |
| 併發 app-pod cold 寫同一 item | 不同檔 = NFS 併發安全；同檔 = atomic temp+rename，last-write-wins（upload endpoint 罕見併發） |
| host 變 item-aware + 掛 NFS = 角色變重 | 這是正確歸屬（sync 資料的地方就該管 durable）；`kind:local`/mock 完全不受影響 |
| root_squash 擋 chown | 已解（Q3）：archive 不保留 ownership，chown 只在本機 |
| P3 是跨服務改動、需 root + NFS | integration 測試 root-gated；每個 privileged op 加 seam，模組維持 100% 單元覆蓋 non-root |

## 4 · 交付順序建議

1. **P1 → P2**（純 app-side，可立即 TDD、零 infra；先把 `NfsTreeFileStore` + M2 安全網做出來並測透）。
2. **P3**（host 端 rsync ops，跨服務、需 root/NFS integration 測試）。
3. **P4 → P5 → P6**（app 接線 + 觸發策略 + quota/GC 收尾）。
4. **P7 → P8**（k8s 部署 + 端到端證明）。
5. **P9 / P10 / P11** 可**任意時間平行**插入（症狀 1 是獨立 PR；症狀 2/3 主要是 ops，P10/P11 的範例改動併 P7）。

> 每完成一個 phase commit 一次（flat integer phase，per CLAUDE.md）。
