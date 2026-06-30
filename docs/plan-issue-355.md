# Plan — Issue #355：Web 表單建立 code collection + sync 非同步 job 化 + 每日 03:00 自動同步

> #281（PR #336 / follow-up #340，見 [`plan-issue-281.md`](plan-issue-281.md) /
> [`plan-issue-281-followup.md`](plan-issue-281-followup.md)）做出了分層 code-wiki builder：
> 「有 `git_url` 的 collection 開 `use_wiki` → git-clone 整份原始碼 → L0/L1/L2 分層生成 wiki」。
> 但**前端沒有任何入口**——`NewCollectionModal` 只有 use_rag / use_wiki 兩個 toggle，
> `git_url` / `git_branch` / `git_token` 都沒接出來，全 FE 也沒有「Sync now」按鈕或同步狀態。
> 今天只能 `curl POST /kb/collections` + `POST /sync` 才能把 codebase 餵進系統。
>
> 本 issue 補上完整的 web 閉環，並順手把 clone+ingest 從「**同步行內跑在 API**」重構成
> 「**非同步 job、跑在 wiki worker**」。經 `/grill-me`（Q1–Q11）鎖定,否決方案就地記錄。
>
> **核心動機**:讓「讓系統吃一份 codebase」這件事能**純靠網頁**完成、看得到進度、失敗看得到原因。

---

## 0 · 進度總覽（flat phases）

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | **每日同步閘門**:新增 config `kb.git.daily_sync: "HH:MM"`（伺服器當地時間;null=關閉）+ `CodeRepoSweeper.tick` 改「每日某時刻」閘門,全面取代舊 `sync_interval_hours` 間隔邏輯 | ✅ |
| **P2** | **`code_sync` job**:`wiki` JobType 新增 `op="code_sync"` handler（clone+ingest → 鏈到既有 `code_split`）;`CodeWikiBuildRun` 加 `cloning`/`ingesting` phase + clone/auth 失敗記 `last_error` | ✅ |
| **P3** | **觸發端非同步化**:`/sync` route 與 `tick()` 改成 enqueue `code_sync` 後**立刻返回**(契約改 `status="queued"`);移除 lifecycle loop 逐 cid `trigger_code_build`;更新既有 /sync 同步行為測試 | ✅ |
| **P4** | **建立表單(FE)**:`NewCollectionModal` 分段切換 Documents \| Code repository + code 模式欄位;`createCollection` 帶 git 欄位 | ✅ |
| **P5** | **首次 sync + 狀態 strip(FE)**:建立後導向 collection 頁 + 自動 enqueue 首次 sync;collection 頁 sync status strip（複用 #162 strip + `/wiki/status` 輪詢）| ✅ |
| **P6** | **編輯既有 git 連線**:`CodeConnectionEditor`(collection 頁設定選單→「Git connection」,只 code collection 顯示)換 branch / 輪換 token(placeholder 不回填、PATCH 帶 git 欄位)| ✅ |

### 實作偏離 / 註記(實作時定的)

- **stamp-on-attempt(P1)**:為了讓 daily 閘門「每天最多一次嘗試」天然防連環重試(create-typo + remote-broke 兩種 storm),`CodeRepoIngestor.sync` 改成**每次嘗試都 stamp `git_last_pulled_at`**(成功才更新 `git_last_sha`)。比 grill 的「None 不掃」更乾淨:None 過了時刻就掃一次,失敗也 stamp → 不再每 tick 重試。
- **phase 放 `CodeWikiBuildRun`(P2)**:cloning/ingesting 放在 `CodeWikiBuildRun.phase`(非 `WikiBuildState`),因 code collection 的 `status()` 讀 run;`start()` 加 `phase` 參數 + 新 `set_phase()`,`on_phase` callback 由 sync 觸發。
- **`enqueue_code_sync` 同步(P3)**:純 specstar enqueue 無 await,設成 sync method 讓 sweeper 的 tick thread 直接呼叫;`CodeRepoSweeper` 退化成純 producer(注入 `enqueue` callback、不再持有 `code_repo`),tick 回傳 enqueued cids。
- **P6 沒延後**:使用者要求一次做完 P1–P6,故 P6 在本 PR 一併完成(原 plan 標 v2)。git 連線編輯走既有 specstar-native PATCH,後端零改動(只加一個 round-trip 測試守約)。
- **git_token v1 明文**:specstar-native GET/PATCH 會序列化 `git_token`(只有自訂的 `CollectionOut` 省略它);沿用 #281 P3.0「v1 明文儲存」既定,本 issue 不改。

**排序理由**:後端先行(P1–P3),FE 才有可呼叫的非同步契約;P1（排程閘門）與 P2/P3（job 化）相對獨立,但 P3 依賴 P2 的 job 存在。P4（建立)先於 P5（首次 sync 要先建得出 code collection)。P6 是 v1 不做的編輯路徑,獨立成 phase。

每階段完成定義:走 `/tdd`（red→green→refactor),改動行為的 targeted 測試 + `ruff`/`ty` 邊做邊跑;commit;本表打勾。**權威全量 100% gate 在最後一次跑**(見 [[feedback_targeted_tests_then_full]])。

---

## 1 · 鎖定決策（/grill-me Q1–Q11）

### 範圍(Q1)
完整閉環:**建立表單 + 建立後自動首次 sync + collection 頁 Sync now 按鈕 + 同步狀態顯示**。
「只加建立表單」被否決——建完 code collection 後 UI 上沒有任何方法觸發首次 sync,wiki 會一直空著,等於沒解決原始痛點。編輯既有 git 連線延後(P6)。

### 每日 03:00 自動同步(Q2, Q7)
- **不開 UI**:全部 code collection 一律每天同一時刻同步,使用者不能逐 collection 調。
- 現有 sweeper 是「**間隔型**」(距上次 `git_last_pulled_at` 滿 `sync_interval_hours`),**沒有「在某時刻觸發」的概念**;`sync_interval_hours=24` ≠「每天 03:00」(錨在上次同步、會飄)。所以**勢必動後端**(這條不是純 FE)。
- 新 config **`kb.git.daily_sync: "03:00"`**(字串 `HH:MM`,伺服器當地時間;支援分鐘比純整數小時有彈性;**null/空 → 關閉每日自動,只剩手動 Sync now**)。
- `CodeRepoSweeper.tick` 閘門改成:**「過了今天 `daily_sync` 時刻 且 上次成功同步在今天 `daily_sync` 之前」才 due**。從未同步(`git_last_pulled_at=None`)**等下一個 `daily_sync` 時刻**、不是「隨時 due」→ 避免「建立時 token 打錯 → sweeper 每 tick 狂重試失敗 clone」。建立當下那次 auto-sync 仍立即 enqueue(P5)。
- daily 時刻**全面取代**舊 `sync_interval_hours` 間隔邏輯;`sync_interval_hours` 留作**死欄位**(不刪不讀,免 migration)。

### 建立 UI(Q3, Q5, Q11)
`NewCollectionModal` 最上面加分段切換 **Documents | Code repository**:
- *Documents* → 維持現狀(上傳型)。
- *Code repository* → `Git URL`(必填)+ 收合的 **Advanced**(`branch`、`access token`);自動 `use_rag=true` + `use_wiki=true`,檢索 toggle 隱藏;名稱必填、**從 git_url 末段帶建議名**(`.../ai-workspace.git` → `ai-workspace`,可改);描述選填。
- **URL 驗證**:前端只收 `http(s)://`,**擋 `file://` / `ssh://`**(Q5;多租戶安全立場;dogfood 改用 GitHub URL)。
- **Token**:可選密碼遮蔽輸入,建立時送出、後端**永不回傳**;`kb.git.default_token` 仍是 fallback。
- **建立鈕啟用(code 模式)**:名稱非空 + git_url 非空且 `http(s)://`。

### Sync 非同步 job 化(Q4=B, Q9)
clone+ingest 改成 **`wiki` JobType 的新 op `code_sync`**(援引 #281 follow-up Q1「沿用 wiki JobType、不過早拆 pod/HPA 到有真 hosted contention」):
- `/sync` route 與 `CodeRepoSweeper.tick` 都改成 enqueue **`WikiMaintenanceJob(op="code_sync", partition_key=cid)`** 後**立刻返回**;handler 做 clone+ingest → **鏈到既有 `code_split`** 開始 build。
- **`partition_key=cid` 天然序列化**「同一 collection 的 clone → build」,不會 clone 到一半又有人觸發 build 打架。
- clone+ingest 從 API **移到 wiki worker**(受 `run_consumers` gate),符合 #312「重活在 worker、API 當 producer」;sweeper 退化成**純 producer**留在 API(它本就是 #312 不 gate 的常駐 sweeper)。
- **三段釐清**(grill 過程更正了「sync=index job」的誤解):①clone+ingest 本體現況**不是 job**,行內跑在 API;②ingest 中的逐檔索引現況走 #281 A0 的**同步繞過 IndexCoordinator**;③ingest 後的 wiki build 才是 job、且在 **`wiki`** 不是 `index`。本 issue 把①也收進 `wiki` JobType,但**不動 IndexCoordinator/A0**(blast radius 最小:job handler 內仍照 `code_repo.sync` 的 clone+同步 ingest,只是搬到 worker)。

### 狀態 / 進度回報(Q10)
非同步後同步 502 沒了,**併進既有 wiki 狀態機,不另開 surface**:
- `code_sync` job 起:`CodeWikiBuildRun` `status="running"`、`phase="cloning"` → `"ingesting"`(可帶 `current=`檔名)→ clone/auth 失敗 `status="error"` + `last_error="<git 錯誤訊息>"` → 成功**鏈到 `code_split`**(phase 翻成建 cards / finalizing / done)。
- FE collection 頁 status strip **輪詢現有 `/wiki/status`**(已回 `building/total/done/current/phase/errors/last_error`):cloning → ingesting → building → done/error。
- 閒置顯示「Synced to `<sha>` · <相對時間> · **[Sync now]**」讀 collection 既有 `git_last_sha` / `git_last_pulled_at`;失敗顯示「Sync failed: `<last_error>` · **[Retry]**」。
- **零新 endpoint、零新 Collection 欄位**。

### `/sync` route 契約改變(Q11)
因非同步:從「跑完才回 `SyncOut(status="ok", git_last_sha=…)` + 失敗 502」改成「**enqueue 後立刻回 `status="queued"`(無 sha)**」;`400 no git_url` 同步驗證保留;clone/auth 失敗改走持久化 `last_error`。**既有 /sync 同步行為的測試需更新**(原本斷言 502 + 回 sha)。

### sweeper 簡化(Q11)
`tick()` 直接 enqueue `code_sync` job(job 自己鏈到 build),`lifecycle.py` 的 `code_sync_sweeper` loop **不再需要逐 cid 呼叫 `trigger_code_build`**。

---

## 2 · 否決 / 延後

- **Q4 選項 A(維持同步 `/sync`,FE 非阻塞 fire)**:被使用者翻案。理由:sync 需非同步;`tick()` 事實上就該 create 一個 job(producer/consumer 分離、耐超時、worker-pod 可擴展)。
- **自開獨立 `code_sync` JobType / coordinator / pod / HPA(Q9 選項 B)**:過早拆;違反 #281 follow-up Q1 既定的「沿用 wiki JobType 到有真 contention」。
- **掛 `index` JobType(Q9 選項 C)**:IndexCoordinator 單位是「索引單一已存在 SourceDoc」,塞「clone 整個 repo」job shape 不合;使用者明確否決。
- **間隔型 `sync_interval_hours=24`(Q2 選項 B)**:達不到「每天固定 03:00」(錨在上次同步、會飄)。
- **`file://` 表單支援(Q5 選項 A)**:多租戶安全立場;dogfood 改用 GitHub URL。
- **另開 sync 專屬狀態 surface(Q10 選項 B)**:FE 要輪詢兩條狀態線,沒必要;clone/ingest 只是 build 生命週期的前置 phase。
- **per-collection `daily_sync` UI 覆寫**:刻意不開(Q7)。
- **編輯既有 git 連線(換 branch / 輪換 token)**:延後 P6,v1 不做。

---

## 3 · 受影響檔案(預估)

**後端**
- `configs/config.example.yaml`:`kb.git.daily_sync` 說明。
- `src/workspace_app/config/…`(Settings):`kb.git.daily_sync` 欄位 + `"HH:MM"` 解析。
- `src/workspace_app/kb/code_repo.py`:`CodeRepoSweeper.tick` 每日時刻閘門(取代 interval)。
- `src/workspace_app/kb/wiki/coordinator.py`:`op="code_sync"` handler(clone+ingest → 鏈 `code_split`)+ enqueue 接縫。
- `src/workspace_app/kb/wiki/code_wiki_run.py`:`cloning`/`ingesting` phase + clone 失敗 `last_error`。
- `src/workspace_app/api/kb_routes.py`:`/sync` 改 enqueue + `status="queued"` 契約。
- `src/workspace_app/api/lifecycle.py`:`code_sync_sweeper` 移除逐 cid `trigger_code_build`。

**前端**
- `web/src/pages/kb/NewCollectionModal.tsx`:分段切換 + code 模式欄位 + 驗證。
- `web/src/api/kb.ts`:`createCollection` 帶 `git_url/git_branch/git_token`;`CollectionOptions` 型別。
- `web/src/pages/kb/KbCollectionPage.tsx`:sync status strip（複用 #162 strip + `/wiki/status` 輪詢)+ Sync now / Retry。
- 對應 `*.test.tsx`(vitest)。

---

## 4 · 測試(/tdd)

- **後端 pytest**:`daily_sync` 的 `"HH:MM"` 解析(含 null/空 → 關閉)+ tick 每日閘門(已過/未過時刻、None 等下一時刻不每 tick 重試、上次同步在今天時刻之前/之後);`code_sync` job handler(clone→ingest→鏈 `code_split`、clone 失敗記 `last_error` 不 crash partition);`/sync` 改 enqueue 後回 `status="queued"`(更新舊測試);`partition_key=cid` 序列化。
- **前端 vitest**:`NewCollectionModal` 分段切換 + code 模式必填/URL 驗證/建議名/Advanced;`createCollection` payload 帶 git 欄位;collection 頁 sync strip 各狀態(cloning/ingesting/building/done、synced-to-sha、Sync now、Retry on error)。
- **權威 gate**:最後一次跑全量 100% + whole-project `ruff`/`ty`/`format`(見 [[feedback_targeted_tests_then_full]]、[[feedback_ty_whole_project]])。

關聯:#281(parent)、#312(job runner ⊥ API)、#162(collection 頁 status strip)、[[feedback_llm_features_need_live_checks]]。
