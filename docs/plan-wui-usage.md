# WUI 頁面用量：部署的頁面被多少人打開、被用來做了多少事

> grill 2026-11-04。每條決定標了來源：**〔user〕** = user 逐題定案；**〔預設〕** = 我自己定、列出來給推翻的
> 實作層決定；**〔查證〕** = 讀 `origin/master`（04929646）程式碼確認的事實；**〔指南〕** = 出自公開的 UI/UX 指南，附出處。

## 問題

user 要「WUI 用量統計，像 skill hub 和 MCP 那樣」。現況〔查證〕：

- WUI 頁面**沒有任何用量紀錄**。部署連結 `/w/:slug/:itemId/*`（`web/src/App.tsx`）由 `WuiPage` 畫 `WuiView`，item 裡打開
  頁面也是同一個 `WuiView`；打開頁面只是前端讀 `.ai.yaml` 和 `dist/`，後端沒有「打開」這個事件。
- 頁面能做的事走 bridge（`web/src/renderers/wui/bridge.ts`）：`startRun`、`callTool`、`callAgent`、`startTool`、`startAgent`、
  `jobStatus`／`stopJob`、檔案類、`whoami`／`openLogin`。後端路由在 `api/wui_routes.py`（`wui/run`、`wui/tools/{name}/call`、
  `wui/agents/{name}/call`、`wui/jobs`）。這些請求**不帶頁面是哪一個**，也分不出是部署連結還是 item 裡發的——`X-Wui-Page`
  只有存檔時帶（`api/real.ts:pageHeaders`）。
- `startRun`、`startTool`、`startAgent` 都建成一個 `WorkflowRun`（`job_id` 就是 run id，`orchestrator.start`）；`callTool`／
  `callAgent` 在路由裡等到結果才回。
- 兩個前例：skill hub 條目上的「使用 N 次」（`apps/skill_hub_usage.py`）；Diagnostics「使用統計」頁（`plan-tool-usage-stats.md`，
  只給 superuser：次數、失敗、耗時、誰、在哪、90 天、表格＋圖表＋CSV）。兩者都是「記憶體累加、定時寫回、每列只留一個
  revision」（`tool_usage_store.py:156`、`skill_hub_usage.py:176` 的 `prune_revisions(keep_last_n=1)`）。

## 決定

| # | 決定 | 來源 |
|---|---|---|
| U1 | 統計 WUI 頁面的**打開量**和**使用量** | 〔user〕 |
| U2 | 打開量的單位是**人天**：同一人同一天打開幾次都算一次。顯示**總共**和**過去 7 天** | 〔user〕 |
| U3 | 使用量顯示**次數**和**總時間**（解讀成 AI 替人做事花的時間），總共和過去 7 天各一組；平均不當指標（不顯示） | 〔user〕 |
| U4 | 使用量只算三類**現有**動作：workflow（`startRun`）、工具（`callTool`＋`startTool`）、agent（`callAgent`＋`startAgent`）。不算 `jobStatus`／`stopJob`（查詢、控制）、檔案類、`whoami`／`openLogin` | 〔user〕 |
| U5 | **頁面主人**在部署總覽 `/wui` 的卡片上看自己頁面的數字；**superuser** 看全部和人名明細 | 〔user〕 |
| U6 | 作者自己打開、自己測試**也算** | 〔user〕 |
| U7 | 只算**部署連結**上的打開；使用量同範圍——item 裡打開頁面按的不算 | 〔user〕＋使用量同範圍〔預設〕 |
| U8 | superuser 的統計放進 Diagnostics「使用統計」頁，成為工具、skill 之後的**第三區**，沿用同一套表格、圖表、明細側邊面板、CSV | 〔user〕 |
| U9 | 每頁每天一列，列裡記每個人的打開次數、使用次數、使用時間（依三類分開）；每 5 分鐘與 SIGTERM 時寫回、只留一個 revision；**所有天數都保留** | 〔user〕＋寫法照抄工具統計〔預設〕 |
| U10 | 失敗的動作也算進次數和總時間（失敗那次 AI 一樣花了時間） | 〔預設〕 |
| U11 | 非同步動作（`startRun`／`startTool`／`startAgent`）在 run **走到結束狀態**時記時間；同步動作（`callTool`／`callAgent`）在路由裡量 | 〔預設〕 |
| U12 | superuser 頁的時間範圍：7／30／90 天，再加「全部」 | 〔預設〕 |
| U13 | 「總共」從這個功能上線那天起算，不回補 | 〔預設〕（沒有可回補的資料） |

## 機制

**頁面是哪一個、是不是部署連結**〔預設〕：部署頁（`WuiPage`）在打開與每個動作請求上帶 `X-Wui-Deployed: <頁面 view 路徑>`
（percent-encoded，和 `X-Wui-Page` 同寫法）。item 裡的 `WuiView` 不帶，所以不算（U7）。後端**只在**那個 item＋路徑有部署紀錄
（`DeployedWui`，`api/wui_deploy.py`）時才記；路徑亂填或頁面沒部署 → 不記、請求照常。列數因此以「部署過的頁面數」為上限，
沒人能塞假頁面名稱灌爆資料。

**打開**：新路由 `POST /a/{slug}/items/{item_id}/wui/opened`（body：頁面路徑），`WuiPage` 讀到頁面後打一次；要求
`read_meta`（能看到頁面的人）。只記，不回任何東西給頁面用。

**使用**：
- `callTool`／`callAgent`：路由裡量 `await` 前後的時間，結束時記一筆（成功或失敗都記，U10）。
- `startRun`／`startTool`／`startAgent`：開始時把「哪個頁面、誰」記在 run 旁邊的私有列（和 `RunIdentity` 一樣不開
  auto-CRUD——`WorkflowRun` 的 auto-CRUD 沒有寫入閘，放在它上面會被 PATCH 竄改），run 走到結束狀態時用它的開始／結束時間
  記一筆，然後刪掉那列。run 跑到一半 pod 死掉時，這筆怎麼記要在 P3 先查 run 的續跑機制再定〔待查〕。

**存法**（U9）：model `WuiUsageDay`：`item_id`、`page`、`day`、`pod`，加上 `by_user: {人: [打開次數, workflow 次數, workflow 毫秒,
工具次數, 工具毫秒, agent 次數, agent 毫秒]}`。索引 `item_id`、`day`。人天＝那天各 pod 列的 `by_user` 鍵的聯集大小。

**讀**：
- `/wui` 總覽卡片：每張卡片的「總共」「過去 7 天」由後端算好放進 `DeployedPage`（只對頁面主人回傳，其他人拿不到這幾個欄位。「主人」＝能改這個 item 的人〔預設〕）。查詢按 `item_id` 索引，一次查完總覽上所有頁面。
- 使用統計頁第三區：每頁一列（頁面、item、人天、三類次數與總時間）；點一列在側邊面板看每個人；CSV 下載原始的每人每天列。

## UI〔指南〕

- **卡片**：NN/g *Cards: UI-Component Definition*（https://www.nngroup.com/articles/cards-component/）——卡片是「a summary and
  link to additional details」，次要資訊和主內容分開（範例用一條橫線隔開）。數字放在卡片底部的次要區，一行打開量、一行使用量，
  不搶頁面名稱與連結。
- **數量的表現**：NN/g *Dashboards: Making Charts and Graphs Easier to Understand*
  （https://www.nngroup.com/articles/dashboards-preattentive/）——長度是最好比較的編碼，「color should not be used to communicate
  information about quantitative values or magnitude」。卡片上只放數字和單位（不用顏色分大小）；使用統計頁沿用工具統計已有的
  橫向長條（長度）。
- 卡片文字範例：「打開 · 總共 120 人天 · 過去 7 天 18 人天」「使用 · 總共 45 次、3.2 小時 · 過去 7 天 6 次、0.4 小時」；動手前
  再對照截圖確認。

## 已知限制

- pod 沒收到 SIGTERM 就死掉時，最後不到 5 分鐘的計數會掉（和工具統計相同）。
- 頁面改名或搬資料夾後，舊數字留在舊路徑下，不會自動合併。
- 若之後做了「每人一份頁面副本」（#996 `plan-wui-publish.md`），副本是不同 item，會分開算。

## Phases

| Phase | 內容 | 驗收 |
|---|---|---|
| P1 | `WuiUsageDay` 與記錄器（記憶體累加、寫回、只留一個 revision）；人天計算 | 同一人同一天多次＝1 人天；兩顆 pod 的列合併；寫回後只剩一個 revision |
| P2 | `X-Wui-Deployed` header 與部署紀錄檢查；`wui/opened` 路由 | 部署連結打開會記、item 裡打開不記；沒部署或亂填路徑不記 |
| P3 | 三類使用的記錄：同步在路由量、非同步在 run 結束時記 | 每類各一條：次數與時間正確；失敗也記；item 裡按的不記；同一個 run 只記一次 |
| P4 | `/wui` 卡片顯示（只給頁面主人） | 主人看得到、其他人拿不到欄位；數字與後端一致；截圖對照 NN/g |
| P5 | 使用統計頁第三區＋明細＋CSV（superuser） | 同工具統計的元件；7／30／90／全部 |
| P6 | 文件：runbook 條目、design-history、`extending-the-platform.md`（頁面作者看得到什麼） | `tests/docs/test_docs_index.py`；`mkdocs build --strict` |
