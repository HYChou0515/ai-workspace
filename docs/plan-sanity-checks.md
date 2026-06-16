# Plan: LLM sanity checks + replay diagnostics (#51)

> 起因(2026-06-06 實戰):insight extraction 對 qwen3:14b 無聲失敗了一整輪 —
> 所有 fake-LLM 測試全綠,真模型在任務上接龍/schema 漂移,沒有任何一層告訴
> 使用者或開發者「這顆 LLM 撐不起這個功能」。#51 要求:(1) 現有功能要有檢查
> 機制 (2) 架構要能手動新增檢查 (3) FE 要有 indicator (4) 內部 agent 也要涵蓋。
> 經 `/grill-me` 走完決策樹定案如下。

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | Check 框架 — `ISanityCheck` ABC + `CheckResult` + registry + factories 組裝 | ✅ ccc6043 |
| **P2** | 七個 bundled canned checks | ✅ b87745f |
| **P3** | Check API — `GET /health/checks`(cached)+ `POST /health/checks/run`;startup sync/async 兩段 | ✅ b82050c |
| **P4** | Replay API — context-snapshot rebuild + pure-LLM probe(doc / turn / tool-call) | ✅ 85b5e0c |
| **P5** | FE — 全域診斷頁 + 健康 indicator | ✅ 42dbd3d |
| **P6** | FE — doc 級 / turn 級 / tool-call 級 replay 入口 | ✅ adfe93e |

每階段完成定義:`ruff check && ruff format --check && ty check` 清、`pytest` 綠、commit、本表打勾。

---

## 1 · 鎖定的決策(grilling)

- **Q1 — 範圍**:七項全部。
  1. RCA workspace agent(tool-calling probe)
  2. KB chat agent(`kb_search` tool-calling probe)
  3. `infer_modules` sub-agent(分類 probe)
  4. Retrieval 增強(multi-query expand probe;HyDE/rerank 同 LLM 同形狀,v1 以 expand 代表)
  5. Insight extraction(迷你對話 → ≥1 個合法 kind 的 insight)
  6. VLM describer(已知內容的測試圖 → 描述含關鍵詞)
  7. Embedders(default + code:embed 一句 → dim 正確)
- **Q2 — 時機**:啟動時 **sync 跑 <1 分鐘的項目**(connectivity 類:Ollama/endpoint 可達、
  模型存在、embedder dim probe),接著 **async 跑全項 capability probes**;結果 cache +
  時間戳。FE 可手動重跑(整輪或單項)。**無定時重跑**(中途掛靠使用現場 error +
  手動重驗)。
- **Q3 — Replay 入口**:四個全要 — 全域診斷頁、doc 級(KB 文件)、turn 級(agent
  對話)、tool-call 級(單次 tool 使用)。
- **Q4 — Replay 本質**:**只測 LLM,不執行 tool、零副作用**。Replay =
  `(context snapshot, target LLM) → raw output`。要看 LLM 對 tool output 的反應,
  用 history 裡既有的 tool output 組 context 餵它 — 永遠不真的 call tool、不寫
  任何狀態。原始輸出(含 reasoning)原樣呈現給人眼比對。
- **Q6 — Gating**:check fail **只顯示警告不鎖功能**(check 可能過時/誤判)。
- **UI/UX**:授權照業界慣例自定(系統健康 indicator:header 狀態點 + 診斷頁;
  warning 不擋操作;文案不露內部技術細節 — per memory `ui-copy-no-internals`)。

## 2 · 推定項(照 codebase 慣例,未另行確認)

- `ISanityCheck` ABC(`I<Name>`、介面/實作分檔,per memory `abc-over-protocol`),
  `kb/` 之外開新 `health/` 模組(七項橫跨 RCA/KB/VLM,不屬於 kb 子系統)。
- `CheckResult = {check_id, status: pass|fail|skip|error, detail, latency_ms, checked_at}`;
  `skip` = 功能未配置(`vlm_llm: null` → VLM check skip 不是 fail)。
- Bundled 七項在 factory 寫死;custom 走 config dotted path(`health.checks: [...]`,
  同 `kb.parsers` 模式)+ `health.checks_disabled`。
- 結果存 specstar resource(`CheckRun` — 歷史可查,per memory
  `specstar-CRUD-routes-fine`),最新一輪另有 in-memory cache 給 GET 用。
- API 回 pydantic models(per memory `pydantic-response-models`)。
- Canned probe 的斷言是**功能級**不是連線級(qwen3:14b 事件的核心教訓:HTTP 200
  不代表能用)。

## 3 · Replay 的 context snapshot 形狀

| 入口 | context 來源 | 重建方式 |
|---|---|---|
| Doc 級(extraction / parser) | SourceDoc blob + extraction prompt | 與 Ingestor 同碼路,dry-run |
| Turn 級 | Conversation.messages[:n] | 與 runner 同 prompt 組裝,只取第一個 LLM 輸出(text 或 tool-call intent),不進 tool loop |
| Tool-call 級 | messages 截至該 tool call 前 | 同上;呈現「當時 call 了 X(args)/ 現在想 call …」對照 |
| VLM | SourceDoc 原圖 + describe prompt | VlmDescriber 同碼路,回 raw markdown |

素材皆在現有資料(Conversation、`Message.tool_args`、SourceDoc blob)— 不需新記錄層。

## 4 · 不在範圍(v1)

- 定時背景重跑(Q2 明確排除)
- check fail 自動 gate 功能(Q6 排除)
- 隔離 sandbox 重放 exec(replay 不執行 tool,本項不存在)
- 自動回歸比對(replay 結果與歷史 output 的 diff 評分)— v1 人眼比對

## 4.5 · 首輪實戰戰果(2026-06-06,vlm-describe)

上線第一輪 `vlm-describe` 即 fail。replay/分層診斷結論:

- transport 正常(wire payload 帶 `images`;Ollama 原生 API + 短 prompt 能認出紅色)
- **qwen2.5vl:7b(經 Ollama)對無特徵合成圖不可靠**:配長模板會幻覺(把紅色方塊
  說成折線圖)、把 context 行抄進 transcription;256px 合成圖甚至觸發 llama.cpp
  `GGML_ASSERT` 把 runner 弄崩(upstream bug)。對**有文字的圖**(production 真正
  的輸入:截圖/slides/掃描)則穩定可讀。
- 處置:probe 改為**讀回渲染文字**("REFLOW ZONE 3" → 斷言 "reflow")— 量 ingestion
  真正依賴的能力;`vlm_describe.md` 模板改為描述先行 + 空節出口
  (`(no visible text)` / `(none)`),live 驗證 7B 不再離軌。色彩 probe 會對
  「實務上沒問題」的模型過度警報,棄用。

## 5 · 教訓落地(開發流程)

Fake-LLM 測試驗「我們的程式」,canned check 驗「這顆模型」,replay 驗「這次失敗」。
三層缺一不可 — 新增任何 LLM-backed 功能時,**bundled canned check 是 DoD 的一部分**
(寫功能必附 check,如同必附測試)。
