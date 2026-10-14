# 工具使用統計:管理者看 AI 用了哪些工具、誰用、在哪、成不成功、多快

**狀態:** P1–P7 實作完成(#932);as-built 與計劃不同之處見 §7。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master`(7bf7e6ff)程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻;〔指南〕= 出自公開的 UI/UX 指南,附出處。

## 1. 問題

user 要「統計 MCP tool 使用」。查證後,這個系統的 MCP 只用在平台**外面**〔查證〕:每個工具包 build 時塞進一個
MCP 伺服器(`tooling/mcp_server.py`、`tooling/builder.py:_inject_mcp`),讓工程師在自己電腦上用自己的 agent 跑
發佈的工具;它不回報平台。平台**裡面**的 AI 呼叫工具不走 MCP——內建工具、第一方套件、第三方工具包
(`agent.external_tools`,模型看到 `<本地名>__<command>`)都是同一套 function tool。user 選的是後者(D1)。

現在記了什麼〔查證〕:

- 對話裡每次工具呼叫存一則 `Message(role="tool")`:`tool_name`、`tool_args`、輸出、`created_at`
  (`resources/conversation.py`、`api/turns.py`)。**沒有**:誰、耗時、成功與否、來源;內建和第一方套件工具
  名字長得一樣分不出來。
- `activity`(記憶體環狀 200 筆)、`monitor`(SDK trace 原樣存)、`/health` 都沒有工具呼叫的彙總。
- 前例:skill hub 的「使用 N 次」(`apps/skill_hub_usage.py`):每個條目每天每顆 pod 一列,記憶體累加、
  每 2 小時與 SIGTERM 時寫回,總數用 specstar `Sum`;誰、哪個 item 有記但不顯示(U5)。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 統計**平台內 AI 呼叫工具**,不是平台外的 MCP | 〔user〕選 A |
| D2 | 給**部署管理者(superuser)**看 | 〔user〕選 A |
| D3 | 指標:**呼叫次數、失敗次數、平均耗時** | 〔user〕選 A |
| D4 | **顯示是誰呼叫的**:可點進某工具看各人用了幾次 | 〔user〕「要有人」 |
| D5 | 預設最近 30 天,可切 7/30/90 天;**資料保留 90 天**,更舊的自動刪 | 〔user〕選 A |
| D6 | **所有工具都算**,每筆標來源(內建/第一方套件/第三方);來源在記錄當下查,不從名字猜 | 〔user〕選 A |
| D7 | **所有場合都算**,每筆記場合、App、item | 〔user〕「A,使用場合也要紀錄」 |

## 3. 機制

### 3.1 記在哪裡:每個工具外面包一層〔查證 + 施工〕

所有 AI 的 turn 都經過 `LitellmAgentRunner.run`,工具組都在 `_agent_for` 組出來;組完之後**每一個工具**都會被
`wrap_with_args_recovery` 包一層(`api/litellm_runner.py` 約 600 行;`agent/args_recovery.py:safer`)。那一刻
所有來源都在:內建(`build_tools`)、規則給的(`request_env`、`ask_outside`)、套件指令(`build_function_tools`)
〔查證〕。所以計數就做成**同一個位置再包一層**——每次真的呼叫恰好算一次,串流與非串流兩種 turn 都經過
〔施工〕。

不在事件流(`ToolStart`/`ToolEnd`)上數〔查證〕:非串流 turn 的事件是跑完才重建的,量不到耗時;sub-agent 與 KB
子代理的事件會轉進父 turn 的串流,會重複算;`ToolEnd` 沒有工具名稱。

sub-agent、`ask_knowledge_base` 裡的 KB 子代理各自的工具呼叫由它們自己的 `_agent_for` 算;父 turn 的
`run_agent`/`ask_knowledge_base` 本身也算一次——不重複〔查證〕。

### 3.2 一筆記什麼〔施工〕

| 欄位 | 從哪來 |
|---|---|
| 工具 | 模型呼叫的名字(第三方是 `<本地名>__<command>`) |
| 來源(D6) | 在 `_agent_for` 合併前,每份清單本來就分開——內建、規則給的、第一方套件、第三方(`PackageInfo.third_party`)——在那裡就標好,不在之後從名字猜 |
| 耗時(D3) | 包層裡 `time.monotonic()` 量 `await` 前後 |
| 失敗(D3) | 見 3.3 |
| 誰(D4) | 見 3.4 |
| 場合(D7) | `AgentToolContext` 新增 `surface`,由**建 context 的地方**明確設定(見 3.4);不從 `in_chat`、`retriever` 這些旁證推 |
| App、item(D7) | `ctx.app_slug`、`ctx.investigation_id` |

### 3.3 什麼算失敗〔查證 + 施工〕

現在沒有一個統一的失敗旗標〔查證〕,失敗有好幾種長相;都算失敗:

- 工具丟例外,或 SDK 把例外變成的字串(`"An error occurred while running the tool"`,
  `agent/args_recovery.py` 已有這個常數)。
- 指令結束碼不是 0:套件指令與 `exec` 的回覆開頭是 `Tool \`name\` returned (exit_code=N)`
  (`agent/tools.py:_format_exec`)——包層讀這個開頭,不重寫格式。
- 回覆以 `error:` / `Error:` 開頭(大多數內建工具與規則給的工具的拒絕這樣寫;不這樣寫的——`kb_grep` 的額度用完——算成功,見 §7 A5)。
- 引數壞掉、包層根本沒呼叫到工具(`safer` 的幾個出口)也算一次失敗——模型確實叫了這個工具、沒做成。

### 3.4 誰、在哪個場合〔查證 + 施工〕

| 場合(`surface`) | 誰 | 建 context 的地方 |
|---|---|---|
| `chat` App 聊天 | 這則訊息的人(`speaker` / `acting_user`) | `build_chat_turn` |
| `kb_chat` KB 聊天 | 發問的人 | KB 聊天送出 |
| `workflow` workflow 步驟 | 啟動這次 run 的人(`captured_user`) | `build_workflow_turn`——**現在 context 沒帶人**〔查證〕,要接上 |
| `schedule` 排程自動跑 | 按過「用我的身分執行」的人;沒有就 item 擁有者〔施工〕 | 排程觸發的 run 往下帶 |
| `subagent` sub-agent | 父 turn 的人 | `subagent_run._child_context` 繼承後改場合 |
| `kb_agent` `ask_knowledge_base` 的子代理 | 父 turn 的人 | `answer_question` 的 context 現在**沒有人、沒有 item**〔查證〕,要從呼叫端帶進去 |
| `background` 背景工作(wiki 維護、卡片草稿、壓縮…) | 沒有人時記 `(系統)` | 各自的建構處 |

「沒有服務帳號」〔查證〕:排程那一輪是以 item 擁有者跑(`acting_user=owner`),綁定的人只決定工具拿誰的環境
變數(`env_user`)。統計記「綁定的人,沒有就擁有者」——管理者要找的是「這一輪替誰在跑」〔施工〕。

### 3.5 存法:每天每顆 pod 每個工具一列〔施工,照抄 skill hub〕

- 新 model `ToolUsageDay`:`day`、`pod`、`tool`、`source`,加上 `calls`、`fails`、`total_ms`,以及三個明細:
  `by_user`、`by_surface_app`、`by_item`(各自記 calls / fails / total_ms)。索引 `day`、`tool`。
- 包層只在**記憶體**累加;每 5 分鐘、以及 SIGTERM 時寫回(用 CAS 合併到同一列)。skill hub 用 2 小時,
  但管理者看的是「剛剛那個工具是不是一直壞」,5 分鐘的延遲比較合理;代價是每顆 pod 每 5 分鐘幾筆小寫入。
  pod 沒收到 SIGTERM 就死掉時,最後不到 5 分鐘的計數會掉——統計用途可接受,文件寫明。
- 列數有上界:工具數 × pod 數 × 90 天;明細放在列裡,不另開列。
- **保留 90 天(D5)**:超過的列刪掉。照 #804 的規矩——這是對一張會長大的表做事,但只按有索引的 `day` 刪,
  一天一次、`ScanLease` 讓一顆 pod 做,不讀整張表。

### 3.6 API(只給 superuser)〔施工〕

- `GET /admin/tool-usage?days=30&source=&surface=&app=` → 每個工具一列:來源、呼叫、失敗、平均耗時。
- `GET /admin/tool-usage/{tool}?days=30` → 這個工具依**人**、依**場合 × App**、依 **item** 的分項。
- `days` 只接受 7/30/90(D5)。非 superuser 403。

### 3.7 畫面(Diagnostics 頁的新分頁「工具使用」)〔指南〕

- **分頁**:Diagnostics 頁本來就是分頁條(`DiagnosticsPage.tsx` 的 `kb-tabs`),新的一頁照用;切換面板用分頁
  (CLAUDE.md 的規則,NN/g〈Tabs, Used Right〉)。<https://www.nngroup.com/articles/tabs-used-right/>
- **時間範圍 7/30/90**:同一份內容的不同模式 → **segmented control**(Material 3〈Segmented buttons〉)。
  <https://m3.material.io/components/segmented-buttons/overview>
- **表格**:每列一個工具,欄位 工具/來源/呼叫/失敗/失敗率/平均耗時,可依欄排序、依來源/場合/App 篩選
  ——NN/g〈Data Tables: Four Major User Tasks〉(找特定列、比較、檢視/編輯一列、對列採取行動)。
  <https://www.nngroup.com/articles/data-tables/> 數字欄靠右對齊、同單位。
- **點進一個工具**:開一個面板列出依人、依場合 × App、依 item 的分項(D4、D7)。

## 4. 施工時我定的事〔施工〕

- 計數點:`_agent_for` 的包層;來源在合併前標。
- 失敗的四種判斷(3.3)。
- `surface` 七個值與各自的「誰」(3.4);排程記「綁定的人,沒有就擁有者」。
- 存法:每天每顆 pod 每工具一列、明細放列內、5 分鐘寫回、90 天刪除一天一次。
- API 路徑與篩選參數。

## 5. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃 | 文件測試、mkdocs `--strict` 綠 |
| P2 | 計數包層 + 來源標記 + 失敗判斷 + 耗時;記憶體累加器 | 每種來源、每種失敗長相、引數壞掉各有測試;串流與非串流 turn 都算到 |
| P3 | `surface` 與「誰」:每個建 context 的地方明確設定;workflow/排程/KB 子代理把人帶進去 | 每個場合走真的入口各一個測試,記到的場合與人正確 |
| P4 | `ToolUsageDay` model、寫回(5 分鐘 + SIGTERM、CAS 合併)、90 天刪除 | 多 pod 寫同一天不互蓋;刪除只動過期的列 |
| P5 | superuser API(總表 + 單一工具分項) | 非 superuser 403;`days` 只收 7/30/90 |
| P6 | Diagnostics「工具使用」分頁:表格、篩選、排序、7/30/90、點進分項 | 元件測試;版面在 1280/390 量過 |
| P7 | 文件:`docs/migrations.md`(新 model、新的背景寫回與刪除)、架構說明 | mkdocs `--strict` 綠 |
| P8 | 真瀏覽器檢查 + demo(MP4) | 截圖對照引用的指南 |

## 6. 不做

- 平台外 MCP 的使用(D1)。
- 失敗原因分類、存錯誤文字(D3 選 A,不選 C)。
- 保留天數做成設定(D5 選 A)。
- 工具作者、item 擁有者看自己的統計(D2 選 A;同一份資料,之後可加)。
- 每次呼叫一筆的明細紀錄:只存每天的彙總。

## 7. 施工後與計劃不同的地方(as-built,#932)

- **A1 寫回不用 CAS**:每個**行程**只寫自己的列(列 id 含「主機名-pid」,`writer_name()`),兩個行程永遠不寫同一列,
  所以不需要計劃 §3.5 說的「CAS 合併」;一個行程內一次只跑一個寫回(鎖)。不照 skill hub 用 `HOSTNAME`:k8s 以外
  它是 shell 變數、不在環境裡,同一台 VM 上的 API 與 worker 會都寫 `~local`、互相蓋掉計數(審查 r1 抓到)。
  寫回是同步函式——API 用 `to_thread` 叫,worker 用一條 thread(`run_flusher`)每 5 分鐘叫,**排空做完之後**
  再叫最後一次(它有自己的停止旗標;跟 SIGTERM 共用的話,最後一次會在排空期間跑,排空時做完的工作的計數就掉了——
  審查 r1)。worker 寫的是它那套組裝**自己的**計數器、走同一個 spec:`build_app` 組的 worker(blob-gc、
  chat-video)寫 app 的那一個,不另開一個空的。
- **A2 刪除是永久刪除**:specstar 的 `delete` 是軟刪除,列與所有版本都還在,表照樣長大;90 天的刪除用
  `permanently_delete`。
- **A3 排程怎麼認**:`WorkflowRun` 新增 `from_schedule`,由排程的**兩個**啟動口帶進 `orchestrator.start`:item 的
  排程(時間到與排程清單的「立即執行」都走 `start_page_schedule`)與 profile 的 `triggers.json`
  (`trigger_sweeper` → `build_trigger_start`;審查 r1 抓到原本漏了這個口);`drive_turn` 讀它決定場合是 `schedule` 還是
  `workflow`。「誰」:有綁定的人(`RunIdentity.env_user`)記綁定的人,沒有就記 run 的 `captured_user`(排程是 item
  擁有者,手動跑是啟動的人)。
- **A4 KB 子代理借呼叫端的人/App/item**:不從 `answer_question` 一路傳參數,改用 contextvar——計數層執行一個工具時
  記下這次呼叫的人、App、item,在它裡面跑的 turn(`ask_knowledge_base` 的 KB 子代理)缺哪個就借哪個。
- **A5 計數層在引數修復的外面**:引數壞掉、工具根本沒跑的呼叫也算一次失敗;舊名稱撞到兩個套件時的拒絕
  (`cross-package tool name collision:`,與訊息共用常數 `COLLISION`)也算(審查 r1)。`kb_grep` 的額度用完不以
  `error:` 開頭,算成功——那是「這輪不准再搜」的告知,不是工具壞了。
- **A6 天以 UTC 切**:列的 `day` 是 UTC 日期;7/30/90 天的視窗是「今天(UTC)往回 N 天,含今天」。
- **A7 `days` 自己檢查**:不用 `Literal[7, 30, 90]` 當查詢參數型別——FastAPI 不會把查詢字串轉成整數字面值,7 和 90
  會一律被拒(測試抓到);改收整數再檢查,不合法回 422。
- **A8 Diagnostics 的分頁條改成共用 `Tabs`**(童子軍):原本是一排有底色的按鈕,讀起來像動作;順手換成文字＋底線的
  分頁條(CLAUDE.md 的規則)。
- **A9 分項表的 App 顯示名稱**:依「場合 × App」那張表用 App 的標題(`/apps` 清單),不是 slug;item 附標題與連結,
  item 已不存在就只顯示 id、不給連結。
- **A10 90 天的刪除沒有 `ScanLease`**:每個行程在每個 UTC 日的第一次寫回時刪一次。只按有索引的 `day` 讀過期的列,
  一天最多一次,讀的量是「昨天過期的那幾列」——多個行程一起刪,最壞是同一批列被刪兩次(第二次找不到,沒事)。
  計劃 §3.5 的租約省下的只有這點,不值得多一張表的寫入。
- **A11 wiki 的 turn 算在發問的那一輪**:wiki reader(`ask_wiki`)與 maintainer 的 context 用 collection id 當
  `investigation_id`(wiki store 這樣存),所以標 `usage_inside_caller`:它的呼叫記在呼叫端的 item 與場合;沒有
  呼叫端(wiki job)就是 `background`、沒有 item——不會把 collection id 當成 item(審查 r1)。只有這兩處把非 item
  的 id 放進 `investigation_id`(grep 確認)。
- **A12 串流與非串流 turn 都算到**(P2 的完成條件):兩條路(`_run_once` / `_run_once_nonstream`)都經過
  `_agent_for`,包層在那裡套上,是結構上成立;測試直接打 `_agent_for` 交出的工具。
