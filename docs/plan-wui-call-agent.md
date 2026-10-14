# WUI 頁面叫能力的兩種叫法:`call`(拿結果)與 `start`(拿工作);subagent 也能叫

**狀態:** P1(計劃,#942 已合)。P2–P6 實作於 PR #945(實作註記見 §6),統計場合「頁面」也在 #945(接在 #932 之上);P7 真瀏覽器檢查與 demo 做完(PR #945)。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master`(2a4e7d0e)程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻。

## 1. 問題

user:「我希望wui要能夠呼叫subagent」;討論中擴大成「每個能力都有兩種叫法,讓 AI 自己決定」。

現況〔查證〕:

- 頁面能用的動詞是封閉的(`web/src/renderers/wui/protocol.ts`、`bridge.ts`):讀寫檔案、`callTool`(套件工具,
  一個請求一個 JSON 回應、不串流,沙盒每次 ≤ 60 秒,`api/wui_routes.py` `wui_call_tool`)、`startRun`(起一個
  workflow run、`onEvent` 收串流事件,`wui_run`)。**沒有任何叫 AI 的動詞**;`docs/plan-wui.md`「Deliberately
  not built」把「從頁面問 agent」列為刻意不做。
- `startRun` 的串流**沒有結果**:只有過程事件,run 結束時連線就斷,頁面也拿不到 run 編號去查結果
  (`turns.subscribe_sse`、`workflow_exec.release`)。
- subagent = item 的 `.agent/<名稱>/AGENT.md`(或 App profile 內建),由 `run_agent` 叫:空白上下文、只回最後的
  文字、不能再往下叫(`api/subagent_run.py`、`apps/subagents.py`)。
- 頁面的 `startRun` 每次開一個新對話(`locator.open_run_chat`),「同時一個 run」是**每個對話**一個
  (`orchestrator.start` 查 `active_run_for_chat`),所以頁面起的 run 本來就能並行。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 頁面能**直接叫 subagent**,不用先寫 workflow | 〔user〕「我希望wui要能夠呼叫subagent」;選「頁面直接叫」 |
| D2 | 只能叫**頁面宣告過的**:頁面 yaml `agents: [名稱…]`,而且這個 item 真的有(item 的 `.agent/` 或 App 內建) | 〔user〕選「頁面宣告過的」 |
| D3 | subagent 的工具權限**跟在聊天裡一樣**:它 AGENT.md 列的工具,上限是 item 的 App 設定,扣掉 subagent 本來就不能用的;點的人要有 `execute` 權限 | 〔user〕選「跟在聊天裡一樣」 |
| D4 | **文字進、文字出**:輸入一段文字,結果是回答文字;寫的檔案留在 item 裡;要結構化就在 AGENT.md 叫它回 JSON | 〔user〕選「文字進、文字出」 |
| D5 | **留紀錄**:每個 `start` 出來的工作、每次叫 subagent,在 item 的 run 清單多一筆,看得到完整過程、能從那裡停止 | 〔user〕選「留,跟 workflow run 一樣」 |
| D6 | **可以並行**;同一個 item 同時進行中的頁面工作最多 **4 個**(設定值) | 〔user〕選「可以並行」;「4個ok」 |
| D7 | **跟工具 API 同一個形狀**:subagent 包成 `POST …/wui/agents/{名稱}/call`,跟 `…/wui/tools/{名稱}/call` 並排;請求 `{input}` 對應 `{args}`、結果 `{output}` 對應 `{output}` | 〔user〕「我們是讓mcp被包成api subagent也應如此」 |
| D8 | **用動詞名稱區分「多久回來」**:`call…` = `await` 拿結果;`start…` = 拿一個**工作**(`result()` 等結果、`status()` 看進度、`stop()` 停止)。**沒有回呼參數**——回呼與非同步函式容易被 AI 用錯 | 〔user〕「我不喜歡你的解法 這樣ai可能會錯用async func」;「在函數命名上區分」 |
| D9 | **每個能力都有兩種叫法,由 AI 自己決定**用哪一種;能力不宣告「通常多久」,平台不替能力決定 | 〔user〕「每個mcp都能有async call 和sync call」「讓ai自己決定」;推翻我先前「平台寫死工具=同步、subagent=非同步」的方案(「誰決定要怎麼包裝各個mcp, 現在看起來你打算hardcoded」) |
| D10 | **不影響現有 WUI**:`callTool`、`startRun` 的行為與回傳形狀完全不變;只**新增**動詞與路由 | 〔user〕「注意不要影響現有wui」 |
| D11 | 身分:算在 item 擁有者頭上,私人環境值用**點的人**的——照 `plan-wui-viewer-login.md` 的「誰的 private」表,與頁面的 `startRun` 相同 | 〔施工,沿用既有決定〕 |

## 3. 機制〔施工〕

### 3.1 頁面看到的動詞(D8、D9、D10)

| 能力 | `call…`(拿結果) | `start…`(拿工作) |
|---|---|---|
| 工具 | `callTool(名稱, 參數) → output`(**既有,不變**) | `startTool(名稱, 參數) → 工作`(新) |
| subagent | `callAgent(名稱, 輸入) → output`(新) | `startAgent(名稱, 輸入) → 工作`(新) |
| workflow | — | `startRun(…)`(**既有,不變**:還是 `onEvent` 串流;D10) |

- `call…`:`await` 之後就是結果;**最多等 60 秒**(跟工具一樣的上限)。超過時回一句錯誤「跑太久,請改用
  `start…`」,並停掉那次工作,不留半套在跑。
- 工作(`start…` 的回傳):
  - `await job.result()`:等它結束,拿 `output`;失敗或被停止時拋出一句說明。
  - `await job.status()`:`{state: running|done|failed|stopped, output?, error?}`,頁面要畫進度就自己定時問。
  - `await job.stop()`:停止。
  - 沒有回呼。工作的完整過程在 run 紀錄裡看(D5)。
- 給 AI 的規則(寫進 `sample-skills/wui`):**要馬上拿結果用 `call`;可能跑久就用 `start` 拿工作;`call` 超時
  就改用 `start`。** 能叫的工具與 subagent 名稱,照現有做法在 AI 讀 skill 時一個個列出(`_wui_callable`)。

### 3.2 後端路由(全部新增;D10)

| 路由 | 用途 |
|---|---|
| `POST /a/{slug}/items/{item_id}/wui/agents/{name}/call` `{input}` → `{output, failed}` | `callAgent`(D7);最多等 60 秒 |
| `POST /a/{slug}/items/{item_id}/wui/jobs` `{kind: tool\|agent, name, input}` → `{job_id}` | `startTool` / `startAgent` |
| `GET  /a/{slug}/items/{item_id}/wui/jobs/{job_id}` → `{state, output?, error?}` | `job.status()` / `job.result()` |
| `POST /a/{slug}/items/{item_id}/wui/jobs/{job_id}/stop` | `job.stop()` |

- 權限:叫 subagent 與起工作都要 `execute`(D3,同 `startRun`);工具的 `call` 照舊 `edit_content`。
- 名稱檢查:工具用 `wui_call_tool` 同一套(App 上限、item 的開關);subagent 要在這個 item 的定義裡,沒有 → 404
  並列出有哪些。頁面 yaml 的 `agents:`、`tools:` 由前端 bridge 擋(跟 `startRun` 擋 `workflows:` 同一層)。
- 同時上限(D6):同一個 item 進行中的頁面工作(含 `callAgent` 進行中的)最多 4 個,超過回 429「同時太多」。
  新設定值,記 migrations。

### 3.3 一個工作 = 一筆 run(D5)

- 沿用 workflow run 的紀錄、run 對話、停止、run 清單:每個工作開自己的對話(所以能並行),存一筆 run,標成
  「頁面工作」並記下種類(工具/subagent)、名稱、輸入;run 清單顯示成「頁面:subagent Y」/「頁面:工具 Z」,
  點進去看得到過程,停止鍵照舊能用。run 的 `result` 存 `{output}`,`GET …/jobs/{id}` 讀它。
- 跑 subagent:用 `workflow_exec.drive_turn` 同一條路跑一個 turn(配額、對話持久化、取消),但系統提示是
  subagent 的 AGENT.md、工具是它的 `allowed_tools` ∩ App 上限 − `SUBAGENT_FORBIDDEN_TOOLS`——把 `_child_context`
  的收窄抽成兩邊共用的一個函式,不各寫一份。回答 = 最後的非 reasoning 文字(同 `drive_subagent`);失敗或沒寫
  回答時,跟 `run_agent_task` 一樣給一句說明。
- 跑工具:在 item 的沙盒跑那個 command(同 `wui_call_tool` 的環境值),但不受 60 秒限制;工作本身有一個總時間
  上限(設定值),到了就停並標成失敗。
- `callAgent`:等於「起一個 subagent 工作,最多等 60 秒拿結果」;超時就停掉那個工作(3.1)。

### 3.4 統計

工具使用統計(#932)把頁面工作裡的 turn 記成場合「頁面」;人 = 點的人(D11)。

## 4. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃;`plan-wui.md` 標註被推翻的那一條 | 文件測試、mkdocs `--strict` 綠 |
| P2 | 共用的工作機制:run 紀錄種類、`jobs` 三條路由、同時上限、停止 | 每條決定一個走真入口的測試;突變探針 |
| P3 | subagent:共用收窄函式、subagent turn、`startAgent`、`callAgent`(60 秒、超時停工作) | 同上 |
| P4 | 工具:`startTool`(沙盒長時間執行、總時間上限) | 同上 |
| P5 | 前端:`callAgent`/`startAgent`/`startTool` 動詞與工作物件、bridge 擋 `agents:`/`tools:`;run 清單顯示頁面工作;**既有 `callTool`、`startRun` 的測試一個都不改** | 元件測試;動詞表守衛綠 |
| P6 | 文件(頁面作者 skill 的叫法規則、`docs/wui.md` 動詞表、migrations 條目)、統計場合 | mkdocs 綠 |
| P7 | 真瀏覽器檢查 + demo(MP4):頁面 `callAgent`、`startAgent` 兩個並行、`startTool`、run 清單、停止;既有頁面照常 | 截圖、demo |

## 5. 不做

- 改 `callTool`、`startRun`(D10)。`startRun` 不收進工作物件:舊的串流寫法與新的工作寫法會並存。
- 能力宣告「通常多久」(D9:AI 自己決定)。
- 回呼式的進度(D8)。
- 保證 JSON / schema 驗證(D4)。
- subagent 再叫 subagent(維持深度 1)。

## 6. 實作註記(PR #945)

做的時候定下、計劃裡沒寫到的事〔施工〕,以及它在哪:

- **頁面工作 = 一筆 `WorkflowRun`,欄位 `page_job`(種類、名稱、輸入、期限)**(`workflow/run.py`)。要跑的東西不是
  manifest,而是 API 在啟動時交給 orchestrator 的一個函式(`start_page_job`),所以只活在起它的那顆 pod 的記憶體裡、
  重啟後**不續跑**。紀錄上有期限;過了期限 + 60 秒還是進行中,就當成 pod 已經不在,讀的時候標成失敗、也不再佔同時名額
  (`page_job_state`、`_live_page_jobs`)。
- **`call…` 的等待是去讀 run 紀錄**,每 0.5 秒一次(跟 turn 的取消監看同一個節奏),最多 60 秒(`wui_routes.CALL_TIMEOUT_S`)——跨 pod 也成立。超時
  先停掉工作、再回 504,句子點名 `startAgent`。
- **三種叫法的結果同形**:`job.result()` 就是對應的 `call…` 會回的東西——工具是 `{output, exit_code}`(命令非零結束是
  結果,不是工作失敗,跟 `callTool` 一樣),subagent 是 `{output, failed}`。工作的 `failed` 狀態只用在平台層的失敗
  (例外、逾時、pod 中斷)。
- **subagent 的收窄沒有另外抽函式**:頁面路徑直接用聊天的 `subagent_run._child_context`(名稱沒改,因為 #932 也動到
  它),一個函式兩邊用。失敗/沒寫回答的句子抽成 `report_of`,`run_agent` 與頁面共用。工作的對話裡會先寫一則使用者訊息
  (頁面送的輸入),紀錄才看得到「問了什麼」。
- **工具工作走 `callTool` 同一扇門**(`_prepare_tool`,原封搬出):名稱檢查、拒絕的句子、環境值(含點的人那次請求的
  值)都一樣,在**啟動當下**就決定。命令的期限是工作剩下的時間(`exec_package_command(exec_timeout=)`,只有給值時才傳,
  其他呼叫者不變);沙盒的**閒置**上限(`log_timeout`)照舊。別顆 pod 按的停止只會推進那個對話的 turn epoch——turn
  自己會看到,命令沒有 turn,所以工具工作自己盯著 epoch。
- **權限**:三條新路由與 `startTool` 都要 `execute`(`callTool` 仍是 `edit_content`)。`jobStatus` / `stopJob` 不看頁面
  宣告(工作物件是唯一送它們的地方),後端只回答**這個 item 的頁面工作**,別的 item 或 workflow run 的 id 一律 404。
- **workflow 才有的操作擋掉頁面工作**:頁面工作的 `workflow_id` 是 "",而 manifest 查找把 "" 讀成 profile 的**第一個**
  workflow。所以 `steer` 回 409(`PageJobNotSteerable`)、`resume` 不接;前端頁面工作的對話不把輸入框變成「引導 run」,
  完成時也不顯示「未執行任何步驟」。失敗通知的標題寫「Page job failed: sub-agent “X”」,一天一則的分桶以工作名稱為準。
- **AI 讀 `wui` skill 時**,除了能叫的工具,也列出這個 item 能讓頁面問的 subagent——取自 turn 自己的 `subagent_defs`
  (聊天能委派的那一份)。兩邊用同一個函式(`_subagent_defs`)算,但輸入的設定不一定一樣:`callAgent` 用 item 解析出的設定(workflow 節點用的那份),聊天 turn 用它自己的(可能掛了別的 preset),所以兩份清單**可能**不同——頁面送出時以`callAgent` 那份為準,不在裡面就 404 並列出有哪些。
- **統計場合「頁面」**(接在 #932 之上):`_usage_identity` 看到 run 有 `page_job` 就記 `page`,人是點的人
  (`RunIdentities` 的 `env_user`)。`_child_context` 會把子 turn 標成 `subagent`——頁面工作沒有父 turn,所以收窄之後
  改回 `page`。**記到的是 subagent turn 裡的工具呼叫**;`startTool` 工作跟 `callTool` 一樣不是 agent 叫的工具,#932
  不記它們。
- **已知限制(沿用既有問題)**:item 的沙盒是冷的時候,第一件工作的 turn 會觸發還原;還原期間另一個請求讀 subagent 定義,可能讀到還沒還原完的工作區,回 404「It has: none」。這是「讀檔不等 ready」的既有缺口(不是這次造成的),頁面重試即可;測試改成等前一件真的開始跑再起下一件。
- **設定值**:`server.wui_page_job_limit`(4)、`server.wui_page_job_timeout_sec`(3600)。上限是每顆 pod 開工作前查一次再寫,不是跨 pod 的鎖——兩顆 pod 同一瞬間各收到一次點擊,可能短暫多出一件。
- **頁面工作結束不關 item 的沙盒**(第一輪 review):workflow run 結束走 `release`,沒有其他 run 在跑就關掉 item 的
  沙盒——但頁面的 `callTool`、開著的聊天都不是 run,`callAgent` 一回答就會把沙盒從它們底下抽走。頁面工作改走
  `end_page_job`:只放掉自己對話的 turn session,沙盒交給閒置回收(`kill_idle`)。
- **停止的 epoch 基準在「收下工作」時取**(第一輪 review):別顆 pod 按的停止只推進 epoch;原本工作拿到名額開始跑才取
  基準,排隊時按的停止就被吃進基準。現在 `start_page_job` 當下取(`stop_epoch`),拿到名額先比對,而且**同一個
  基準**交給工作(`wf.stop_baseline`:工具工作的監看、subagent turn 的 `_run_baseline` 都用它,不再自己重取——第二輪
  review 抓到重取的那一下又是一個空隙)。被別顆 pod 停掉的工作,由**跑它的這顆** pod 在取消路徑上自己收尾(按停止的
  那顆沒有 task 可收)。
- **中斷的紀錄也在開新工作時結算**(第一輪 review):原本只有讀狀態會結算,頁面關掉之後就永遠「進行中」。
- **停止**:同一顆 pod 上的停止會取消工作的 driver;工具工作的命令是另一個 task,所以工作離開時(不論怎麼離開)一定把命令一起取消並等它結束——第一輪 review 抓到只取消 driver 時命令會一路跑到期限(`test_a_stop_on_this_pod_takes_the_command_down_too`)。
