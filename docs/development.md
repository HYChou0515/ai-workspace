# 開發者指南（Development Guide）

給要在這個 codebase 上開發的人：環境、慣例、TDD 流程，以及幾個最常見的「怎麼加一個 X」
的步驟（新增 SSE 事件、agent 工具、檔案 renderer）。

> 對照閱讀：[architecture.md](architecture.md)、[contract.md](contract.md)、
> [deployment.md](deployment.md)。

---

## 1. 環境與指令

**後端**（Python 3.12，uv 管理）：

```bash
uv sync                                              # 安裝
uv run python -m workspace_app                       # 跑 app（127.0.0.1:8000）
uv run pytest tests/path/test_x.py::test_name        # 跑單一測試
uv run coverage run -m pytest && uv run coverage report   # 全測試 + 覆蓋率
uv run ruff check && uv run ruff format --check       # lint + 格式
uv run ty check                                       # 型別檢查
```

**前端**（React + Vite，在 `web/`）：

```bash
cd web && pnpm install
pnpm run dev          # 開發伺服器（5173，proxy 後端）
pnpm run build        # 打包 web/dist（後端自動掛載）
pnpm run typecheck
pnpm exec vitest run  # 跑測試
```

> 工具鏈固定：**uv + pytest + ruff + ty + coverage.py（直接用，不要加 pytest-cov）**。

---

## 2. 不可動搖的慣例

- **語言**：回覆用繁體中文（台灣用語）；程式碼、識別字、commit、檔案內容用英文。
- **TDD**：新功能/修 bug 走 red-green-refactor（見 §3）。`web/` 新程式也要測（vitest）。
- **100% 後端覆蓋率**：`_run_once`（只有 live Ollama 測試會跑）標 `# pragma: no cover`；
  其餘邏輯抽成可單測的純函式來維持覆蓋率。
- **specstar**：永遠 `SpecStar()` 建新實例，不要用 module-level singleton（測試隔離）。
- **SSE schema 同步**：`api/events.py` 改了，`web/src/events.ts` 要跟著改（見 §5）。
- **git**：分項 stage（不要 `git add -A`）；commit 訊息結尾加
  `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`。

---

## 3. TDD 流程（vertical slice）

一次一個行為，不要一口氣寫完所有測試再寫實作：

```
RED   → 寫一個描述「行為」的測試（透過公開介面，不綁實作細節）→ 失敗
GREEN → 寫剛好讓它通過的最小程式 → 通過
重構  → 在綠燈狀態下整理；每步都重跑測試
```

好的測試讀起來像規格、能撐過重構；壞的測試綁內部結構、改名就壞。

---

## 4. 專案結構

```
src/workspace_app/
  api/         create_app、SSE 端點、AgentRunner Protocol、LitellmAgentRunner、
               InvestigationRegistry、events.py（AgentEvent/CellEvent）、
               kb_routes.py（collections/docs）、kb_chat_routes.py（threads + chat SSE）
  agent/       AgentToolContext（雙形態 RCA/KB）、tools.py（exec/read/write/ls/exists/
               delete、kb_search、ask_knowledge_base）
  sandbox/     Protocol + Mock / LocalProcess / Docker
  filestore/   Protocol + Memory / Specstar
  sync/        SandboxSync（restore/flush/reverse）+ ignore 規則
  resources/   msgspec.Struct（Investigation / AgentConfig / Conversation；
               KB：Collection / SourceDoc / DocChunk / KbChat）
  kb/          chunker、embedder、ingest、retriever（hybrid）、fusion/bm25/merge、
               query（multi-query/HyDE）、rerank、citations、llm、agent + prompt
  rca/         system prompt（prompts/system.md）、agent 工廠、範本 profiles

web/src/
  pages/investigation/   InvestigationShell（VSCode 殼）、AgentPanel、FileTree、
                         SearchPanel、TerminalPane、agentLog.ts（reduceAgent）
  pages/kb/              KbHome（/kb 殼）、AskAgentLauncher/AskAgentDrawer（快速問答）、
                         KbChatPanel/KbChatView、KbChatsPage、KbCollectionsPage、
                         KbDocBody/KbDocViewer/KbDocPage、kbLinks、rehypeHighlightSnippet
  components/            AgentEntryView（RCA 與 KB 共用的 log 渲染）、Icon、…
  api/                   index（RCA client）、kb.ts / kbMock.ts（KB client）
  renderers/             Markdown / Text / FileView / notebook / report / fishbone
  hooks/                 useEditorGroups、useAgent、useKbChat、fileBuffer…
  events.ts              AgentEvent/CellEvent（鏡像後端）
```

---

## 5. 怎麼新增一個 SSE 事件型別

事件要跨 BE→FE 三處同步：

1. **後端 `api/events.py`**：加 `@dataclass(frozen=True)`，含 `type: Literal["…"] = "…"`，
   並加進 `AgentEvent`（或 `CellEvent`）union。
2. **發送點**：在 `litellm_runner.py`（或對應產生器）yield 它。
3. **前端 `web/src/events.ts`**：加對應 `type`，並加進 `AgentEvent` union；若為終止事件，
   更新 `isTerminal`。
4. **前端 reducer `pages/investigation/agentLog.ts`**：在 `reduceAgent` 加 `case`，
   把事件折進狀態；先寫 vitest（`agentLog.test.ts`）紅燈再實作。
5. **渲染**：在 AgentPanel／InvestigationShell 對應位置顯示。
6. **契約文件**：更新 [contract.md](contract.md) §3 表格。

> 範例：`ToolLog`（即時工具輸出）就是照這流程加的——events.py→litellm_runner→events.ts
> →reduceAgent（累加 `liveOutput`）→ToolCallCard/run-history 渲染→contract.md。

---

## 6. 怎麼新增一個 agent 工具

1. 在 `agent/tools.py` 寫 `async def my_tool_impl(ctx: RunContextWrapper[AgentToolContext], ...)`，
   透過 `ctx.context.filestore` / `ctx.context.sandbox` 操作。
2. 加進 `_IMPLS` dict（key 是工具名）。`build_tools(allowed)` 會自動包成 `FunctionTool`；
   `allowed=None` 回傳 `_WORKSPACE_TOOLS`（RCA 預設集，含 `ask_knowledge_base`）。
3. 若工具有即時輸出需求，沿用 `ctx.context.on_exec_output` 那套 sink（見 exec_impl）。
4. 在 `tests/agent/test_tools.py` 寫測試（用 `MockSandbox`/`SpecstarFileStore` 的 `ctx` fixture）。
5. 要限制某 AgentConfig 能用哪些工具，設其 `allowed_tools`（空 = 用 `_WORKSPACE_TOOLS`）。

> **`AgentToolContext` 是雙形態**：RCA turn 帶 `sandbox/filestore/sync/investigation_id`；
> KB turn 帶 `retriever/collection_ids`、沒有 sandbox。所以這些欄位都是 optional——RCA 工具
> 開頭用 `_workspace(ctx)` 斷言取出 filestore/investigation_id，KB 的 `kb_search` 斷言 retriever。
> 只給 KB 用、需要 retriever 的工具（如 `kb_search`）不要放進 `_WORKSPACE_TOOLS`，由 KB
> AgentConfig 的 `allowed_tools` 明確要求。

---

## 7. 怎麼新增一個檔案 renderer

前端依副檔名/檔名挑 renderer（`web/src/renderers/`）：

1. 在 `renderers/` 寫元件，接 `{ investigationId, path }`，用 `useFileBuffer`/`useFileContent`
   取內容。
2. 在 renderer 的分派表（`pages/investigation/renderer.ts`）登記副檔名/規則。
3. Markdown 類請沿用既有設定：`remarkPlugins={[remarkGfm, remarkMath]}`
   `rehypePlugins={[rehypeKatex]}`（LaTeX/GFM 一致）。
4. 二進位/圖片走 `FileView`；所有檔案都應可開啟編輯（含 binary）。
5. 寫 vitest。

> agent 寫檔的慣例（FE renderer 依賴）見 [contract.md](contract.md) §5，例如
> `/report.vN.md`（最大 N 為現行版本）、`*.canvas`（fishbone）。

---

## 8. 怎麼新增一個 workspace template（profile）

每個 profile 是 `src/workspace_app/rca/templates/` 底下的一個子資料夾；picker（`GET /templates`）會自動列出。

1. 建資料夾，放進起始檔案：`*.tpl` 會做 `string.Template` 變數替換並去掉 `.tpl` 副檔名落地（可用變數見 [deployment.md](deployment.md) §8）；其他副檔名原封不動複製。
2. **務必同時放一份 `_prompt.md` 附錄**，只描述**這個 template 的起始檔案**——system prompt 的「base + 附錄」會在 turn 時依投資調查的 `template_profile` 組起來（`rca.templates.compose_system_prompt`）。沒有 `_prompt.md` 的話，agent 只會拿到 template-無關的 base，不知道你 seed 了哪些檔。
   - `_prompt.md` 是 prompt metadata，**不是** workspace 檔——seeding 會自動跳過它（`_walk` 排除），不會出現在檔案樹。
   - **不要把跨 template 的慣例**（`/report.vN.md` 版本規則、fishbone `.canvas` schema、notebook 由 user 執行…）寫進附錄；那些屬於 base `system.md`，附錄只寫「這個 template 有哪些起始檔 + 建議流程」。
3. 在 `tests/rca/test_templates.py` 加測試：profile 出現在 `list_profiles()`、seed 出預期檔案、`load_template_appendix(profile)` 描述的是自己的檔案。

> 為什麼要這條：prompt 與 template 解耦後（commit `ca1c728`），漏寫 `_prompt.md` 不會壞掉但 agent 會「不知道有哪些起始檔」。把附錄和檔案放在同一個資料夾，新增 template 時就不會忘。

---

## 9. 怎麼換／新增 KB 的 chunker / embedder / 檢索步驟

KB 的可抽換點都在 `src/workspace_app/kb/`，皆為小 Protocol：

- **Chunker**（`chunker.py`）：`chunk(text) -> list[Chunk]`。新切法（如 markdown 結構感知）
  實作 Protocol 即可；測試比對 `Chunk.start/end` 為對 canonical text 的字元 offset。
- **Embedder**（`embedder.py`）：`dim` + `embed_documents` + `embed_query`。繼承
  `_PrefixedEmbedder` 只需寫 `_embed` 與 `dim`（prefix 已處理）；live 呼叫標 `# pragma: no cover`。
  注入靠 `create_app(kb_embedder=...)`（見 [deployment.md](deployment.md) §8）。
- **檢索增強**（`query.py` / `rerank.py`，靠 `Llm` Protocol）：multi-query、HyDE、rerank 都是
  「prompt 純函式 + 注入 `Llm`」——parsing 寫 vitest 等級的純測試（fake `Llm`），整合進
  `Retriever.search`，並用 `# pragma: no cover` 圈住 live model 路徑。
- **Retriever 管線**（`retriever.py`）：dense（specstar 原生向量查詢）+ BM25 → `fusion.py`
  RRF → MMR → `merge.py` parent-doc 合併。加新訊號就多疊一個 ranked list 進 RRF。

> 攝取是「`store`（快、同步、`status=indexing`）+ `index`（慢、背景）」兩段——慢的嵌入別放進
> 上傳 request；測試可直接呼叫 `Ingestor.ingest`（= store + index 同步版）。引用解析（`[n]`→
> `Citation`）在 `citations.py`，是純函式（passage registry 傳進去）。

---

## 10. 平台說明頁（Help，#230）的內容怎麼更新

`/help` 頁的「使用說明 + 更新紀錄（release note）」就是一個系統 KB collection
「Platform Help」的內容，AI 問答直接 `kb_search` 它。

- **改內容 = 改 repo**：編輯 `src/workspace_app/kb/help_content/*.md`
  （`getting-started.md` = 使用說明、`CHANGELOG.md` = 更新紀錄；檔名 `CHANGELOG.md`
  會被標成 `release_notes`，其餘為 `guide`）。內容隨 wheel 出貨,**每次開機
  idempotent upsert**（相同 bytes 為 no-op）——repo 是唯一真相,UI 上手改的會被下次
  開機覆蓋。新增一份指南只要丟一個 `.md` 進去即可。
- **權限**：collection 公開可讀可搜,但寫入鎖給 owner（`system`）+ superuser
  （`settings.server.superusers`),用既有 #262 `Permission`,**沒有新權限碼**。
  機制見 `kb/help_collection.py`,端點見 `api/help_routes.py`。
- **前向相容 #281**：之後讓 AI 讀原始碼生成 wiki 會餵進**同一個** collection,
  屆時 source doc 是完整 codebase 而非單一 `CHANGELOG.md`。
- **尚未做（deferred）**：help 專屬「人格」system prompt——目前無 per-collection
  的 chat-prompt 追加掛點,故沿用預設 KB agent prompt（知識庫即 help 內容,問答+引用
  已足夠）；要加人格時再評估注入機制,別為此硬塞新 preset。

---

## 11. 測試備註

- Docker sandbox 測試在本機有 daemon 時跑、否則自動 skip。
- Ollama live 測試（`test_live_run_against_ollama_*`）在 daemon/模型不在時自動 skip；
  它是唯一會真的跑 `_run_once` 的測試。
- user-namespace 隔離測試在 userns 不可用時 skip（`_needs_userns`）。
- FE 用 `// @vitest-environment happy-dom` 開需要 DOM 的 renderHook 測試。
