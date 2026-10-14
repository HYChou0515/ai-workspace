# 工具使用統計:管理者看 AI 用了哪些工具、誰用、在哪、成不成功、多快

**狀態:** P1–P16 完成(#932,同一個 PR;skill §8、選擇與顆粒度與原始資料 §9;每一段都有真瀏覽器檢查與 demo)。P11 的「工具/Skill」分段控制被 D12 推翻(§8.5 S6),P13 起的版面以 §8.4、§9 為準。as-built 見
§7、§8.5、§9.4。
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
| D8 | **skill 也要像工具一樣記**(§8),同一個 PR 完成 | 〔user〕「我希望skill也能像mcp一樣紀錄」「同一個pr完成即可」 |
| D9 | 一次 skill 使用 = AI 用 `read_skill` 讀到內容,**或**使用者在選單套用(內容預先載入);兩種合成一個次數,不分開 | 〔user〕選「兩種都算、不分開」 |
| D10 | **所有來源的 skill 都算**,每筆標來源(平台內建/App 內建/skill hub/自己寫的) | 〔user〕選「全部、標來源」 |
| D11 | 放在**「工具使用」同一頁**,只給 superuser;**只算成功讀到的**,沒有失敗、沒有耗時 | 〔user〕選「放進同一頁」「只算成功讀到的」 |
| D12 | 工具與 skill **直接攤在頁面上,放表格和圖表**,不用切換;圖表**不引入 ECharts**,但要畫得像圖表 | 〔user〕「直接放表格和圖表吧 你看uiux guidance」;「不要好了 但你至少不用echart也畫像一點」;「X軸scale要相同」 |
| D13 | 工具區**能選要顯示哪些工具／command** | 〔user〕「要能選顯示哪些tool,command」 |
| D14 | 工具區**能選顯示的顆粒度:每個 command,或依套件合併** | 〔user〕「要能選要顯示顆粒度」→ 選「工具顆粒度(套件/command)」 |
| D15 | 工具與 skill **都能下載原始資料** | 〔user〕「兩個都要能下載raw data」 |

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
- 回覆以 `error:` / `Error:` 開頭(大多數內建工具與規則給的工具的拒絕這樣寫;不這樣寫的——`kb_search` / `kb_grep` / `search_wiki` / `ask_wiki` 的額度用完——算成功,見 §7 A5)。
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
| `page` 頁面(#942:WUI 頁面 `callAgent` / `startAgent` 叫的 subagent) | 點的人(頁面工作的 `env_user`) | `WorkflowExecutor._usage_identity`(run 有 `page_job`);`_child_context` 標的 `subagent` 改回 `page` |
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
- `surface` 七個值與各自的「誰」(3.4;#942 加了第八個 `page`);排程記「綁定的人,沒有就擁有者」。
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
| P9 | skill 的計數:`read_skill` 成功讀到的三條路 + 套用預先載入,來源依 §8.2 判定,記在同一個記憶體累加器 | 每個來源、兩個入口各有測試;讀不到的不算 |
| P10 | 存法與 API 加 `kind`(tool/skill);skill hub 的寫入者名稱改成每個行程一個(童子軍) | 兩種 kind 不混列;舊 API 參數不給 kind 時照舊是工具 |
| P11 | 畫面(**後來被 D12 推翻,見 §8.5 S6**):「工具使用」頁加「工具/Skill」分段控制;skill 表只有名稱/來源/次數,點進看分項 | 元件測試;版面在 1280/390 量過 |
| P12 | 文件:migrations 的 #932 條目補上 skill;§7 as-built | mkdocs `--strict` 綠 |
| P13 | 真瀏覽器檢查 + demo(MP4)涵蓋 skill;實際做的是 D12 的一頁兩區、CSS 長條圖與分頁改名 | 截圖對照引用的指南 |
| P14 | 記下每次呼叫的套件;API 加 `group=command\|package`(總表與單一分項都可);原始資料 CSV 匯出 | 同套件的 command 合成一列;內建不併;舊參數照舊;CSV 加總得回報表的數字 |
| P15 | 工具區:顆粒度分段控制 + 多選要顯示的工具/套件,圖與表都照選擇 | 元件測試;突變探針;1280/390 量過 |
| P16 | 文件、真瀏覽器檢查、demo(MP4) | mkdocs `--strict` 綠;截圖對照指南 |

## 6. 不做

- 平台外 MCP 的使用(D1)。
- 失敗原因分類、存錯誤文字(D3 選 A,不選 C)。
- 保留天數做成設定(D5 選 A)。
- 工具作者、item 擁有者看自己的統計(D2 選 A;同一份資料,之後可加)。
- 每次呼叫一筆的明細紀錄:只存每天的彙總。
- skill 讀不到(名字不存在、被關掉、太大)不算(D11);那些是 `read_skill` 這個工具的失敗,工具統計裡已有。
- skill hub 面板上的「使用 N 次」不改:它仍只算 `read_skill` 讀到的 hub 條目,套用不算,誰用也不顯示(U5)。兩邊
  數字因此可能不同——hub 面板答的是「這個條目多常被 AI 讀」,這裡答的是「這個 skill 多常被載入」。

## 7. 施工後與計劃不同的地方(as-built,#932)

- **A1 寫回不用 CAS**:每個**行程**只寫自己的列(列 id 含「主機名-pid」,`writer_name()`),兩個行程永遠不寫同一列,
  所以不需要計劃 §3.5 說的「CAS 合併」;一個行程內一次只跑一個寫回(鎖)。不照 skill hub 用 `HOSTNAME`:在 VM／一般 shell
  它是 shell 變數、不在環境裡(k8s 與 docker 會設),同一台 VM 上的 API 與 worker 會都寫 `~local`、互相蓋掉計數(審查 r1 抓到)。
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
  (`cross-package tool name collision:`,與訊息共用常數 `COLLISION`)也算(審查 r1),而且記在**模型叫的那個舊名稱**
  下——撞名的呼叫會被導到第一個候選工具,但那個工具沒跑,不該替它記一次失敗(審查 r2)。`kb_search` / `kb_grep` /
  `search_wiki` / `ask_wiki` 的額度用完不以 `error:` 開頭,算成功——那是「這輪不准再搜」的告知,不是工具壞了。
- **A6 天以 UTC 切**:列的 `day` 是 UTC 日期;7/30/90 天的視窗是「今天(UTC)往回 N 天,含今天」。
- **A7 `days` 自己檢查**:不用 `Literal[7, 30, 90]` 當查詢參數型別——FastAPI 不會把查詢字串轉成整數字面值,7 和 90
  會一律被拒(測試抓到);改收整數再檢查,不合法回 422。
- **A8 Diagnostics 的分頁條改成共用 `Tabs`**(童子軍):原本是一排有底色的按鈕,讀起來像動作;順手換成文字＋底線的
  分頁條(CLAUDE.md 的規則)。
- **A9 分項表的 App 顯示名稱**:依「場合 × App」那張表用 App 的標題(`/apps` 清單),不是 slug;item 附標題與連結,
  item 已不存在就只顯示 id、不給連結。
- **A10 90 天的刪除沒有 `ScanLease`**:每個行程在每個 UTC 日的第一次寫回時刪一次。只按有索引的 `day` 讀過期的列,
  一天最多一次,讀的量是「昨天過期的那幾列」——多個行程一起刪時,慢的那個刪到已被刪掉的列會找不到,這一列略過、
  繼續刪其餘的(審查 r2 抓到原本會丟例外、放棄整批)。
  計劃 §3.5 的租約省下的只有這點,不值得多一張表的寫入。
- **A11 wiki 的 turn 算在發問的那一輪**:wiki reader(`ask_wiki`)與 maintainer 的 context 用 collection id 當
  `investigation_id`(wiki store 這樣存),所以標 `usage_inside_caller`:它的呼叫記在呼叫端的 item 與場合;沒有
  呼叫端(wiki job)就是 `background`、沒有 item——不會把 collection id 當成 item(審查 r1)。只有這兩處把非 item
  的 id 放進 `investigation_id`(grep 確認)。
- **A12 串流與非串流 turn 都算到**(P2 的完成條件):兩條路(`_run_once` / `_run_once_nonstream`)都經過
  `_agent_for`,包層在那裡套上,是結構上成立;測試直接打 `_agent_for` 交出的工具。

## 8. Skill 使用(D8–D11)

### 8.1 兩個入口〔查證〕

- **AI 讀**:`agent/tools.py:read_skill_impl`。讀到內容的只有三條 return:工作區的 skill
  (`read_workspace_skill` 之後)、平台內建的(`load_shared_skill`)、App profile 的(`load_skill`);其他
  return 都是 `error:` 字串,不算(D11)。
- **使用者套用**:聊天送出帶 `apply_skills`,`api/chat_send.py` 用 `apps/skills.py:build_applied_skills_block`
  →`resolve_skill_body` 把內容放進這一輪(不經過任何工具)。只有聊天會套用,workflow 不會。
- 同一輪先套用、AI 又 `read_skill` 同一個 skill,算**兩次**——兩次都把內容載入了(D9 的「一次」是一次載入)。

### 8.2 來源怎麼判〔查證 + 施工〕

skill 的「來源」記成四個值,依讀到內容的那條路與工作區副本的 `.origin`(`SkillOrigin.source`):

| 值 | 畫面 | 怎麼判 |
|---|---|---|
| `shared` | 平台內建 | `load_shared_skill` 讀到的;或工作區副本 `.origin.source == "shared"`(帶 scripts 的內建 skill 第一次讀會被複製進工作區) |
| `profile` | App 內建 | `load_skill` 讀到的;或副本 `.origin.source == "profile"` |
| `hub` | skill hub | 副本 `.origin.source == "hub"` 且不是 fork(含作者發佈後自己的那份——它的 `.origin` 跟安裝的一樣) |
| `workspace` | item 內自建 | 工作區有、沒有 `.origin`(或讀不出來);或 hub 的 fork(fork 之後就是自己的) |

### 8.3 記在哪〔施工〕

- 同一個記憶體累加器、同一張表,多一個欄位 `kind`(`tool` / `skill`),列 id 也帶 `kind`——工具與 skill 同名
  也不會混在一列。skill 的列 `fails` 與 `total_ms` 都是 0。#932 還沒合併,這張表在 prod 還沒有任何列,加欄位
  不用回填。
- 誰、App、item、場合:跟工具一樣取這一輪的(`_attribution`)。套用發生在 runner 開始之前,所以這一輪的
  context 由建它的地方就帶上累加器(runner 本來就保留 context 已有的累加器)。
- API:兩條路由加 `kind=tool|skill`(預設 `tool`,舊的呼叫不變)。
- skill hub 的計數器(`apps/skill_hub_usage.py`)用 `HOSTNAME` 當列的寫入者,跟 §7 A1 是同一個坑;改成同一個
  「主機名-pid」函式(童子軍)。它的總數是各列加總,換名字只是多新列,舊列照算。

### 8.4 畫面〔user + 指南〕

- **一頁兩區,不切換**(D12):最上面是共用的時間(7/30/90)、場合、App;下面「工具」一區、「Skill」一區,
  各有自己的來源篩選、圖表、表格與點開的分項。分頁的名稱改成「使用統計」。
- **圖表是橫條圖**:比較各類別的數量用長條圖,標籤長時用橫的,名稱不必縮寫或轉向——NN/g〈Choosing Chart
  Types〉 <https://www.nngroup.com/articles/choosing-chart-types/>。大小只用**長度**表現、所有條同一個顏色
  (顏色不能帶數量),不用圓餅——NN/g〈Dashboards: Making Charts and Graphs Easier to Understand〉
  <https://www.nngroup.com/articles/dashboards-preattentive/>。
  - 工具:「呼叫次數」與「失敗次數」**兩張並排**,不疊成一張堆疊長條——NN/g〈Choosing Chart Types〉說堆疊長條
    「very hard to understand」,建議改成幾張並排。沒有任何失敗時不畫失敗那張。Skill:「載入次數」一張。
  - 每張最多前 10 名,由大到小;表格保留全部。
  - **同一區的圖用同一條 X 軸**(D12「X軸scale要相同」):失敗次數畫在呼叫次數的軸上(軸的終點由該區最大的
    呼叫次數決定;失敗不會多過呼叫),兩張並排才能比——各用各的軸時,失敗 2 次也畫滿整條。Skill 區是另一種量,
    用自己的軸。
  - 畫得像圖表(D12):數值軸從 0 到下一個整數刻度(1、2、5 × 10ⁿ 的步長、約四格,計數不切到 1 以下),
    0 的基線、每個刻度一條垂直格線、軸下標刻度值,條長 = 值 / 軸的終點。不引入圖表函式庫:這些都是 HTML／CSS。
  - 時間趨勢(折線圖,NN/g〈Choosing Chart Types〉:看趨勢用折線)這次不做——API 只有區間總數,沒有逐日的序列。
- **表格保留**,精確數字、排序、點開分項靠它(NN/g〈Data Tables〉)。skill 表欄位:Skill/來源/次數(D11
  沒有失敗與耗時)。點一個 skill 看依人、依場合 × App、依 item 的次數。

### 8.5 施工後與 §8 不同的地方(as-built)

- **S1 套用的那一次靠建 context 的地方帶上累加器**:聊天的 turn context 由 `TurnContextBuilder` 建時就帶上 app 的
  累加器(`create_app` 傳進去),送出路徑在 runner 開始前用它記套用;runner 本來就保留 context 已有的累加器,所以
  工具呼叫還是進同一個。
- **S2 來源從同一個解析函式出來**:`apps/skills.py:resolve_skill` 回傳(內容, 來源),套用的預先載入用它;`read_skill`
  的三條成功路徑各自標來源,工作區副本用同一個 `usage_source(.origin)`。原本的 `load_workspace_skill` 已沒有呼叫者
  (改用一次讀到內容與 `.origin` 的 `read_workspace_skill`),刪掉。
- **S3 寫入者名稱只有一份**:`writer_name()` 搬到 `apps/skill_hub_usage.py`(取代 `pod_name()`),skill hub 與工具統計
  共用;`api/tool_usage_store.py` 轉出口同一個函式。
- **S4 列 id 帶 `kind`**:工具與 skill 的來源詞彙不重疊,單靠來源也分得開;`kind` 放進列 id 是讓「同名」這件事不依賴
  兩份詞彙永遠不撞。
- **S5 分頁名稱**:「工具使用」改成「使用統計」——先試的「工具與 Skill」在 390 寬把整條分頁擠到換行(截圖抓到;NN/g〈Tabs, Used Right〉要標籤短),「使用統計」跟原本一樣四個字、一行放得下。
- **S6 先做成「工具／Skill」分段控制,被推翻**(D12):兩種東西藏在切換後面,一次只看得到一種。改成一頁兩區、
  各自的圖表與表格;第一版的橫條只有條與數字,user 要「畫像一點」,補上數值軸、格線與基線。曾試著改用 ECharts
  (`view-plugins/chart` 用的那套),user 說不要——ECharts 也刻意不進 SPA 的主 bundle。

## 9. 選要顯示的工具與顆粒度(D13、D14)

### 9.1 顆粒度:每個 command,或依套件〔user + 施工〕

- 計數時多記一個 `package`:第一方/第三方套件的 command 記它的套件名稱(`PackageInfo.name`,在 `_agent_for`
  標來源的同一處查),內建工具是空字串。`ToolUsageDay.package` 加索引;#932 還沒合併,不用回填。
- API 兩條路由加 `group=command|package`(預設 `command`,舊的呼叫不變)。`package` 時同一個套件的列加總成一列,
  名稱是套件名;**內建工具沒有套件,維持一個一個**〔施工〕。點開一個套件,分項是那個套件所有 command 的加總。
- 畫面:工具區加「每個 command / 依套件」分段控制——同一份內容的兩種看法 = 切換檢視,Material 3〈Segmented
  buttons〉。Skill 沒有套件,沒有這個控制。

### 9.2 選要顯示的〔user + 指南〕

- 工具區加一個**「顯示哪些」選擇器**;全部勾 = 全部顯示(預設)。勾選結果**圖與表都照著顯示**——選擇是「我要看
  哪些」,不是只換圖。
- 形式照 NN/g〈Listboxes vs. Dropdown Lists〉<https://www.nngroup.com/articles/listbox-dropdown/>:**下拉不支援
  多選**,多選用帶核取方塊的清單;清單會捲動時,**選到的要在清單外看得到**。一開始想用 Mantine `MultiSelect`(下拉
  + 標籤),查了這篇才改;第一版把方塊全部攤開不捲動,user 指出「近百個 command 會出問題」,改成:
  - 平常**收成一行**,寫著現在顯示什麼(「顯示哪些:全部(96)」/「已選 5 / 96」),按了展開(progressive
    disclosure)。
  - 展開後有**搜尋框**(打字篩選)、「全部」「清除」,方塊**依套件分組**(內建在最前),整塊固定高度在框內捲動。
  - 選到的子集合在清單**外面**列成可移除的標籤;超過 20 個就只寫數量,不列名字。
  - 用 96 個 command(8 個套件)量過:收起 33px,展開整塊約 300px、清單框 224px 內捲動;1280/390 都沒有橫向溢出;
    圖照舊只畫前 10。
- 報表的每一列多回 `package`,選擇器才能分組。
- 切換顆粒度時回到全部顯示:command 與套件是兩組不同的名字〔施工〕。
- 數字為 0 的不會出現在清單裡(那段期間沒被叫過,本來就不在報表上)。

### 9.3 下載原始資料(D15)〔施工〕

- `GET /admin/tool-usage.csv?days=7|30|90&kind=tool|skill`,只給 superuser。路徑不放在 `/admin/tool-usage/` 底下:
  `/admin/tool-usage/{tool}` 會把 `export` 之類的名字當成一個工具。
- 「原始」= 存下來的每一格,不做報表的合併:各行程同一天的列先加總(行程名稱是內部細節),然後**長格式**一格一列——
  `day, kind, name, package, source, dimension, user, surface, app, item, calls, fails, total_ms`;`dimension`
  是 `total`(那天那個工具的總數)、`user`、`surface_app`、`item` 之一,只填它那一欄(`surface_app` 填 `surface` 與 `app` 兩欄)。依人、依場合、依 item 是三份
  各自的加總,不是交叉表(存法本來就只存這三個邊際,§3.5),所以不給「某人在某 item 用了幾次」這種格子。
- UTF-8 加 BOM:Excel 直接開才不會把中文顯示成亂碼。檔名 `tool-usage-<kind>-<days>d-<今天>.csv`。
- 畫面:每區各一個「下載原始資料(CSV)」——是下載檔案,所以是連結(`<a download>`),不是按鈕。下載的是目前的時間
  範圍;來源、場合、App、顯示哪些的篩選不套用——原始資料就是全部。

### 9.4 施工後與 §9 不同的地方(as-built)

- **T1 一個名字可以有兩個來源**:demo 的資料一做出來就碰到——把 `line-report` 發佈到 skill hub、裝進另一個 item,
  同一個名字就有「自己寫的」與「skill hub」兩列。點開的分項原本只用名字查,會把兩個來源加在一起;圖上也會有兩條
  一樣的 `line-report`。改成:分項路由多收 `source`,點的是哪一列就只看那個來源;同名出現兩次時,圖上的名字後面
  加上來源。
- **T2 圖與表跟著勾選走,軸也是**:共用的 X 軸(D12)由「勾選後還顯示的」最大呼叫次數決定,取消勾選大的,軸會跟著
  縮——看的是選到的那幾個彼此比較。
- **T3 列 id 帶套件**(審查 r3):兩個 App 各選了一個有同名 command 的套件時(衝突檢查是每個 App 自己的),
  同一個工具名有兩個套件;列 id 原本沒有套件,兩邊的次數會加進同一列、記在先寫的那個套件下。現在列 id、圖的名字
  (同名時加套件)、選擇器、標籤與點開的分項(`?package=`)都以「名稱 + 來源 + 套件」分開。
- **T4 原始資料不交出公式**(審查 r3):skill 名稱是使用者取的(`.skill/<名稱>/`),開頭是 `=`、`+`、`-`、`@`、
  tab 或 CR 時,CSV 的那一格前面加 `'`——OWASP〈CSV Injection〉的做法,Excel 直接開也只當文字。
- **T5 點開的分項跟著顯示走**:列被篩選或選擇器拿掉時,它的分項不再顯示。
- **T6 選擇器改成 tool modal 那一款**(user「畫面請參考tool modal那樣的選單」):不再攤在頁面上,而是一行摘要 +
  一個 modal——搜尋、每個套件一個摺疊列(核心工具在前)、摺疊列的方框整包勾/清(部分勾 = 混合)、取消/套用;
  改的是草稿,套用才換,改過再取消會問一次(`useDirtyClose`)。已選的子集合照舊在 modal 外列成標籤。
- **T7「自己寫的」改名「item 內自建」**(user 同意改名):它指的是存在這個 item 工作區、沒有來源標記(或標記讀不出來)的
  skill,以及 hub 的 fork(fork 之後算自己的);不一定是看報表的人寫的。
- **T8 點開的詳細內容改成側邊面板**(user「詳細內容那邊不清楚」):原本接在整張表下面、只有名字當標題、三張「依…」表。
  照 NN/g〈Data Tables: Four Major User Tasks〉看單一列的建議——modal 會蓋住要對照的列、在原地展開會越堆越亂,
  非 modal 的側邊面板只蓋住一部分、表格照樣能點——改成右側面板(手機上整寬),點另一列就換內容,✕ 或 Escape 關。
  內容先講是什麼與總數(「工具 · 內建 · 最近 30 天」、「呼叫 7 次 · 失敗 2 次(29%) · 平均 31 毫秒」),再分「誰在用」
  「在哪裡用(場合與 App)」「在哪些 item」;表的第一欄寫那欄是什麼(人/場合 · App/Item)而不是重複標題;次數附上
  跟頁面圖表一樣的長條;兩個同名 item 會各自標出 App 與 id 的前 8 碼。
- **T9 側邊面板,審查 r5 修的**:整頁只有**一個**面板(原本工具區與 Skill 區各開一個,會疊在一起);摘要的總數取自
  分項本身(「誰在用」加起來就是全部),不再取表格那一列——表格那一列吃了頁面的篩選、換時間也不會跟著變,兩者會對不上;
  分項是三份各自的加總、不是交叉表,沒辦法依場合/App 篩,所以頁面有場合或 App 篩選時面板寫明「不受上方篩選」;
  關閉後鍵盤回到開面板的那一列;Escape 在焦點在面板裡時有效。寬螢幕(≥1100px)面板開著時頁面往左讓出面板的寬度,
  來源篩選、CSV 連結與表格右邊幾欄不會被蓋住(1280/1440 量過);≤420px 時面板整寬,421–1099px 時是疊在頁面上
  的 420px 面板(不讓位)。面板頂端用 `--nav-height`
  (GlobalNav 也改用它),層級用 `--z-rail`。
- **T10 審查 r6**:摘要的總數改由分項回應直接帶(`calls`、`fails`、`avg_ms`,伺服器用未四捨五入的總和算)——用各人
  已四捨五入的平均再平均一次會差 1;讀取中寫「讀取中…」(`aria-busy`);換時間時報表保留舊資料直到新的到(列與面板
  不會拆掉重建),面板只在**有人點了一列**時才把鍵盤移到標題,關閉時開面板的那顆按鈕若已重建,就用列的 key 找回同一列。
- **T11 詳細內容的橫條**(user「橫條應該要至左對齊,放在最右側固定欄寬,數字和橫條住一起」):次數改放每張表**最右邊、
  固定寬(140px,手機 84px)的一欄**,橫條從那一欄的左邊開始(每列起點同一個 x),數字跟著橫條——長條(≥ 最大值的
  60%)放在條尾內側、淺色字,短條放在條尾外側。
- **T12 審查 r7**:換時間範圍時保留舊資料,但**換顆粒度時不保留**——套件報表還在讀時留著 command 的列,點下去會開出
  全是 0 的分項;短條外側的數字不會超出欄寬(長數字往回落在條上)。條內淺色數字(`--text-dark` 在 `--accent-h` 上)
  對比 3.76:1:過 repo 的 3:1 下限(`ganttBarContrast.test`),沒到 WCAG AA 一般字的 4.5:1;這個配色上沒有哪個字色
  到 4.5(深色 4.0、白 4.43)。照 user「數字和橫條住一起」保留在條內,要到 AA 就得把數字一律放條外。
