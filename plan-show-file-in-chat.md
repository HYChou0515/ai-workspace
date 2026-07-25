# Plan — agent 在 chat 中顯示 workspace 的檔案

> Grill 收斂（2026-07-25）。分支 `worktree-show-file-in-chat`，尚未 push。

## 需求

> 「現在 ai 寫完 python 之後 畫圖也只會放在路徑中然後說圖在 /path/to/img
> 我希望當 ai 知道路徑時能直接顯示在 chat 裡面 這需要前端配合」

範圍是**檔案**不只圖片：「我要的是 ai 有能力在 chat 中顯示 workspace 的檔案」。

## 現況

| 路徑 | 狀態 |
| --- | --- |
| user 夾帶圖片進 chat | 通（`imagePaths`，#519/#598） |
| tool 貼圖進 chat | 靠前端猜，且在摺疊卡片裡 |
| agent 答案貼檔進 chat | 不通 |

猜的位置是 `web/src/renderers/toolImages.ts`：正則找 `"images": [...]` / `"plots": [...]`，
呼叫點 `web/src/components/AgentEntryView.tsx:833` 對**每個**工具的輸出文字都套。三個病：

1. **打不到本案。** 只認那個 JSON 形狀；`exec` 印「圖存到 out/chart.png」不觸發。
2. **會誤判。** 純文字比對、無過濾、沒驗檔存在 —— `exec cat some.json` 就會中，撈到不存在的路徑破圖。
3. **撈到也躲著。** `AgentEntryView.tsx:853` 是 `<details open={streamingLive}>`，跑完就摺疊，
   重整後要自己點開。

後端服務檔案的管線已存在且過權限閘：`GET /a/{slug}/items/{item_id}/files/{path}`
（`api/file_routes.py:569`，`require_access(..., "read_content")`）。

## 定案

| # | 定案 | 否決的選項 |
| --- | --- | --- |
| D1 | 訊號來源＝結構化工具 `show_file(path, caption)` | 教模型寫 `![](path)`（賭小模型合規、驗不到檔）；前端從文字撈路徑（＝病 2 再來一次） |
| D2 | 騎現有 tool 訊息，零新 event、零 schema | 新 SSE event + `Message.attachments[]`（四處同步，換到的「附件可查詢」本案不需要） |
| D3 | 圖片 inline；其餘檔卡片＋點開 | 連圖片也只給縮圖（痛點只解一半）；整套 renderer 搬進對話（對話流被切碎） |
| D4 | 是 tool 不是 skill | skill 是 per-message opt-in（`api/chat_send.py:569`），且無執行時機可驗路徑 |
| D5 | 歸一到 `shown_files`，退役正則嗅探 | 改 `sample-tools/*` 鍵名（那是對外契約，`test_cli.py` 有斷言） |
| D6 | 只給有工作區的 app；KB 對話不給 | 給了再拒絕（#537：模型會以為整個能力壞掉） |

D2 的先例是 `ask_user` / `AskUserCard`（#591，`AgentEntryView.tsx:219`）：`tool_args` 已持久化
（`api/turns.py:372` → `web/src/api/types.ts:52`），reducer 已產 `ToolCallView`。
宣告內容放**工具結果**而非只放 `tool_args`，因為 mime／size／正規化路徑是後端算的。

D3 的檢視器已存在：`renderers/registry.ts`（image/pdf/csv/notebook/json）＋
`hooks/openFile.tsx` 的 `useOpenFile()`（WorkspaceShell 外回傳 `null` → 降級不畫死控件）。

## Phases

### P1 — 後端 `show_file` ✅

- `agent/tools.py::show_file_impl(path, caption=None)`：驗 `read_content` → 讀檔 →
  不存在回 `error:` 且不宣告 → `magic` 嗅 mime → `{"shown_files":[…], "note":…}`
- 路徑用 `abs_path`（`files/facade.py` 的 `_norm` 改公開，與 `rel_path` 成對）
- 註冊 `_IMPLS`、`_WORKSPACE_TOOLS`（預設開）、`tool_authz.TOOL_VERBS`、`agent/__init__.py`
- `tests/agent/test_show_file_tool.py`（7）＋ `test_tools.py::test_show_file_needs_no_opting_in`
- `tests/agent` + `tests/files`：507 passed
- `cap_tool_outputs` 自動掛在每個工具，無需額外註冊

### P2 — 前端讀宣告 + 卡片

- `web/src/renderers/shownFiles.ts` ✅（10 測試）：`parseShownFiles` 零正則、
  `JSON.parse` 整份讀一個鍵、半截 JSON → `[]`；`isInlineImage` 看嗅出的 mime，SVG 算圖
- `web/src/components/ShownFiles.tsx` ⬜（12 測試已寫、元件未實作）：圖片 inline、
  `alt` 用 caption；非圖片卡片給檔名 + `formatBytes`，不露 mime；點擊優先 `useOpenFile()`，
  無 shell → `fileUrl` 連結，兩者皆無 → 只顯示檔名

### P3 — 接進對話流

- `AgentEntryView` 分派 `name === "show_file"` 且有宣告 → 畫在摺疊卡片外；
  宣告為空 → 落回普通工具卡（錯誤仍可見）
- `TOOL_LABEL`（`AgentEntryView.tsx:107`）+ `lib/i18n.tsx` 補 `show_file`

### P4 — 退役正則嗅探

- 刪 `toolImages.ts` 與 `AgentEntryView.tsx:833` 呼叫點及貼圖區塊
- 後端把 sci-plot `images` / csv-summary `plots` 正規化成 `shown_files`，並驗檔存在

### P5 — prompt 只正面列能力 ✅

`feedback_prompt_positive_only`。P1 docstring 原有反向敘述，已改。

### P6 — 驗收

- `uv run ty check` 全專案不 scope；`ruff check` + `format --check`
- targeted：`tests/agent`、`tests/files`、`tests/api`、`web` 相關（全套 gate 交 CI）
- **真瀏覽器**（playwright 內建 chromium）：多張圖、超長檔名、暗色主題 —— happy-dom 量不到版面
- **真模型 live check**：實跑「幫我畫一張圖」，確認模型呼叫 `show_file`、圖真的出現

## push 開 PR 

