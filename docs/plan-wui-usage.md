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
| U9 | 每頁每天一列，列裡記每個人的打開次數、使用次數、使用時間（依三類分開）；每 5 分鐘與 SIGTERM 時寫回、只留一個 revision；**所有天數都保留** | 〔user〕＋記憶體累加、定時寫回照抄工具統計〔預設〕；「一列」跨 pod 合併（review 第 1 輪改，見〈存法〉） |
| U10 | 失敗的動作也算進次數和總時間（失敗那次 AI 一樣花了時間） | 〔預設〕 |
| U11 | 底下是一個 run 的動作（`startRun`／`startTool`／`startAgent`，以及 `callAgent`——路由只是等它的 run 跑完）在 run **走到結束狀態**時記；只有 `callTool` 在路由裡量 | 〔預設〕（`callAgent` 實作時改，它底下就是一個 run） |
| U12 | superuser 頁的第三區跟工具、skill 兩區**共用頁面的 7／30／90 天**（實作時改：一頁兩種時間範圍會讓人以為三區比的是同一段時間）；「總共」看主人的卡片；報表與 CSV 的 API 另接受 `days=0` 代表全部。也跟頁面的 App 篩選；選了「頁面」以外的場合時，第三區說「這一區只有頁面場合」 | 〔預設〕 |
| U13 | 「總共」從這個功能上線那天起算，不回補 | 〔預設〕（沒有可回補的資料） |

## 機制

**頁面是哪一個、是不是部署連結**〔預設〕：部署頁（`WuiPage`）在每個動作請求上帶 `X-Wui-Deployed: <頁面 view 路徑>`（打開不帶 header，路徑放在 body，見下）
（percent-encoded，和 `X-Wui-Page` 同寫法）。item 裡的 `WuiView` 不帶，所以不算（U7）。後端**只在**那個 item＋路徑有部署紀錄
（`DeployedWui`，`api/wui_deploy.py`）時才記；路徑亂填或頁面沒部署 → 不記、請求照常。列數因此以「部署過的頁面數」為上限，
沒人能塞假頁面名稱灌爆資料。

**打開**：新路由 `POST /a/{slug}/items/{item_id}/wui/opened`，頁面路徑放在 **body**（不是 header），`WuiPage` 讀到頁面後打
一次；同一次掛載裡同一頁只報一次，讀取失敗後按「Try again」讀回來不算第二次（review 第 1 輪）。要求 `read_content`（和
`/w/` 連結本身同一個門檻）。只記，不回任何東西給頁面用：沒部署、路徑不存在都回 204；路徑含 `..` 這種不合法的寫法回
400（`file_routes._workspace_path` 的規則，和部署路由一樣）。

**使用**：
- `callTool`：路由裡量工具真的執行的那段時間（被拒絕的不算，沒花任何人的時間），結束時記一筆（成功或失敗都記，U10）。
- `startRun`／`startTool`／`startAgent`，以及 `callAgent`（它底下也是一個 run，路由只是等它跑完）：開始時把「哪個頁面、誰」
  記在 run 旁邊的私有列 `WuiRunUse`（和 `RunIdentity` 一樣在 `spec.apply` 之後註冊，沒有路由；不放進 `WorkflowRun`，
  run 紀錄的形狀不變），run 結束時記一筆，然後刪掉那列——「取出並刪除」，所以只記一次。
- **時間取自 run 自己的紀錄**：`WorkflowRun.started` 到 `ended`（review 第 1 輪改；原本是寫下歸屬時看時鐘）。所以排隊等
  空位的時間不算（那段時間沒替任何人做事）；還沒開始就被停的 run 算一次、0 毫秒；頁面工作的時間以它的 `deadline`
  為上限——遺失的頁面工作要等有人碰它才會被結掉，那時可能早已過期限很久。逾時被停的工作（`page_job_timeout_s`）
  照 U10 算一次，時間到期限為止。
- **結束有兩個寫入來源**〔查證〕：driver（`workflow/driver.run_workflow`）自己寫完成／失敗／取消——driver 已經開始跑之後的逾時
  也是它先寫：`wait_for` 逾時先取消 driver，driver 寫成取消並通知，orchestrator 之後再改寫成失敗時 run 已經不是進行中，
  不會再通知；orchestrator 的 `_patch` 寫頁面工作遺失、放棄、還沒開始就被停（包括等空位等到期限已過、driver 根本沒跑的
  頁面工作：那時 `_patch` 把它從 pending 寫成失敗並通知，算一次、0 毫秒）。兩邊都通知 `on_settled`（driver 多一個參數；`_patch` 只在
  「原本是進行中、這次寫成結束」時通知，gate 暫停與恢復是進行中之間的移動，不通知）。只掛 `_patch` 的第一版漏掉了
  driver 那邊，測試抓到。
- **路由寫下歸屬之前 run 就跑完**：那時通知找不到歸屬。路由寫完歸屬後再看一次 run 狀態，已經結束就當場記。
- **pod 死掉**〔查證〕：歸屬存在資料庫裡，不在記憶體，但不是每種 run 都會被結掉——見〈已知限制〉。

**存法**（U9，review 第 1 輪改）：
- `WuiUsageDay`：`item_id`、`page`、`day`，加上 `by_user: {人: [打開次數, workflow 次數, workflow 毫秒, 工具次數, 工具毫秒,
  agent 次數, agent 毫秒]}`。索引 `item_id`、`day`。**每頁每天一列，不分 pod**：每顆 pod 寫回時讀那一列、加上自己的數字、
  帶著讀到的 etag 寫回（compare-and-swap），被別的 pod 搶先就重讀重做。第一版是「每頁每天每顆 pod 一列」（照抄工具
  統計），列數會隨 pod 數和每次重啟倍增。
- `WuiUsageTotal`：每頁一列，`person_days`、`uses`、`uses_ms`。寫 day 列的同一次寫回，把這次**多出來的**人天（這次讓某人
  那天的打開從 0 變成大於 0 的人數）、次數、毫秒加上去，同樣用 compare-and-swap。day 列寫失敗 → 數字退回記憶體，下次
  再寫；day 列寫成功但 total 寫失敗 → 只記 log，不重試（重試會把同一批數字加兩次）。
- 人天＝那天 `by_user` 裡打開次數大於 0 的人數。只有使用、沒有打開紀錄的人（例如打開那一下沒送到）不算人天。
- 日期是 **UTC** 的日期。

**讀**：
- `/wui` 總覽卡片：每張卡片的「總共」「過去 7 天」由後端算好放進 `DeployedPage`（只對頁面主人回傳，其他人拿不到這幾個欄位。「主人」＝能改這個 item 的人〔預設〕）。「總共」讀 `WuiUsageTotal`，「過去 7 天」讀那 7 天的 day 列——兩次按索引的查詢，筆數以「頁面數」和「7 天」為上限，不會隨上線天數變多（review 第 1 輪：第一版每次載入都讀全部歷史）。讀失敗時總覽照常列出頁面，只是沒有數字。
- 使用統計頁第三區：每頁一列（頁面、item、人天、三類次數與總時間）；點一列在側邊面板看每個人；CSV 下載原始的每人每天列。

## UI〔指南〕

- **卡片**：NN/g *Cards: UI-Component Definition*（https://www.nngroup.com/articles/cards-component/）——卡片是「a summary and
  link to additional details」，次要資訊和主內容分開（範例用一條橫線隔開）。數字放在卡片主體和底部（擁有者、下架）之間的次要區，一行打開量、一行使用量，
  不搶頁面名稱與連結。
- **數量的表現**：NN/g *Dashboards: Making Charts and Graphs Easier to Understand*
  （https://www.nngroup.com/articles/dashboards-preattentive/）——「color should not be used to communicate information about
  quantitative values or magnitude」。卡片上只放數字和單位（不用顏色分大小）；使用統計頁沿用工具統計已有的
  橫向長條（長度）。
- 卡片文字（實作後）：「**打開** 總共 120 人天 · 近 7 天 18 人天」「**使用** 總共 45 次、3.2 小時 · 近 7 天 6 次、24 分鐘」。
  時間的單位四捨五入**之後**才選（59.6 秒寫「1 分鐘」，不寫「60 秒」）。

## 已知限制

- pod 沒收到 SIGTERM 就死掉時，最後不到 5 分鐘的計數會掉（和工具統計相同）。
- **頁面的 `startRun` 跑到一半 pod 死掉，永遠不會被算到**〔查證〕：孤兒 run 的追蹤（#429 P8，`TriggerSweeper` 的
  `OrchestratorOrphanOps`）只處理排程觸發的 run，頁面起的 run 會停在「執行中」，`WuiRunUse` 那列也留著。
- **遺失的頁面工作要有人碰才會被算到**〔查證，含 #984 之後〕：只有三個地方會把過期的頁面工作結掉——同一個 item 開新的
  頁面工作（`start_page_job`）、頁面查那個工作的狀態（`page_job_state`）、列出 item 的頁面工作（`page_jobs`，
  `GET …/wui/jobs`）。沒人再碰就一直不算；算到時時間以期限為上限。
- **停過 gate 的 run 只算最後一段**〔查證，review 第 2 輪〕：gate 會讓 driver 結束，有人回應後重跑 `run_workflow`，
  `started` 被改成恢復的時間。所以等人的時間不算，gate 之前做的事也不算——時間是「最後一次恢復到結束」。
- **跨 pod 同時寫同一列**：合併靠 specstar 的 etag 檢查（改既有列）與 `if_not_exists`（建第一列）；兩者在 specstar 裡都是
  「先查、再寫」分開做，不是原子的。兩顆 pod 在同一瞬間寫同一列時，其中一方的數字仍可能被覆蓋——這兩個檢查只把機會
  縮到同一瞬間。專案裡其他手寫的 compare-and-swap 也是同一個前提。
- **「總共」可能比 day 列少**：day 列寫成功後 total 寫失敗（或 pod 正好在兩者之間死掉），那次的數字只在 day 列裡，
  total 不會補回（重試會把同一批數字加兩次）。卡片上「總共」可能小於「近 7 天」。
- **滾動更新時正在跑的 run 算不到**〔查證，讀程式碼，未重現〕：關機時 lifespan 先寫回計數，還在跑的 run 要到 event loop
  收尾時才被取消；那時記下的使用沒有下一次寫回。
- 逾時被停的頁面工作算一次使用（U10），時間記到期限為止。
- 頁面改名或搬資料夾後，舊數字留在舊路徑下，不會自動合併。
- 若之後做了「每人一份頁面副本」（#996 `plan-wui-publish.md`），副本是不同 item，會分開算。

## Phases

| Phase | 內容 | 驗收 |
|---|---|---|
| P1 | `WuiUsageDay` 與記錄器（記憶體累加、寫回、只留一個 revision）；人天計算 | 同一人同一天多次＝1 人天；兩顆 pod 的列合併；寫回後只剩一個 revision |
| P2 | `X-Wui-Deployed` header 與部署紀錄檢查；`wui/opened` 路由 | 部署連結打開會記、item 裡打開不記；沒部署或亂填路徑不記 |
| P3 | 三類使用的記錄：同步在路由量、非同步在 run 結束時記 | 每類各一條：次數與時間正確；失敗也記；item 裡按的不記；同一個 run 只記一次 |
| P4 | `/wui` 卡片顯示（只給頁面主人） | 主人看得到、其他人拿不到欄位；數字與後端一致；截圖對照 NN/g |
| P5 | 使用統計頁第三區＋明細＋CSV（superuser） | 同工具統計的元件；共用頁面的 7／30／90；整頁仍只有一個側邊面板 |
| P6 | 文件：runbook 條目、design-history、`wui.md`（頁面作者看得到什麼：寫在 WUI 總覽那一節，作者讀的是那裡） | `tests/docs/test_docs_index.py`；`mkdocs build --strict` |
