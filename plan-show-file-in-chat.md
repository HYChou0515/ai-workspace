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
| D3 | 圖片以**縮圖**呈現（260px 上限），點開看全圖；其餘檔卡片＋點開 | 整套 renderer 搬進對話（對話流被切碎） |
| D4 | 是 tool 不是 skill | skill 是 per-message opt-in（`api/chat_send.py:569`），且無執行時機可驗路徑 |
| D5 | 歸一到 `shown_files`，退役正則嗅探 | 改 `sample-tools/*` 鍵名（那是對外契約，`test_cli.py` 有斷言） |
| D6 | 只給有工作區的 app；KB 對話不給 | 給了再拒絕（#537：模型會以為整個能力壞掉） |
| D7 | 宣告是工具結果尾端的 `[shown-files]{json}` 一行 | 整份輸出當 JSON（產圖工具的結果是 `_format_exec` header＋JSON，且 header 的 exit code 是小模型的定位點）；新 event＋Message 欄位（＝D2 已否決的四處同步） |

**D7 是實作時才浮現的，grill 沒問出來。** 原本打算「前端把整份工具輸出 `JSON.parse`」，
但那只對 `show_file` 成立 —— 產圖工具的結果是散文＋JSON。標記讓兩個來源共用同一條前端路徑，
而且宣告留在**已持久化的 tool 訊息裡**，所以重整後自動還原，不必付 schema 的錢。
`show_file` 也改走標記（結果＝一句話給模型讀 + 標記行），前端因此只有一條解析路徑。

D2 的先例是 `ask_user` / `AskUserCard`（#591，`AgentEntryView.tsx:219`）：`tool_args` 已持久化
（`api/turns.py:372` → `web/src/api/types.ts:52`），reducer 已產 `ToolCallView`。
宣告內容放**工具結果**而非只放 `tool_args`，因為 mime／size／正規化路徑是後端算的。

D3 的檢視器已存在：`renderers/registry.ts`（image/pdf/csv/notebook/json）＋
`hooks/openFile.tsx` 的 `useOpenFile()`（WorkspaceShell 外回傳 `null` → 降級不畫死控件）。

**D3 的「縮圖」是看到實物後改的。** 原本定「圖片 inline」，真瀏覽器跑出來是 420px 高的圖佔滿
對話流，把解釋它的文字推出畫面；使用者看了實際畫面後拍板改縮圖 —— 大到認得出，想看再點。

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

### P7 — 讓工具真的到得了 agent（live check 抓到的缺陷）

真模型第一次實跑就露出來：`show_file` 進了 `_WORKSPACE_TOOLS`、`build_tools(None)` 也發得出來、
2600 條單元測試全綠 —— 但**每個 `app.json` 都明列 `agent.tools`**，那份清單才是真 turn 的天花板，
`show_file` 不在任何一份裡。工具等於誰都拿不到。被要求畫圖時，agent 做的正是這個工具存在前的事：
用 `read_image` 自己看那張 png，然後把路徑打進答案裡。

跟 #613 的 live-probe 回歸同一個形狀，所以護欄下在 **manifest 層**而不是再加一條 `build_tools` 斷言：

- `src/workspace_app/apps/*/app.json` 五份全部補上 `show_file`
- `tests/apps/test_show_file_granted.py`：**有 `read_file` 就必須有 `show_file`** ——
  能讀檔的 agent 就該能把檔攤到使用者面前，兩個 grant 必須同進同出。
  含一條「glob 有抓到東西」的自我防呆，否則參數化測試會空轉全綠。

### P8 — 真瀏覽器抓到的兩個缺陷

happy-dom 量不到、單元測試全綠、只有真瀏覽器看得見：

1. **URL 雙斜線** `…/files//out/sine.png`。宣告把路徑正規化成絕對，`encodePath` 又直接接在
   `files/` 後面。後端 `lstrip("/")` 容忍，前面擋一層會正規化路徑的 proxy 就未必。
   修在 `encodePath`（唯一的編碼入口），配 `web/src/api/fileContentUrl.test.ts`。
2. **答案裡多一張破圖**。模型同時呼叫了 `show_file` **並且**在答案裡寫 `![](out/sine.png)`，
   而答案層的 ReactMarkdown 沒有解析 workspace 路徑 → 0×0 破圖 + 404。
   修法是 `img`/`a` override 走 `fileUrl`（跟檔案檢視器 `MarkdownRenderer` 同一條規則），
   沒有 workspace 的介面（KB 對話）則不畫圖而非畫破圖。

⚠️ **第 2 點是一條額外的路徑，要講明白**：`![](path)` 現在也會顯示圖了。它跟被刪掉的正則嗅探
不同 —— 那是在自由文字裡猜路徑，這是 markdown AST 裡的明確作者意圖，且與檔案檢視器同規則。
代價是模型若同時宣告又內嵌，同一張圖會出現兩次（本次實跑就發生了）。**若你要我拿掉，說一聲。**

### P6 — 驗收

- ✅ `uv run ty check` 全專案不 scope；`ruff check` + `format --check` 全綠
- ✅ `pnpm typecheck`（tsc）乾淨
- ✅ targeted 後端：`tests/agent`＋`tests/files`＋`tests/apps`＋`tools/test_review_wiring` **745 passed**
- ✅ 前端全套 **2098 passed / 290 files**
- ✅ **真模型 live check**：本機 `qwen3-14b-ctx40k` 實跑「畫 sin(x) 並把圖給我看」 ——
  模型自己推理出「應該用 `show_file`」、呼叫、宣告進串流、圖在對話裡
- ✅ **真瀏覽器**（playwright 內建 chromium，非 happy-dom）：抓到 P8 的兩個缺陷；
  修完複驗淺色／深色／窄版
- ⬜ 對抗式自我複查
- 全套 100% gate 交 CI（不在本機空等）

## push 開 PR 

