# 系統架構（Architecture）

本文說明整個 RCA 應用的設計：分層、Protocol、一次 agent 回合的資料流、SSE 事件流、
sandbox / FileStore / 同步的生命週期，以及關鍵設計決策。

> 對照閱讀：[deployment.md](deployment.md)（如何抽換各層）、[contract.md](contract.md)
> （HTTP/SSE 線上契約）、[development.md](development.md)（開發慣例）。

!!! tip "這頁是概觀；逐子系統的深入見「子系統深入」"
    本頁給你**整體心智模型**。要鑽進某一塊（以真實程式碼為錨的職責、模組、Protocol、
    不變式、原始碼錨點），請看 **[子系統深入總覽](subsystems/index.md)** 的 13 篇深入文件。
    名詞查 **[詞彙表](glossary.md)**；「為什麼這樣設計、否決了什麼」查
    **[設計決策](decisions.md)**。新進開發者建議從 **[開發者導覽](index.md)** 起步。

---

## 1. 分層總覽

```
┌─────────────────────────────────────────────────────────────────┐
│  React SPA  (web/)                                                │
│  VSCode 風格 UI：檔案樹／分割編輯區／terminal／agent 面板／       │
│  report 版本／notebook。透過 fetch + SSE 與後端溝通。              │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP + Server-Sent Events
┌───────────────────────────▼─────────────────────────────────────┐
│  FastAPI app  (api/app.py  ← create_app 工廠)                     │
│  REST 路由 + 兩條 SSE 串流（agent 回合、notebook cell 執行）       │
└───┬───────────────┬───────────────┬───────────────┬─────────────┘
    │               │               │               │
    ▼               ▼               ▼               ▼
AgentRunner     Sandbox         FileStore        specstar
Protocol        Protocol        Protocol         (resources/)
(api/runner.py) (sandbox/)      (filestore/)     Investigation /
                                                 AgentConfig /
LitellmAgent    Mock /          Memory /         Conversation
Runner ─► SDK   LocalProcess /  Specstar         的自動 CRUD
─► LiteLLM      Docker
─► Ollama/LLM        │
                    ▲  SandboxSync (sync/)  在 FileStore 與 sandbox 間搬檔
```

每一層都是 **Protocol**（結構型別、duck typing），靠 `create_app(...)` 注入。換掉任何
一塊不需要動其他層——詳見 [deployment.md](deployment.md)。

---

## 2. 核心元件

| 元件 | 位置 | 職責 |
|---|---|---|
| `create_app` | `api/app.py` | 組裝 FastAPI app；注入 sandbox / filestore / runner；掛載 SPA |
| `AgentRunner` | `api/runner.py` | 驅動一次 agent 回合，逐一 yield `AgentEvent` |
| `LitellmAgentRunner` | `api/litellm_runner.py` | 正式實作：包 OpenAI Agents SDK + LiteLLM |
| `Sandbox` | `sandbox/protocol.py` | 指令執行環境（exec/upload/download/walk） |
| `FileStore` | `filestore/protocol.py` | workspace 檔案永久儲存（含「誠實目錄」） |
| `SandboxSync` | `sync/sandbox_sync.py` | FileStore ↔ sandbox 之間的 restore/flush/reverse |
| `AgentToolContext` | `agent/context.py` | 傳進 agent 工具的每回合 context；雙形態：RCA（sandbox/filestore/sync）或 KB（retriever/collection_ids，無 sandbox） |
| agent tools | `agent/tools.py` | RCA：`exec`/`read_file`/`write_file`/`ls`/`exists`/`delete_file`；KB：`kb_search`；橋接：`ask_knowledge_base` |
| `InvestigationRegistry` | `api/registry.py` | 每個調查的 sandbox session：建立、閒置回收、取消回合 |
| specstar resources | `resources/` | RCA：`Investigation`/`AgentConfig`/`Conversation`；KB：`Collection`/`SourceDoc`/`DocChunk`/`KbChat`（msgspec.Struct） |
| 範本 profiles | `rca/templates/` | 新調查的起始檔案 |
| KB `Ingestor` | `kb/ingest.py` | bytes →（嗅 content-type、解壓 zip/tar）→ `SourceDoc`（dedup by xxh3、`status=indexing`）→ `index()` 切塊+嵌入→`DocChunk` |
| KB `Embedder` / `Chunker` | `kb/embedder.py` / `kb/chunker.py` | `LitellmEmbedder`（Ollama/hosted，非對稱 query/doc prefix）/`HashEmbedder`（離線/測試）；`FixedTokenChunker` |
| KB `Retriever` | `kb/retriever.py` | 混合檢索：dense（specstar 原生向量查詢）+ BM25 → RRF → MMR → parent-doc merge；有 `Llm` 時加 multi-query/HyDE/rerank |
| KB agent | `kb/agent.py` + `api/kb_chat_routes.py` | 用同一個 `AgentRunner` + KB `AgentToolContext` + `kb_search`；對話端點串流並持久化（含 `[n]` citations） |

---

## 3. 一次 Agent 回合的資料流

使用者在 agent 面板送出訊息：

```
POST /investigations/{id}/messages   { "content": "..." }
        │
        ▼  (api/app.py → ChatTurnEngine.stream(), see api/turns.py)
1. 把使用者訊息存進 Conversation
2. 解析該調查綁定的 AgentConfig（`ItemLocator.resolve_agent_config`，見 `api/locator.py`；經 `AppCatalog` 三層解析，無 attached preset → profile `default_preset` 否則 picker 第一個）
3. 建 AgentToolContext：filestore、sandbox、sync、ensure_sandbox_via（registry）
4. engine.stream(key, content, ctx, on_complete=persist):
     - 取消前一個還在跑的回合（同一 key 一次只有一個 turn，序列化）
     - async for ev in runner.run(...): ev 逐一 to_sse(ev) → SSE 串流
       （前端 reduceAgent 折進對話狀態），同時 reduce 成中性 TurnMessage
5. 回合結束：on_complete 把 TurnMessage 映射成 Message（author="RCA Agent"）存回 Conversation
```

> **共用的回合引擎**：RCA workspace 與 KB chat 的回合生命週期是同一個
> `ChatTurnEngine`（`api/turns.py`）：每個 conversation 一把 lock + 一個可取消的
> in-flight turn、`_drive` pump（取消 → `RunCancelled`、其他例外 → `RunError`、
> sentinel）、SSE `gen()` 把事件 reduce 成中性 `TurnMessage`、`cancel()`/`forget()`。
> 兩個介面只差在各自建的 `AgentToolContext` 與 `on_complete`（怎麼持久化：
> Conversation/`Message` vs KbChat/`KbMessage`+citations）。`InvestigationRegistry`
> 只剩 sandbox 生命週期（RCA 專屬）。`DELETE …/messages/current` 兩邊都有。

`runner.run`（`LitellmAgentRunner`）內部：

```
run()                          ← 重試外圈：工具/格式錯誤帶提示重試，MaxTurnsExceeded 終止
 └─ _run_once()                ← 把兩個來源 fan-in 成一個 queue：
      ├─ producer task: Runner.run_streamed(...).stream_events()
      │     把 LiteLLM/Qwen 的原始事件正規化成我們的 AgentEvent
      │     （見 §4）並 put 進 queue
      └─ ctx.on_exec_output: 執行中的 exec 工具把 stdout 即時 put 成 ToolLog
    drain loop: 從 queue 取出、yield —— 誰先到先出，所以工具輸出能邊跑邊顯示
```

> **為什麼要 fan-in queue**：Agents SDK 的工具是 request→response，工具執行期間
> SDK 不提供回報 stdout 的管道。所以 exec 工具把輸出推進同一個 queue，drain loop
> 與 producer 併行，長時間指令的輸出才能即時變成 `ToolLog` 事件。

---

## 4. LiteLLM/Qwen ↔ OpenAI 事件正規化

`LitellmModel.stream_response` 會把 chat-completion chunk 轉成 OpenAI **Responses 風格**
的 stream events。但 LiteLLM/Qwen 的事件形狀與 OpenAI 有差異，`litellm_runner.py` 做了
完整分類（這段是本專案最關鍵也最易踩雷的地方）：

**`raw_response_event`**：有 **五種** 事件帶 `.delta` 字串，必須用 `type` 分流而不是
「有沒有 `.delta`」（`_delta_channel`）：

| `data.type` | 通道 | 處理 |
|---|---|---|
| `response.output_text.delta` | content | 可見回答（再經 `ThinkSplitter` 處理內嵌 `<think>`） |
| `response.refusal.delta` | content | 拒答（仍是使用者可見） |
| `response.reasoning_summary_text.delta` | reasoning | 思考（來自 Qwen `delta.reasoning_content`） |
| `response.reasoning_text.delta` | reasoning | 思考（來自 `delta.reasoning`） |
| `response.function_call_arguments.delta` | **ignore** | 串流中的工具參數 JSON——**必須忽略**，否則會漏進回答 |

**`run_item_stream_event`**（`_map_event`）：

| run item | → AgentEvent |
|---|---|
| `tool_called` | `ToolStart(call_id, name, args)`（`raw_item` 可能是物件或 dict，用 `_raw_field` 取值） |
| `tool_output` | `ToolEnd(call_id, output)`（`raw_item` 是 `FunctionCallOutput` TypedDict→dict，`call_id` 要從 dict 取） |
| `message_output_created` | 丟棄（內容已透過 output_text.delta 串流過） |

**token 指標**：`AgentMetrics(phase=up/down/final)`。Ollama 常把 usage 回報成 0，所以
`_final_tokens` 在 usage 為 0/None 時退回字數估算（`chars/4`），避免結算時跳成 ↑0 ↓0。

> 新增/修改事件型別時，**`api/events.py` 與 `web/src/events.ts` 必須同步**（見
> [development.md](development.md)）。

---

## 5. Sandbox / FileStore / 同步的生命週期

**兩個命名空間**：FileStore 用虛擬根 `/`（agent 的檔案工具操作這裡，是永久真相）；
sandbox 是執行環境（exec 在這裡跑）。`SandboxSync` 負責橋接：

```
首次 exec  ─► registry.ensure_handle()  建立 sandbox
                └─ restore(): 把 FileStore 全部檔案上傳進 sandbox（清 dirty）
每次 exec  ─► exec_impl 先 sync.flush(): 把 agent 這回合新寫的 dirty 檔上傳
                再 sandbox.exec(...)
閒置回收  ─► registry.kill_idle()
                └─ reverse(): 把 sandbox 內變動的檔案（套用 ignore 規則）寫回 FileStore
                   再 kill 容器/目錄
```

- **Sandbox 延遲建立**：只有 `exec` 工具第一次用到才開 sandbox；純檔案操作
  （read/write/ls…）走 FileStore，永遠不開 sandbox。
- **誠實目錄**：FileStore 用 `mkdir/rmdir/is_dir/listdir` 真的支援空目錄，不靠 `.keep`
  之類的 hack。
- **刪除不反向傳播**：reverse-sync 不會因為 sandbox 少了某檔就刪 FileStore 的檔
  （太危險）；清理交給 Files API。

### user-namespace 隔離（LocalProcessSandbox）

有 unprivileged user namespace 時，每個指令在 `unshare --user --map-root-user --mount`
建立的命名空間內 `chroot` 到 sandbox 目錄執行：

- `/` 就是 workspace → agent 的 `/script.py` 在 shell 與檔案工具裡解析一致。
- `/usr`、`/etc` 以唯讀 bind 掛入（保護 host），`/dev` 逐節點 bind（`null/zero/...`），
  `/tmp` 為 tmpfs（短暫），host 檔案系統不可見。
- 偵測不到 userns 時自動退回直接在 host 跑（無隔離）。

---

## 6. SSE 事件模型

兩條獨立的 SSE 串流（皆 `text/event-stream`，每筆 `data: {json}\n\n`）：

- **AgentEvent**（`POST …/messages`）：`MessageDelta`（含 `reasoning` 旗標）、
  `ToolStart`、`ToolEnd`、`ToolLog`（即時 stdout）、`AgentMetrics`、`ToolCallParseError`、
  `MaxTurnsExceeded`、`RunError`、`RunCancelled`、`RunDone`。
- **CellEvent**（`POST …/cells/{idx}/execute`）：`CellStream`、`CellDisplayData`、
  `CellError`、`CellDone`。

前端的 `reduceAgent`（`web/src/pages/investigation/agentLog.ts`）是個純 reducer，把
事件流折成對話/run-history 狀態；`tool_log` 會累加到該工具的 `liveOutput`。

完整欄位見 [contract.md](contract.md) §3。

---

## 7. 資料模型（specstar resources）

- `Investigation`：一次調查（title/owner/severity/status/product/topics/members、
  `attached_agent_config_id`、`template_profile`…）。
- `AgentConfig`：agent 人格（model/system_prompt/suggestions/allowed_tools…，見
  [deployment.md](deployment.md) §7）。
- `Conversation`：每個調查的訊息歷史（user/assistant/tool）。

KB（見 §9）：

- `Collection`：一個具名文件集（name/description）。
- `SourceDoc`：一份上傳文件。id = 自然鍵 `{collection_id}/{path}`（**path-keyed，非 per-user**；
  同一 path 在一個 collection 內就是同一份文件）以 `encode_doc_id` 把每個 `/` 換成 `∕`
  （U+2215）成 slash-free 的不透明 token（永不解析，**非**百分比編碼，見 `kb/doc_id.py`）；
  `content` 為原始 bytes
  （`content.file_id` = xxh3 內容雜湊，dedup 用）；`status` = `indexing`/`ready`/`error`。
- `DocChunk`：一個切塊 + 它的嵌入向量（`Vector`，cosine，寬度 `KB_EMBED_DIM`）。
- `KbChat`：一個對話 thread（title/collection_ids/messages，含 `[n]` `Citation`）。

specstar 為這些 model 自動產生 CRUD 路由（藏在 `/docs`）；自訂業務路由（上表）疊在其上。

> **慣例**：永遠 `SpecStar()` 建新實例，不要用 module-level singleton——測試隔離靠這點。

---

## 8. 知識庫（KB）chatbot 子系統

KB 是與 RCA 平行的第二個子系統：把內部文件存成具名 collection，使用者用一個會引用來源
的 agent 對它提問；RCA 的 workspace agent 也能把它當工具呼叫。

**攝取（ingest，`kb/ingest.py`）**
`POST /kb/collections/{id}/documents`（multipart）→ `Ingestor.store(...)`：嗅 content-type
（magic），zip/tar(.gz) 解壓後逐一處理、單檔則直接處理；以 xxh3(原始 bytes) dedup，建立
**opaque slash-free id**（`encode_doc_id`＝自然鍵 `{collection_id}/{path}`，把每個 `/` 換成 `∕`
（U+2215）；path-keyed 非 per-user，**非**百分比編碼；specstar id 不能含 ASCII `/`，見
`kb/doc_id.py`）的 `SourceDoc`（`status=indexing`）。`store` 與慢的
`index()`（切塊 → 嵌入 → 建 `DocChunk` → `status=ready`/`error`）都用 `asyncio.to_thread`
**明確 offload 到工作緒**——阻塞的 magic 嗅探／specstar I/O／嵌入 HTTP 不壓 event loop，
`index` 在背景跑、不擋上傳回應。資料夾上傳＝把每個檔案以其相對路徑當檔名上傳。
doc id 是**不透明 handle，永不解析**：要 `path`/`collection`/`user` 一律讀記錄欄位 +
`created_by` meta；render 端點用 query param 收這個 slash-free id（`GET /kb/documents?id=`）。

**檢索（retrieve，`kb/retriever.py`）**
混合管線：dense（specstar 原生向量查詢，`QB["embedding"].cosine(qv)`，有 pgvector 時走索引）
＋ sparse（BM25）→ RRF 融合 → MMR 去重 → parent-document 文本合併 → top-k `RetrievedPassage`。
注入 `Llm` 時再加 multi-query 擴展、HyDE、LLM rerank（`kb/query.py`、`kb/rerank.py`）。嵌入由
我們算、原始向量存在 `DocChunk`；query/doc 用非對稱 prefix。

**KB agent（`kb/agent.py`、`api/kb_chat_routes.py`）**
重用同一個 `AgentRunner`：KB 版 `AgentToolContext` 帶 `retriever` + `collection_ids`（無
sandbox），工具只有 `kb_search`。agent loop 可多次搜尋、根據結果再查，最後整合作答；`kb_search`
回傳的 passage 在一個 turn 內全域編號（`[1]`、`[2]`…），答案的 `[n]` 由 `parse_citations`
（`kb/citations.py`）對回那些 passage 變成 `Citation`。串流走與 RCA 相同的 `AgentEvent`；
citations 不在串流裡——FE 在 `done` 後 refetch thread 取得已持久化的 `[n]`。KB chat 的回合
與 RCA 共用同一個 `ChatTurnEngine`（§3），所以串流／序列化／取消（`DELETE …/messages/current`）
規格完全一致。RCA agent 透過 `ask_knowledge_base` 工具（`AgentToolContext.ask_kb`，由
`create_app` 注入）呼叫 KB agent，拿回一段含來源的整合答案；該工具把 KB 子代理的搜尋與
reasoning 經本回合的 `on_exec_output` 即時 relay 成 `ToolLog`，所以 RCA 串流裡看得到 KB 的
中間狀態，而不是卡著等整段答案（`answer_question(on_event=…)` + `kb_progress`）。

**前端**：fast chat 抽屜（首頁 Ask agent）與 `/kb` 頁（collection 管理 + 完整對話 + 文件
viewer）。RCA 與 KB 對話共用 `web/src/components/AgentEntryView.tsx` 渲染（可摺疊 reasoning、
工具卡、token metrics），所以兩者外觀一致。HTTP/SSE 細節見 [contract.md](contract.md) §2/§3。

---

## 9. 關鍵設計決策（出處）

| 決策 | 理由 |
|---|---|
| 各層皆 Protocol、靠 `create_app` 注入 | 單點抽換；測試用 Mock/Scripted，正式用真實作 |
| Sandbox 延遲建立、純檔案操作不開 sandbox | 不跑 shell 的對話零成本（grill-me「a2+」策略） |
| `AgentRunner` 為 scripted↔真 LLM 的抽換點 | 測試不依賴 LLM；SSE plumbing 可獨立開發 |
| SSE schema 在 BE/FE 鏡像 | `events.py` ↔ `events.ts` 必須同步 |
| 預設本機 Qwen3（Ollama）而非 hosted | 成本/隱私；可用 LiteLLM 模型字串切換 |
| 小模型失敗模式的重試提示 | Ollama chunk_parser 多工具呼叫會產生無效 JSON → `diagnose_error` 帶提示重試 |
| 記憶體 FileStore 為預設 | 最簡單；要持久化換 `SpecstarFileStore` |
| KB 嵌入由我們算、原始向量存 specstar | 控制非對稱 prefix；dense 檢索可下推 specstar 原生向量查詢（pgvector 時走索引） |
| KB 攝取切成 store（快、同步）+ index（慢、背景） | 慢的嵌入不擋上傳；文件即時以 `indexing` 出現，再翻成 `ready` |
| KB agent 重用 `AgentRunner`、`AgentToolContext` 雙形態 | 不另寫一套 agent；RCA 欄位改 optional，KB 只帶 retriever + `kb_search` |
| RCA + KB 回合共用 `ChatTurnEngine`（`api/turns.py`） | turn/cancel/SSE 邏輯一份；兩邊只注入 context + `on_complete`，不再各刻一套 |
| SourceDoc id ＝ 自然鍵 `{collection_id}/{path}`（path-keyed）以 `/`→`∕`（U+2215）編碼成 slash-free 不透明 handle | specstar id 不能含 ASCII `/`，且 id 會出現在對使用者顯示的連結（故用 `∕` 而非百分比編碼）；id 永不解析，記錄欄位 + `created_by` meta 才是真相 |
| 無綁定 AgentConfig 時 fallback 到 store 第一個 | 預設代理是真的 seeded config（如本機 Qwen3），不是空殼 `workspace-agent` |
| ingest store/index 明確 `asyncio.to_thread` offload | 背景任務必須真正非阻塞，否則阻塞 event loop 會卡住所有請求 |
| `ask_knowledge_base` 把 KB 子代理進度串進 RCA 流 | 否則 RCA 卡著等整段答案、看不到 KB 在搜什麼（`on_exec_output`/`ToolLog`） |
| Job coordinator 由 FastAPI-free 的 `coordinators.build_coordinators` 統一建構（#312） | API 與獨立 worker 共用同一份組裝；API 經 `server.run_consumers` 可變純 producer，`python -m workspace_app.worker <jobtype>` 各 JobType 獨立 pod 化掛 HPA（見 `docs/deployment.md` §11） |

更深的 rationale 與被否決的替代方案，見專案 `/grill-me`（Q1–Q12）對話紀錄與
`docs/plan-backend.md` / `docs/plan-frontend.md`。
