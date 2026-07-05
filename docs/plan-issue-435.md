# Plan — #435 非冪等 capability 的去重框架

> 狀態:P1–P8 全數以 `/tdd` 完成(見文末 phase 表);flat integer phases。P1–P6 是初版;
> P7/P8 是 follow-up——原本 defer 給 #429「per-invocation journal 邊界」的兩項(`create_new`
> 跨 run、`send_notification` per-window),在 #429 落地後才發現它**並未**提供那條邊界(journal
> 仍只 workflow_id-scoped),所以 P7/P8 自己把 per-invocation identity(`run_id` + 建立時刻)
> 建到 handle 上,補齊這兩個缺口。
> 背景:現有的 workflow capability 分兩種脾氣。`ingest_to_collection` / `upsert_context_card`
> 是**冪等**的——action 是 upsert,重跑覆蓋掉就好,`run_step` 的「同 args 跳過」已足夠。
> 但 `create_entity`(#419 的實體)/ `send_notification` 的 action **本質一次性**:再做一次
> 就是「多一筆實體」「多一則通知」。一個 revise 改了欄位、或跨 run 手動重跑,args 會變,但那個
> 「真實世界的東西」還是同一個——同 args 去重接不住,會雙建。#435 補上這層去重。

## 核心概念

- **非冪等外殼(framework-locked shell)**:`workflow/nonidempotent.py`。把一次 capability 呼叫
  拆成**兩筆各自 journal 的 `run_step`**——不是另造一套「兩段落盤」機制。
  - `step_<name>/<key>.decide.json` ← 一個 `Verdict(kind = new | duplicate | token)`。
  - `step_<name>/<key>.json` ← 發布出去的 `Result(fields, artifact)`。
  - `decide` 先判斷「這東西存不存在」,`act` 再依 verdict 分派(建新／合併／跳過)。因為 act 的
    input hash **把 decide 的 verdict 也算進去**,§9 的 hash-chaining 白送**三態重跑**
    (verdict 沒變 → 跳過;verdict 變了 → 重判)。不需要另寫兩段落盤的持久化。
- **去重是機制目錄,不是「策略層」**。policy 定義在**單一 capability 介面**這層(不是通用策略層)——
  因為 `create_entity` 自己就橫跨 M1+M2,證明機制粒度是**政策**不是 capability。
- **name 是去重身分**(不是 args 指紋)。`create_entity` / `send_notification` 的 `name` 必填;
  同一個 `name` 站點在 revise／replay 時對應到同一個實體——這就是修掉「revise 雙建」的關鍵。

## 機制目錄(决议7,取代「策略層」)

| 機制 | 做什麼 | 用在哪 |
|---|---|---|
| **M1 — 查既有真實來源(store)** | 兩種味道:*deterministic fingerprint*(查一個確定的鍵)或 *AI-semantic*(問模型是不是同一個真實東西) | `send_notification` 用 `{recipient}:{topic}` 查通知 ledger;`create_entity` 用 AI 問跨來源是否同一實體 |
| **M2 — idempotency token** | 呼叫綁一個**每次 invocation 的 token**(該 run 的 `run_id`);`create_new` = 「M1 減掉跨來源比對」+ token fold 進 hash + per-run `created.json` | `create_entity` 的 `on_duplicate="create_new"`(P7 起上線:每次獨立 invocation 各開新號,同 run 內 revise 重用) |
| **M3 — self-ledger(deferred)** | 給真正 blind 的外部 channel(送出去無法回查)自建 ledger | 目前無;in-app 通知不需要(見下) |

**為什麼 in-app 通知不需要 M3**:那筆 Notification store record **本身**就同時是「送出」與
「ledger」(原子),沒有「送出了但還沒記錄」的 act-crash 窗——不像盲目外部 channel。

## 鎖定決策(grill-me)

| # | 主題 | 定案 |
|---|------|------|
| 决議1 | 別造兩段落盤 | decide / act 表達成**兩筆各自走 `run_step` 的記錄**;verdict = decide 的 result;失效靠 §9 hash-chaining。 |
| 决議2 | 身分來源 | **journal-first + AI-second**。先看 write-once `created.json`(deterministic 自我去重,擋 revise 雙建);只有跨來源才動用 AI。 |
| 决議3 | self / cross 合併分流 | patch = 作者宣告的欄位(無 per-field provenance)。**self-merge = overlay;cross-merge = 圍欄覆寫**(非破壞)。 |
| 决議4 | `on_duplicate` 政策 | `update` / `skip` / `create_new`,定義在**單一 capability 層**。`create_new` = update 減跨來源比對,綁調用 token;**不收 `link`**;#429 前靜態擋。 |
| 决議5 | cross-merge 冪等 | 圍欄區塊 `<!-- wf:<name> begin/end -->` **每次覆寫**(不是 append)→ 重跑不累積,**靠構造冪等**不靠 ledger。marker 本身 = 機器 vs 人手的分界(無 schema 改動)。 |
| 决議6→7 | 「策略層」溶解 | SendOnce 不是獨立策略,是 SemanticDedup 的退化情形(deterministic fingerprint)。策略層溶進機制目錄;但**三態落盤外殼不能一起溶掉**(它是共用地基)。 |
| 决議8 | decide-AI 可逆性 | M1-AI **從構造上只守可逆的 act**(非破壞 enrich),所以 fail-open 永遠安全:模型出錯／幻覺 → 當 NEW,頂多多建一筆(可逆),**絕不誤 merge 進不存在的紀錄**。 |

**Node5 補充**:(1) P3 依賴的「entity schema 需先劃圍欄 vs 人手寫區」被溶解——marker **就是**
demarcation,不需 schema 改動;(2) `create_new` 部分交付:#429(per-invocation journal 邊界)
落地前,`workflow check` 加一道靜態門把它擋在啟動前。

## ~~Blocking:#429~~ → 已由 P7/P8 自建(#429 並未提供邊界)

原本判斷 `create_new` 的跨 run 版本、以及 `send_notification` 的「時間窗內只發一次」都需要
**per-invocation 的 journal 邊界**,並以為 #429 會提供。**#429 落地後實際並未**——journal 仍只以
`workflow_id` 為 key(手動 re-Run／re-trigger 重用同一 journal),且 driver 的 `started` 每次
drive 都被覆寫、不是 resume-stable。所以 P7/P8 直接把 per-invocation identity 建到 handle:
`run_id`(WorkflowRun resource_id,resume-stable、每次 firing 相異)+ `run_started_at`(specstar
`created_time`,resume-stable)。`create_new` 把 `run_id` fold 進 dedup key(每次 invocation 各開
新號);`send_notification` 把 `run_started_at` bucket 進 window fingerprint。P4 的 `workflow check`
靜態門在 P7 退休。

## Phase 拆解(全數完成)

| Phase | 內容 | 依賴 |
|---|---|---|
| **P1** | framework-locked 三態外殼 `nonidempotent.py`(`Verdict` / `Result` / `run_nonidempotent`,兩筆 `run_step`) | — |
| **P2** | `create_entity` 改用 `name` 去重身分(取代 args-digest);journal-first 自我去重(`created.json`);DSL surface(`name` 必填、`on_duplicate`、outputs schema) | P1 |
| **P3** | cross-origin M1-AI:`entity_dedup.py`(`match_prompt` / `parse_match` fail-open;圍欄覆寫 `replace_fenced_block`);`EntityStore.update(body=)` | P2 |
| **P4** | `on_duplicate` 三值 + `create_new` 的 #429 靜態門(`workflow check`)〔門於 P7 退休〕 | P2 |
| **P5** | `send_notification`(M1 deterministic fingerprint `{recipient}:{topic}`);`Notification.dedup_key` indexed + `notification_sent` 查詢;driver 接線 | P1 |
| **P6** | driver wire `ask_llm`(create_entity 跨來源 match 用 run 的模型,`asyncio.to_thread(collect)`;無模型 → 純 journal 自我去重);live canned integration check(decide-AI,`@pytest.mark.integration`);本文件 + `workflows-authoring.md` | P1–P5 |
| **P7** | create_new 跨 invocation(M2 token):handle 曝 `run_id` + `run_started_at`(orchestrator `_build_handle` 從 `created_time` 灌入);`create_new` fold `run_id` 進 per-run `created.json` + shell input hash,`decide` 產 `token` verdict;lift P4 靜態門(`create_new` 進 `_CAP_ON_DUPLICATE`) | P4, #429 merged |
| **P8** | send_notification per-window:抽 `triggers.window_key`(`fire_window` 委派);`send_notification(window=)` bucket `run_started_at` 進 fingerprint + fold `run_id` 讓 decide 每 invocation 重查 ledger;DSL `CapabilityStep.window` 欄位 + 驗證 | P5, P7 |

## 落點

- `src/workspace_app/workflow/nonidempotent.py` — 三態外殼(P1)。
- `src/workspace_app/workflow/entity_dedup.py` — cross-origin M1-AI 純函式(P3)。
- `src/workspace_app/workflow/handle.py` — `create_entity` / `send_notification` capability;`run_id` + `run_started_at` per-invocation identity(P7)。
- `src/workspace_app/workflow/dsl.py` — DSL surface + 靜態驗證(`name` 必填、`on_duplicate` 含 `create_new`、`window` 政策)。
- `src/workspace_app/workflow/triggers.py` — bare `window_key(every, now)`(P8,`fire_window` 委派)。
- `src/workspace_app/workflow/orchestrator.py` — `_build_handle` 灌 `run_id` + `run_started_at`(P7)。
- `src/workspace_app/api/workflow_exec.py` — driver 接線(notify ledger + `ask_llm`)。
- `src/workspace_app/resources/notification.py` — `dedup_key` 欄位。
- `tests/workflow/test_create_entity_dedup_live.py` — decide-AI live canned check。

延伸見〈[撰寫 Workflow](workflows-authoring.md)〉的「非冪等 capability 去重」小節。
