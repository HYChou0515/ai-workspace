# Plan — Issue #281 follow-up：code-wiki 修缺口 + scale

> 繼 #281 本體（PR #336 已 merged，分層 code-wiki builder：L0 檔卡片 → L1 資料夾 →
> L2 架構/index/topics，見 [`plan-issue-281.md`](plan-issue-281.md)）。
> 本文處理 #281 merge 後**自我稽核**抓到的缺口，並把 build 從「單一 job 內序列跑完」
> 重構成 specstar job-queue fan-out。經第二次 `/grill-me`（Q1–Q5）鎖定，否決方案就地記錄。
>
> **核心動機**：merged 的 #281 在主要流程裡**根本不會 build**——一個已驗證的 critical bug
> （A0）。本 follow-up 先把它修到「真的會跑」，再驗 reader、dogfood、最後才做 scale。

---

## 0 · 進度總覽（flat phases）

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | **A0+A1 觸發接線**：統一 `trigger_code_build` 接縫，從 sync 端點 / sweeper（lifespan closure）/ 手動 rebuild route 觸發；A1 刪檔對 code collection 跳過散文 unfold。讓功能真的能端到端跑 | ✅ |
| **P2** | **Reader/QA 驗證（A2）**：測 + live-check KB reader 在 code wiki 上回答問題並引用回原始 SourceDoc | ✅ |
| **P3** | **真 repo dogfood（A3）**：拿本 repo 的 `kb/wiki` 套件配真 Ollama 走 fan-out 建一次（12 檔→6 批、44.7s、reader 正確回答+引用）| ✅ |
| **P4** | **job-queue fan-out（Q1+Q2）**：monolithic `code_build` → `code_split`/`code_card`/`code_finalize` op + `CodeWikiBuildRun` CAS join；L0 按「資料夾內 token-capped 批」fan-out（`partition_key=None`）| ✅ |
| **P5** | **L1/L2 增量（Q3）**：per-page input-hash，只重建受影響的 dir 鏈 + L2-if-changed（無變更 rebuild = 0 LLM call）| ✅ |
| **P6** | **FE（Q5，minimal）**：刪檔過期提示 + code-build phase 標籤；結構不動（已能 render）| ✅ |
| **P7** | **prompts + `_unfence`（B8）**：`_unfence` 容忍未關 fence；read_source 容忍 `/files/<src>.md` card 路徑（修 P2 引用斷鏈）| ✅ |
| **P8** | **權威全量 100% gate（C9）**：CI-mirror unit + 新碼 100% + whole-project ty/ruff/format；完整 integration gate 見下方註記 | ✅ |

### 實作偏離 / 註記（grill 鎖定外、實作時定的）

- **Q2 的「小檔併共用 collect()」子優化延後**：fan-out 的 unit（job）粒度照 Q2 做了「資料夾內 token-capped 批」（MULTIHOST 防 straggler 的關鍵已兌現），但**批內每檔仍各自一個 `collect()`**。把多檔併進單一 call 回傳 N 卡片，對小模型的多卡解析可靠性有風險，且 job 層批次已提供 MULTIHOST 均衡——故延後為後續優化。
- **use_wiki toggle-on 不做後端 event_handler**：collection 更新走 specstar auto-CRUD（無手寫 seam），且 sync/async event handler 糾結。改由 **FE 切換 on 時呼叫既有 rebuild endpoint**（P6），後端「toggle + 手動」收斂成同一條 rebuild 路徑。
- **card batch budget 是 coordinator 構造器旋鈕**（預設 24k chars），YAML operator 設定延後（預設適合本機、參數適合測試）。
- **P2 live 發現**：reader 的 agent loop 需要 **tool-calling 能力的模型 + `ollama_chat/` 前綴**（`ollama/` 走 /api/generate 無原生 tools）；小模型會把 wiki 頁路徑誤傳給 read_source → P7 讓 read_source 容忍 card 路徑。build 用 coder 模型摘要、reader 用通用模型，分工。
- **P3 dogfood 發現**：`qwen2.5-coder` 偶會**拒答**某檔摘要（graceful：tree-sitter skeleton 仍在、空摘要不汙染上層）；屬模型品質限制，不加 refusal 偵測（過擬合）。
- **逐層 L1 fan-out 延後**（Q3）：首建的 dir roll-up 仍序列；多輪 DAG 排程對一次性成本不成比例。

**排序理由**：A0 不修，功能走主流程根本不跑，所以 P1 最先。fan-out（P4）是 scale 優化、不改 wiki 內容語意，所以放在「先讓它跑（P1）+ 驗 reader（P2）+ dogfood 看真實況（P3）」之後——dogfood 也才能告訴我們 scale 到底痛不痛。

每階段完成定義：走 `/tdd`（red→green→refactor），改動行為的 targeted 測試 + `ruff`/`ty` 邊做邊跑；commit；本表打勾。**權威全量 100% gate 在 P8 一次跑**（[[feedback_targeted_tests_then_full]]）。

---

## 1 · 缺口表（自我稽核；merged #281 沒有任何產物記錄）

| 編號 | 嚴重度 | 缺口 | 關鍵 file:line | 收在 |
|---|---|---|---|---|
| **A0** | **critical（已驗證）** | git sync **永不觸發** code-wiki build。`sync_collection` → `code_repo.sync` → `ingestor.ingest()` 是同步 store+index，**繞過 IndexCoordinator**，`on_doc_indexed` 不觸發 | `kb_routes.py:743` → `ingest.py:184-192`；`on_doc_indexed` 唯二呼叫端 `index_coordinator.py:559`（API 上傳）、`kb_routes.py:881`（rebuild route）| P1 |
| **A1** | will-bite | 刪檔走錯路徑。`on_doc_deleted` 只 gate `use_wiki`、不看 `git_url`，無條件建散文 `unfold` job → 對 code wiki 跑散文 unfolder = 垃圾 | `coordinator.py:291-330`（gate 在 309，建 job 在 318）| P1 |
| **A6** | minor | 上傳路徑可能 build 到一半（per-doc coalesced 觸發 → 最終一致但可能跑 2–3 次）| `coordinator.py:260-289` | P4（coalesce 收斂）|
| **A2** | validation | reader/QA 路徑**從未測**。#281+#230 的重點就是「AI 回答 code 問題」，但只驗了生成、沒驗讀取+引用 | — | P2 |
| **A3** | validation | **從未在真 repo dogfood**。live check 用 4 檔玩具 repo | — | P3 |
| **B7** | polish | FE 沒驗。code wiki 新增 `/files//dirs//topics//architecture.md` 頁結構，`WikiBrowser` 沒對照過 | `web/src/pages/kb/WikiBrowser.tsx` | P6 |
| **B8** | polish | prompt 未迭代；`_unfence` 只剝單一整段 output fence | `code_wiki.py` | P7 |
| **C9** | process | 權威全量 100% gate **從未跑**（只跑過 CI-mirror unit 子集，非 100%-gated）| — | P8 |

> **A0 silver lining**：`code_repo.sync` 同步跑完才回傳（所有 doc 都 index 完），所以 sync 後觸發**一次** build **不需要 async batch-join**——直接在 sync 回傳後敲 trigger 即可。

---

## 2 · 決策表（第二次 grill 鎖定，Q1–Q5）

| 問 | 答 | 為什麼 |
|---|---|---|
| **Q1** build 用哪種 JobType | **沿用現有 `wiki`（WikiMaintenanceJob）**，加 `code_split`/`code_card`/`code_finalize` op + 新 `CodeWikiBuildRun` CAS join。JobType 隔離延後 | fan-out 跟 JobType 數量正交——`index`（#227）就是「一型多 op」。開新 JobType 唯一好處是 HPA/pool 隔離，只在 hosted 多 consumer 並行時兌現；本機 Ollama 序列下無意義（YAGNI）。之後真撞隊頭阻塞再拆是局部動作（op+Run 已就位）|
| **Q2** L0 fan-out 粒度 | **unit = 單一資料夾內、token-capped 的一批檔**；card unit `partition_key=None` 自由併發；split/finalize 保 `partition_key=cid`；token budget 可設定（兼並行粒度旋鈕）| 用 **MULTIHOST** 判斷：CAS join 讓 L0 phase 的 wall-clock = 最慢那條 unit 鏈。純「一資料夾一 job 不設上限」→ 肥 dir = straggler gate join；token cap 把 unit 壓平 → 消滅 straggler、近線性加速。dir coherence（同模組鄰檔互為脈絡）→ 卡片品質，path 排序 + dir 邊界 flush 近乎免費補回。每檔一 job 既爆 call 數又爆 CAS 列數，淘汰 |
| **Q3** L1/L2 增量 | **(a) per-page input-hash**（沿用 L0 hash-marker 機制往上延伸）；**首建 L1/L2 在 finalize 序列跑**；逐層 fan-out L1 延後 | MULTIHOST：全 rebuild 讓**未 fan-out 的 finalize** 變序列瓶頸（re-sync 改 3 檔卻跑 M+8 call），吃掉 L0 並行。per-page hash 讓 re-sync 的 finalize 縮到一把 call。L1 bottom-up 有層間依賴，fan-out 要逐層 round（specstar queue 不原生 DAG），對一次性首建成本不成比例 → 延後 |
| **Q4** 何時自動重建 + 刪檔 + 進度 | **4 種情況**自動觸發**統一一個** build（Sync / 自動 re-sync / use_wiki toggle-on / 手動 rebuild），coalesce。**刪檔不自動重建**——對 code collection 直接跳過散文 unfold（停掉垃圾），孤兒頁等下次重建清。**每次 finalize 都 reconcile** `/files` 卡片 vs 當前 SourceDoc（清孤兒 + 空 dir）。進度由 **`CodeWikiBuildRun` 驅動**（非 cid-keyed active-job count）| 刪一檔建一次浪費（使用者自挑時機重建）。Sync 是「整 repo 拉完建一次」不浪費。進度心法被 Q2 逼改：card unit `partition_key=None` → `_active_count(cid)` 的 cid filter 數不到 → 必須改讀 Run |
| **Q5** FE 動多少 | **minimal**：結構不動（WikiBrowser 把扁平路徑當樹 render，`/files//dirs//topics/` 等照原樣顯示、不會壞）；只加刪檔過期提示 + code-build phase 標籤；dogfood 親眼驗 render | FE 探查確認 `WikiBrowser`/`KbWikiIde` 無寫死假設、`[[wikilink]]`+`Sources:` 頁尾現成；不為假想問題預先做花俏導覽（YAGNI），dogfood 看到真需要再說 |

### 否決

- **開新 JobType `CodeWikiBuildJob`**（Q1）：唯一好處 = HPA 隔離，現階段本機 Ollama 序列下無意義；拆分是可延後的局部動作。
- **每檔一 job**（Q2）：1000 檔 = 1000 call + 1000 CAS 列，直接推翻「batch 小檔」目標。
- **token-batch 不分 dir**（Q2）：丟失 dir coherence（卡片品質）。
- **全 rebuild 上層**（Q3）：finalize 變序列瓶頸、吃掉 L0 並行。
- **刪檔自動全重建**（Q4）：浪費；刪多檔 = 重建多次。
- **逐層 fan-out L1**（Q3）：首建才痛、要多輪 DAG round，不成比例 → 留作後續整數 phase。
- **FE 加 code 專屬花俏導覽**（Q5）：YAGNI，等 dogfood 看到需要。

---

## 3 · 架構（fan-out 形狀，P4 後）

```
[5 個觸發點] ──► WikiMaintenanceCoordinator.trigger_code_build(cid, actor)
  Sync 端點 / sweeper(callback) / toggle-on / 手動 rebuild        │  coalesce：已有 active code_split/card/finalize 則跳過
  （刪檔 NOT 在此；刪檔只跳過散文 unfold）                          ▼
                                          enqueue WikiMaintenanceJob(op="code_split", partition_key=cid)
                                                                   ▼
   code_split  ── list 當前 SourceDoc → 按資料夾分組 → 每組 token-capped 打包成 N 批
                  → 建 N 個 op="code_card"（partition_key=None，自由併發）
                  → 建 CodeWikiBuildRun(id=cid, total=N, done=[], failed=[], finalized=False)
                                                                   ▼  （×N，跨 consumer 並行）
   code_card   ── 對該批做 per-file 卡片（小檔併共用 collect()，大檔自成 call）
                  → 寫 /files/<path>.md（嵌 content-hash marker，未變跳過）
                  → CodeWikiBuildRun CAS 記 done.append(idx)
                                                                   ▼  （done∪failed==total 時）
   code_finalize ─ CAS claim-once（claim_finalize，仿 IndexRun）
                  → L1 dir roll-up（bottom-up，per-page input-hash 跳過未變；首建全跑）
                  → L2 architecture / topics / index（input-hash 跳過）
                  → reconcile：刪 /files 孤兒卡片 + 清空 /dirs
                  → CodeWikiBuildRun.status = done

進度：WikiMaintenanceCoordinator.status(cid) 對 code collection 改讀 CodeWikiBuildRun
      （total / len(done) / finalized / phase），散文 fold 路徑維持原本 active-job count。
```

仿 `index` fan-out（[`index_jobs.py:30-51`](../src/workspace_app/kb/index_jobs.py) payload `kind` dispatch、[`index_coordinator.py:281-297`](../src/workspace_app/kb/index_coordinator.py) `_handle`、[`index_run.py:84-104`](../src/workspace_app/kb/index_run.py) `claim_finalize` / `_cas`）。`CodeWikiBuildRun` 仿 `IndexRun`（[`resources/kb.py:305-336`](../src/workspace_app/resources/kb.py)）。

---

## 4 · 各階段細節

### P1 — A0+A1 觸發接線（讓它真的跑）

目標：不動 build 內部（仍是 monolithic `code_build` op），只把「何時觸發」接對，讓功能端到端跑起來。

- 新 `WikiMaintenanceCoordinator.trigger_code_build(cid, actor)`：enqueue 一個 build job、coalesce（沿用 `_has_active_code_build` `coordinator.py:284`）。P1 內仍 enqueue 既有 `code_build`；P4 換成 `code_split`（接縫不變）。
- 五個觸發點接上：
  - **sync 端點** `kb_routes.py:sync_collection`（~743）：`await asyncio.to_thread(code_repo.sync,…)` 回傳後 `await wiki_coordinator.trigger_code_build(cid, actor)`。
  - **sweeper** `api/lifecycle.py:83`（`code_sync_sweeper`）：給 `CodeRepoSweeper`/`CodeRepoIngestor` 注入 `on_synced(cid, actor)` callback；`code_repo.py` **不 import wiki**（保「只 clone+ingest」職責）。`lifecycle`/`coordinators` 接成 `trigger_code_build`。
  - **use_wiki toggle-on**：collection PATCH 路由偵測 `use_wiki` 對 git_url collection 翻 `False→True` → 觸發一次。
  - **手動 rebuild route** `POST /kb/collections/{id}/wiki/rebuild`（`kb_routes.py:865`）：對 code collection 改成**呼叫一次** `trigger_code_build`，**不** loop `on_doc_indexed`（`kb_routes.py:876-883`）。
- **A1**：`on_doc_deleted`（`coordinator.py:291`）對 `git_url` collection **跳過散文 unfold**（直接 return）。孤兒頁不在此清——等重建時 finalize reconcile（P4）。
- **測試紀律**：必須**走真正的 sync→trigger 接縫**（前任 bug 正因測試直接呼叫 `on_doc_indexed` / `build()` 繞過接縫才漏掉 A0）。

DoD：set git_url + Sync → wiki 真的被 build；刪 code 檔不再跑散文 unfolder。

### P2 — Reader/QA 驗證（A2）

- 測 KB reader（`search_wiki`/`read_source` 那組 wiki 維護/讀取 agent）在 code wiki 上回答問題，且引用回原始 SourceDoc。
- **live check**（[[feedback_llm_features_need_live_checks]]）：真 Ollama，問一個關於 code 的問題，確認答案有引用、引用指對檔。

### P3 — 真 repo dogfood（A3）

- 拿本 repo（或可控子集，如 `src/workspace_app/kb/wiki/`，因本機 Ollama 序列、首建慢）配真 Ollama 建一次。
- 看：卡片/資料夾/架構頁品質、時間、成本、**FE 實際 render 長相**。把問題餵回 P6/P7。

### P4 — job-queue fan-out（Q1+Q2）

- `WikiMaintenanceJob` payload 加 op：`code_split` / `code_card` / `code_finalize`（沿用今天的 op 欄位）。
- 新 `CodeWikiBuildRun`（`resources/kb.py`，仿 `IndexRun`；id=cid）：`total/done[]/failed[]/finalized/status/phase`。
- 新 CAS store（仿 `index_run.py` `claim_finalize`/`_cas`）。
- `code_split`：list 當前 SourceDoc → 按資料夾分組 → 每組依 token 預算貪婪打包（不跨 dir，path 排序天然聚 dir）→ 建 N 個 `code_card`（`partition_key=None`）+ `CodeWikiBuildRun(total=N)`。
- `code_card`：批內小檔併共用 `collect()`（**prompt/parse 要能把一次回應切回 N 個 per-file 卡片**）；大檔自成 call；寫 `/files`，CAS 記 done。
- `code_finalize`：`claim_finalize` 一次性 → L1 roll-up + L2 + **reconcile 清孤兒**。
- 進度：`status()` 對 code collection 改讀 `CodeWikiBuildRun`（Q4c）。
- token budget / 並行可設定（`kb.wiki.*` config）。
- **A6**：coalesce 讓上傳路徑也收斂成一次 build。

### P5 — L1/L2 增量（Q3）

- 每個 L1 dir 頁 / L2 頁嵌 input-hash marker（input = 直接子項摘要的 hash），沿用 L0 在 `/files/<path>.md` 的 hash-skip 機制往上延伸。
- finalize 只重建 input-hash 變了的 dir 鏈 + L2-if-top-level-變。首建全跑（input 全新）。
- 逐層 fan-out L1 **不在此** phase（延後）。

### P6 — FE（Q5，minimal）

- 結構不動（探查確認 `WikiBrowser`/`KbWikiIde` 照原樣 render code-wiki 路徑；`web/src/api/kb.ts` wiki 端點現成）。
- 加：刪檔後「wiki 可能過期，按重建更新」提示；確認重建進度條對 code build 的 phase（split/cards/finalize）顯示講得通。
- dogfood 親眼驗 render；真醜/真斷再補。
- **vitest TDD**（[[feedback_fe_tdd]]）。

### P7 — prompts + `_unfence`（B8）

- 迭代 prompt（尤其 P4 的批次「多檔 → N 卡片」格式，要可靠切回 N 卡片）。小模型 prompt 規則見 [[feedback_llm_features_need_live_checks]]。
- `_unfence` 改成逐頁 robust 去 fence（非只剝單一整段 output fence）。

### P8 — 權威全量 100% gate（C9）

- `uv run coverage run -m pytest && uv run coverage combine && uv run coverage report --fail-under=100`（完整 suite 含 integration）。
- whole-project `uv run ty check`（[[feedback_ty_whole_project]]）+ `ruff check` + `ruff format --check`。
- gate 不用 pipe-mask（[[feedback_gate_no_pipe_mask]]）。

---

## 5 · 環境 / 雷（沿用既有）

- `uv sync --all-extras`（process-sandbox extra，否則 `ty` 報 `pandera.pandas` + `test_infer_modules` fail）。
- CI = unit only `-m "not integration" -n auto`（非 100%-gated）；權威 gate = 全量本地 suite。
- specstar：struct 欄位 `dict[str, Any]`（非 object）；`assert isinstance(...)` narrow `resource.data`；每次測試 fresh `make_spec`（兩個 coordinator 共一 spec 會雙註冊 job model）。
- 規則：繁中回應；無 canvas / 無 5-Why（[[feedback_no_canvas_5why]]）；research-backed 決策在 code+PR 引文獻（[[feedback_cite_literature]]）；LLM 功能要 live check；每個 LLM call 必 stream（`ILlm` streaming-only，[[feedback_always_stream_llm]]）；不 push remote（[[feedback_no_push]]）。
- live-check 樣板：`/home/hychou/.claude/jobs/51167346/tmp/live_check_281.py`（job-scoped，可能已刪，照 pattern 重建）；本機 Ollama 有 `qwen2.5-coder:7b-instruct` / `qwen3:8b` / `qwen3:14b` + embed `bge-m3`。
