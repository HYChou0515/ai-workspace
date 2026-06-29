# Plan — Issue #281: 讀程式碼的 AI 生成 wiki

> 繼 #50（LLM wiki）+ P3.0（code-QA：git clone → SourceDoc + code embedder）。
> 經一次 `/grill-me` session 鎖定決策樹後定案。被否決的替代方案就地記錄。
>
> **目標**：讓 LLM wiki 管線能「讀程式碼」生成有用的 wiki——通用功能（任何
> git code collection 都能生），平台讀自己的 repo 只是免費特例（生成後 KB chat
> 就能回答關於平台自身的問題）。

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | L0 檔卡片：`code_outline`（tree-sitter 抽 symbol/import 骨架）+ `CodeWikiBuilder._file_cards`（骨架 + LLM 一句白話）→ WikiPage `/files/<path>.md`；以 source hash 增量跳過 | ✅ |
| **P2** | L1 資料夾頁：沿目錄樹遞迴 roll-up（每資料夾餵子檔卡片 + 子資料夾摘要 → 寫頁）；**L0 有任何檔變動才整批重建上層**（單一 changed 閘門，比逐 dir hash 簡單且足夠）| ✅ |
| **P3** | L2 架構/索引/主題頁：餵全部資料夾摘要 → 寫 `/architecture.md` + `/index.md` + `/topics/<slug>.md`；摘要變才重合成 | ✅ |
| **P4** | 觸發接線：`on_doc_indexed` 對 code collection（有 `git_url`）改 enqueue 單一 coalesced `code_build` job（跳過逐 source fold）；handler dispatch；coordinator + `build_coordinators` + `create_app` 注入 wiki `ILlm`；`WikiBuildState` 粗進度 | ✅ |
| **P5** | DoD：真 LLM live check（本專案慣例 #51）+ docs/development.md + 全 gate（ruff/ty/format/coverage 100%）| ⬜ |

每階段完成定義：`uv run ruff check && ruff format --check && ty check` 全清、改動行
`coverage report` 100%、走 `/tdd`（red→green→refactor）、commit、本表打勾。

---

## 1 · 範圍 + 決策表（grill 鎖定）

| 問 | 答 | 為什麼 |
|---|---|---|
| 目標情境 | **通用 code-wiki 功能**；平台讀自己 = 免費特例 | 「能讀 code 生成」是能力陳述；自我文件化是 side-effect，不需獨立大階段 |
| 怎麼避免「大 project 一次看會漏」又「逐檔沒大局觀」 | **分層 bottom-up 摘要 + top-down 合成**；每層只讀下層摘要、不讀原始碼 | 全域一次塞不進 context 必漏；逐檔無大局。每層 context 有界 + 覆蓋率由「iterate 完整清單」強制 |
| build 引擎放哪 | **擴充 wiki coordinator**（背景 job）+ 自寫「沒改的檔跳過」增量；**不用 #100 workflow** | coordinator 已擁有觸發/queue/per-collection 序列化/多 pod/durable state；workflow 綁 app/profile/item + 自動觸發不合 + 雙 infra。只借它的 input-hash 增量點子 |
| L0 檔卡片怎麼做 | **tree-sitter outline + AI 一句白話**（可批次、沒改跳過） | 純函式名 AI 看不懂還得翻原始碼→又爆又漏；多一句白話才換到「上層只讀卡片、永不爆/漏」的保證 |
| wiki 樣子 | **兩者都要**：資料夾頁（不漏）+ 架構/主題頁（好讀）+ 每檔一頁（`/files/`，從資料夾頁連） | 分層法的自然產出：L1 資料夾頁、L2 架構 + 主題頁；多出來的只是 L2 多幾頁 |
| 每頁產出方式 | **v1 = 乙**：給固定材料、AI 直接吐每頁、**程式存檔**（非 agent loop） | 結構已知（哪些頁要建是確定的）→ 可預測流水線、可平行、繞開 #50「narrate 而不 write_file」的雷。甲（精修 agent）留待之後，v1 不擋它 |
| 觸發/範圍 | 有填 `git_url` = 走 code-wiki；同步整批 index 完後自動生；**repo 內所有檔都讀**（含測試） | 沿用 P3.0 既有判斷；測試碼常說明預期行為，是好材料 |
| 讀者端（回答問題） | **沿用現有 wiki reader**，引用回程式碼檔，基本不動 | code wiki 頁就在同一 WikiFileStore；reader 照常 navigate + cite SourceDoc |

### 否決

- **#100 workflow 跑 build**：觸發/歸屬不合 + 雙 orchestration + human_gate 死重 + 持久化重複。
- **whole-repo 單次 plan→fill**：大 project context 必漏。
- **逐檔/逐 package 增量 fold**：無跨檔大局觀；大 repo = 數百序列 LLM run。
- **L0 純機器 outline（不花 LLM）**：上層遇到看不懂的檔仍得翻原始碼 → 大 package 又爆又漏。
- **排除 tests/vendor/generated**：使用者要全讀（測試說明行為）。v1 不做路徑排除。

---

## 2 · 架構

```
on_doc_indexed(doc_id)                     [既有 hook，P4 改]
   │  collection.git_url 有設？
   ├── 無 → 既有逐 source fold（散文 wiki，不動）
   └── 有 → enqueue 單一 coalesced WikiMaintenanceJob(op="code_build", partition_key=cid)
              │  （已有 active code_build job 則跳過 enqueue = coalesce）
              ▼
         WikiMaintenanceCoordinator._handle → op=="code_build"
              ▼
         CodeWikiBuilder.build(cid)          [新 kb/wiki/code_wiki.py]
              │
   L0 ── 每個 SourceDoc：outline(tree-sitter) + ILlm 一句白話
         → WikiPage /files/<path>.md（含 <!-- src: file_id --> 增量標記，吻合則跳過）
              │
   L1 ── 沿目錄樹遞迴：每資料夾餵子檔卡片摘要 + 子資料夾摘要 → ILlm 寫頁
         → WikiPage /dirs/<dir>.md（子摘要 hash 未變則跳過）
              │
   L2 ── 餵全部資料夾摘要 → ILlm 寫 /architecture.md + /index.md + /topics/<slug>.md
              ▼
         WikiBuildState：phase（reading-files / writing-dirs / writing-architecture）
                         + current（當前檔/資料夾）；total=0（v1 不做假 N/M 進度條）
```

**重用（不動）**：`WikiPage` / `WikiFileStore` / `WikiBuildState` / `WikiMaintenanceJob`
/ wiki reader / `search_wiki` / `read_source` / P3.0 git sync。

**新增**：
- `kb/wiki/code_outline.py` — `outline(path, text) -> str`：用 `tree_sitter_languages.get_parser(lang)` 走樹抽頂層 def/class/import 骨架（py/ts/tsx/js/jsx 起步集）；非 code 或 parse 失敗 → `""`（graceful）。
- `kb/wiki/code_wiki.py` — `CodeWikiBuilder(spec, llm: ILlm, *, wiki_store, concurrency=…)`，`build(cid, *, on_phase=None)`，內部 `_file_cards` / `_dir_pages` / `_arch_pages`，各層 `asyncio.gather` + semaphore fan-out（per-collection 仍序列）。
- `kb/prompts/code_card.md`、`code_dir.md`、`code_arch.md` — L0/L1/L2 的 prompt。
- `WikiJobPayload.op` 多一個 `"code_build"`；coordinator `_handle` dispatch + `on_doc_indexed` 路由 + coalesce。
- `build_coordinators` / `create_app`：從 `get_wiki_endpoint(settings)` 建 `LitellmLlm` 注入 coordinator（`code_wiki_llm: ILlm | None`，null → code_build 記錯不 crash）。

**增量機制（借 input-hash）**：
- L0：卡片首行寫 `<!-- src: {SourceDoc.content.file_id} -->`；該檔卡片存在且 file_id 吻合 → 跳過。
- L1：資料夾頁寫 `<!-- inputs: {hash(子卡片 file_id 排序串)} -->`；吻合 → 跳過。
- L2：`/architecture.md` 寫 `<!-- inputs: {hash(全部資料夾摘要)} -->`；吻合 → 跳過。

---

## 3 · 各 Phase 細節

### P1 — L0 檔卡片
- `code_outline.outline(path, text)`：副檔名 → tree-sitter 語言；parse → 走頂層節點收 class/function/import 名 + 起始行；組成 markdown 骨架。不支援語言/parse 例外 → `""`。
- `CodeWikiBuilder._file_cards(cid, sources)`：對每個 SourceDoc，比對既有 `/files/<path>.md` 的 `src:` 標記；變了才呼叫 `llm.collect(code_card prompt)` 取一句白話，組卡片（標記 + 骨架 + 白話 + 原始碼前 N 行可選）→ `wiki_store.write`。
- 測試：outline（py/ts/fallback/parse-fail）；卡片生成（fake ILlm）；增量跳過（file_id 吻合不呼叫 LLM）。

### P2 — L1 資料夾頁
- 由 source 路徑建目錄樹；後序遍歷：每個資料夾餵「直接子檔的白話 + 直接子資料夾頁摘要」→ `llm.collect(code_dir prompt)` → 寫 `/dirs/<dir>.md`。
- 增量：子輸入 hash 標記吻合則跳過（連帶其祖先若無其他變動也跳過）。
- 測試：樹建構、遞迴 roll-up、跳過未變資料夾、巢狀深樹。

### P3 — L2 架構/索引/主題頁
- 餵全部資料夾摘要 → 一次 `llm.collect(code_arch prompt)` 產出：`/architecture.md`（分層/資料流）、`/index.md`（頂層導覽，連到資料夾頁與主題頁）、主題清單 + 各主題頁內容 → 寫 `/topics/<slug>.md`。
- 解析容忍（mirror quality `_extract_json_object`）。
- 測試：合成、主題頁切分、增量跳過、空 collection。

### P4 — 觸發接線
- `WikiJobPayload.op` 加 `"code_build"`；`on_doc_indexed`：collection 有 `git_url` → coalesce-enqueue 一個 code_build job 並 return（不做逐 source total bump / fold）。
- `_handle`：`op=="code_build"` → `CodeWikiBuilder.build`；wiki llm None → 記 `WikiBuildState.last_error`（仿 #57 skip），不 crash partition。
- `WikiMaintenanceCoordinator.__init__` 收 `code_wiki_llm: ILlm | None`；`build_coordinators` + `create_app`/worker 從 `get_wiki_endpoint` 建並傳入。
- `WikiBuildState`：code build 期間寫 `phase` + `current`，`total=0`（v1 不做數字進度條——延後）。
- 測試：路由（有/無 git_url）、coalesce、handler dispatch、llm-None graceful、reader 端不受影響。

### P5 — DoD
- 真 LLM live check（Ollama / 設定的 wiki 模型）：餵一個小 repo（fixture）跑一次 build，確認生出 `/files`、`/dirs`、`/architecture.md` 且非空。
- `docs/development.md` 補一節；本 plan 進度表打勾。
- 全 gate：`uv run coverage run -m pytest && uv run coverage combine && uv run coverage report --fail-under=100`、ruff、format、`ty check`（全專案，含 tests/）。

---

## 4 · 延後（不在 v1）
- 甲：精修 agent 第二趟（挑重要/不清楚頁回頭翻原始碼寫深）。v1 不擋。
- 數字 N/M 進度條（v1 只給 phase + current）。
- 更多語言（go/rust/java…）outline。
- 跨檔依賴圖視覺化 / LSP 級 references。
