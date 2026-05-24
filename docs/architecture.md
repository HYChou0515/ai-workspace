# 系統架構（Architecture）

本文說明整個 RCA 應用的設計：分層、Protocol、一次 agent 回合的資料流、SSE 事件流、
sandbox / FileStore / 同步的生命週期，以及關鍵設計決策。

> 對照閱讀：[deployment.md](deployment.md)（如何抽換各層）、[contract.md](contract.md)
> （HTTP/SSE 線上契約）、[development.md](development.md)（開發慣例）。

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
| `AgentToolContext` | `agent/context.py` | 傳進 agent 工具的每回合 context（sandbox/filestore/sync…） |
| agent tools | `agent/tools.py` | `exec` / `read_file` / `write_file` / `ls` / `exists` / `delete_file` |
| `InvestigationRegistry` | `api/registry.py` | 每個調查的 sandbox session：建立、閒置回收、取消回合 |
| specstar resources | `resources/` | `Investigation` / `AgentConfig` / `Conversation`（msgspec.Struct） |
| 範本 profiles | `rca/templates/` | 新調查的起始檔案 |

---

## 3. 一次 Agent 回合的資料流

使用者在 agent 面板送出訊息：

```
POST /investigations/{id}/messages   { "content": "..." }
        │
        ▼  (api/app.py: gen())
1. 取消前一個還在跑的回合（registry._cancel_prior_turn）
2. 把使用者訊息存進 Conversation
3. 解析該調查綁定的 AgentConfig（_resolve_agent_config）→ 套到 runner
4. 建 AgentToolContext：filestore、sandbox、sync、ensure_sandbox_via（registry）
5. async for ev in runner.run(prompt, ctx):
        ev 逐一 to_sse(ev) → 寫進 SSE 串流（前端 reduceAgent 折進對話狀態）
6. 回合結束：把 assistant 訊息（含 reasoning）存回 Conversation
```

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

specstar 為這些 model 自動產生 CRUD 路由（藏在 `/docs`）；自訂業務路由（上表）疊在其上。

> **慣例**：永遠 `SpecStar()` 建新實例，不要用 module-level singleton——測試隔離靠這點。

---

## 8. 關鍵設計決策（出處）

| 決策 | 理由 |
|---|---|
| 各層皆 Protocol、靠 `create_app` 注入 | 單點抽換；測試用 Mock/Scripted，正式用真實作 |
| Sandbox 延遲建立、純檔案操作不開 sandbox | 不跑 shell 的對話零成本（grill-me「a2+」策略） |
| `AgentRunner` 為 scripted↔真 LLM 的抽換點 | 測試不依賴 LLM；SSE plumbing 可獨立開發 |
| SSE schema 在 BE/FE 鏡像 | `events.py` ↔ `events.ts` 必須同步 |
| 預設本機 Qwen3（Ollama）而非 hosted | 成本/隱私；可用 LiteLLM 模型字串切換 |
| 小模型失敗模式的重試提示 | Ollama chunk_parser 多工具呼叫會產生無效 JSON → `diagnose_error` 帶提示重試 |
| 記憶體 FileStore 為預設 | 最簡單；要持久化換 `SpecstarFileStore` |

更深的 rationale 與被否決的替代方案，見專案 `/grill-me`（Q1–Q12）對話紀錄與
`docs/plan-backend.md` / `docs/plan-frontend.md`。
