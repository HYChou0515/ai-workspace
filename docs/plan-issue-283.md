# Plan — #283 精進 workflow（操作者 UX）

> 來源：#283「我們需要精進 workflow」。原 issue 綁了三個抱怨，經 /grill-me 拆分：
> - **#287** — 設定/編寫不直觀（作者 DX）。
> - **#288** — 對話操控進行中的 run + 增量續跑（steering）。
> - **#283（本 plan）** — 操作者 UX：抱怨 1（使用不直觀）+ 抱怨 3（進度視覺化太陽春）。

## 鎖定的設計決策（/grill-me）

### A. 啟動前預覽對話框（pre-flight）— 解「不知道選哪個 / 它做什麼 / 要先備好輸入 / 盲目 no-op」

- 按「執行」**一律先彈** pre-flight dialog，**沒有跳過快通道**。
- 對話框內容 = 現有 manifest 欄位 `title` + `description` + `phases`（**不加新欄位**，`summary` 與 `description` 職責會打架；description 空就少一段）＋**作者提供的 pre-flight checklist 報告**。
- **作者提供 pre-flight hook**：workflow 定義多一個 `async def preflight(wf, inputs) -> PreflightReport`，回傳
  - checklist 項目（沿用 `checks.py` 的 `CheckResult(ok, reason)`，**新增 severity = required | advisory**）
  - 一句**具體會發生什麼**的人話預覽，例：「即將把 `uploads/` 裡的 N 個檔案吸收分組上傳到 a, b, c collections」。
- **擋不擋**：必要（required）check 沒過 → 「執行」鈕 disable + 說明為何不能跑 + 怎麼修；選配（advisory）沒過 → 顯示警告但仍可確認。
- pre-flight **選用**：作者沒寫就退回只顯示 phases（＋可選的通用檔案數計算）。topic-hub 既有 workflow 這輪補上 preflight + 好的 description。
- **新端點**：`runs/preview`（不真的開跑）跑 preflight、回 `PreflightReport`。

### B. 進度視覺化（抱怨 3）— 保留簡單 + 加複雜時間軸，雙視圖

- **保留**現有 `WorkflowStepBoard`（簡單步驟清單）當**預設**視圖。
- 新增 **Timeline / Gantt** 第二視圖，頂部 **segmented 切換 `[步驟清單 | 時間軸]`**，記住選擇（`usePersistentBoolean`）。
- **Phase diagram + 頂部 metrics 列**無論哪個視圖都常駐（視圖無關）。
- Timeline 資料直接吃現有 `StepState.started/ended ms`，**不需新後端資料**。
- **時間軸壓縮 active time**；`awaiting_human` 的等待空檔用斷裂/折疊標記帶過（標「等人 Nm」），避免 step 條被巨大留白擠成面條。
- **互動**：可平移（x = 時間、y = 多列 step）、可 zoom in/out（x = 時間軸）。
- **邊跑邊看 live-tail**：預設自動跟隨「現在」邊緣；一旦手動 zoom/pan 離開就**停止跟隨**（不被拉走）＋右下角出「回到現在」鈕跳回。
- **DAG 依賴圖：不做**（後端沒記依賴邊，多數 workflow 線性 → ROI 低）。

### C. run / chat / item 重整（Design D — 「不關門」）

- **run 升級成一級物件**：把現有 history dropdown 升級成 **Runs 清單**；每個 run 有自己的 panel → 修掉「跑完蹤跡去哪 / 怎麼回看舊 run」。
- run panel = 流水線視圖（phase diagram + 時間軸/step board + metrics）＋**該 run 綁定的對話/活動**（#3 的 per-run `Conversation`）顯示在 **run 視圖內**（不是神祕跳出的獨立 chat）→ 修掉「為什麼 chat 突然冒 agent 訊息」。gate 決策卡也在此。
- **本輪只顯示 run 的對話/活動（唯讀）**，**不放自由輸入 composer**（自由輸入＋實際操控隨 #288 來）。FE **預留 composer 位**。
- **結構保證（門開著）**：per-run `Conversation` 仍是該 run agent turn 的唯一歸宿；run↔chat 不離婚，#288 的 steering 加得上。

### 不在本 issue

- 作者 DX / 設定面 / 欄位職責整理 → **#287**。
- 對話操控 run + 增量續跑 + live 中途注入 → **#288**。

## 影響面

- **後端**：preflight hook（`WorkflowHandle` / discovery / manifest 載入端）、`PreflightReport` pydantic 模型、`CheckResult` 加 severity、`runs/preview` 端點。其餘為 FE。
- **前端**：launch dialog、互動式 Timeline 元件、視圖 segmented toggle、Runs 一級清單 + run panel 內嵌對話/活動（唯讀）、i18n。
- **資料**：Timeline 用既有 `started/ended ms`，無 schema 變動；Design D 用既有 per-run `Conversation`，無 schema 變動。

## Phases（flat integer，照 CLAUDE.md）

- **Phase 1** — pre-flight 後端：`PreflightReport` 模型、`CheckResult` 加 `severity`、`preflight()` hook（discovery 載入 + `WorkflowHandle` 注入驅動）、`runs/preview` 端點（不開跑）。/tdd。
- **Phase 2** — pre-flight 前端：launch dialog（一律先彈），渲染 checklist（✓/⚠️/✗ + reason）＋ 人話預覽 ＋ title/description/phases；required 沒過 → disable 執行 ＋ 修法提示。接 `runs/preview`。/tdd（vitest）。
- **Phase 3** — topic-hub 三個 workflow（memory/collections/consolidate）補 `preflight` ＋ 補 `description`；live qwen 驗證 pre-flight 文案正確。
- **Phase 4** — 確認 `StepState.started/ended ms` 對所有 step 種類（agent/agent_write/sandbox）都有持久化；缺的補上（可能 no-op）。/tdd。
- **Phase 5** — 互動式 Timeline 元件：Gantt 條（吃 started/ended）、壓縮 active time、awaiting_human 斷裂標記、pan/zoom、live-tail 跟隨 + 回到現在鈕、頂部 metrics 列。/tdd（vitest）。
- **Phase 6** — 視圖 segmented toggle `[步驟清單 | 時間軸]`（`usePersistentBoolean`）；phase diagram + metrics 常駐於 `WorkflowRunPanel`。/tdd。
- **Phase 7** — Design D 重整：Runs 一級清單（升級 history dropdown）、run panel 內嵌該 run 的對話/活動（唯讀、無 composer、預留位）、把 UI 上的「chat」用語改成 run 導向；保留 per-run `Conversation` 為唯一歸宿。/tdd。
- **Phase 8** — i18n（zh-TW + en）：所有新字串走 `useT`。
- **Phase 9** — 收尾：`uv run coverage … --fail-under=100` ＋ `ruff` ＋ `ty`（unscoped）＋ vitest ＋ `pnpm build`；live 驗證 pre-flight + timeline + run 一級化 於真實 topic-hub run。

## 待 /tdd 期間定的小事

- `runs/preview` 確切路徑（沿用 `/a/{slug}/items/{id}/runs/...` 命名）。
- Runs 清單與 item 頁主聊天的版面關係（提案：主聊天 + Runs 區並列，點 run 開 panel）— 落地時給 mock 由你拍。
- Timeline：手刻 SVG + pointer events（沿用 hand-rolled 風格，不引圖表庫）vs 引輕量庫 — 傾向手刻、保 bundle 小。
