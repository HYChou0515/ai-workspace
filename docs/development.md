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
               InvestigationRegistry、events.py（AgentEvent/CellEvent）
  agent/       AgentToolContext、tools.py（exec/read/write/ls/exists/delete）
  sandbox/     Protocol + Mock / LocalProcess / Docker
  filestore/   Protocol + Memory / Specstar
  sync/        SandboxSync（restore/flush/reverse）+ ignore 規則
  resources/   msgspec.Struct（Investigation / AgentConfig / Conversation）
  rca/         system prompt（prompts/system.md）、agent 工廠、範本 profiles

web/src/
  pages/investigation/   InvestigationShell（VSCode 殼）、AgentPanel、FileTree、
                         SearchPanel、TerminalPane、agentLog.ts（reduceAgent）
  renderers/             Markdown / Text / FileView / notebook / report / fishbone
  hooks/                 useEditorGroups、useAgent、fileBuffer、useStickToBottom…
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
2. 加進 `_IMPLS` dict（key 是工具名）。`build_tools(allowed)` 會自動包成 `FunctionTool`。
3. 若工具有即時輸出需求，沿用 `ctx.context.on_exec_output` 那套 sink（見 exec_impl）。
4. 在 `tests/agent/test_tools.py` 寫測試（用 `MockSandbox`/`SpecstarFileStore` 的 `ctx` fixture）。
5. 要限制某 AgentConfig 能用哪些工具，設其 `allowed_tools`（空 = 全部）。

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

## 8. 測試備註

- Docker sandbox 測試在本機有 daemon 時跑、否則自動 skip。
- Ollama live 測試（`test_live_run_against_ollama_*`）在 daemon/模型不在時自動 skip；
  它是唯一會真的跑 `_run_once` 的測試。
- user-namespace 隔離測試在 userns 不可用時 skip（`_needs_userns`）。
- FE 用 `// @vitest-environment happy-dom` 開需要 DOM 的 renderHook 測試。
