# WUI 頁面直接叫 subagent

**狀態:** P1(計劃)。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master`(2a4e7d0e)程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻。

## 1. 問題

user:「我希望wui要能夠呼叫subagent」。

現況〔查證〕:

- 頁面能用的動詞是封閉的(`web/src/renderers/wui/protocol.ts`、`bridge.ts`):讀寫檔案、`callTool`(套件工具,
  一次 ≤ 60 秒、不串流,`api/wui_routes.py` `wui_call_tool`)、`startRun`(起一個 workflow run、串流事件,
  `wui_run`)。**沒有任何問 AI 的動詞**;`docs/plan-wui.md`「Deliberately not built」把「從頁面問 agent」列為
  刻意不做。
- 唯一間接的路:頁面 `startRun` 一個 workflow,workflow 的 agent 步驟裡 AI 再用 `run_agent` 叫 subagent。
- subagent = item 的 `.agent/<名稱>/AGENT.md`(或 App profile 內建),由 `run_agent` 叫:空白上下文、只回最後的
  文字、不能再往下叫(`api/subagent_run.py`、`apps/subagents.py`)。
- 頁面的 `startRun` 每次都開一個新的對話(`locator.open_run_chat`),而「同時一個 run」的檢查是**每個對話**一個
  (`orchestrator.start`:有 `chat_id` 時查 `active_run_for_chat`),所以頁面起的 run 本來就能並行;只有沒對話的舊式
  run 才是一個 item 一個(`ActiveRunExists`)。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 頁面**直接叫**:新動詞 `window.workspace.callAgent(名稱, 輸入, onEvent)`,背後是一次性的背景 run(串流進度、可停止、有紀錄),不用先寫 workflow | 〔user〕選「頁面直接叫」 |
| D2 | 只能叫**頁面宣告過的**:頁面 yaml `agents: [名稱…]`,而且這個 item 真的有(item 的 `.agent/` 或 App 內建) | 〔user〕選「頁面宣告過的」 |
| D3 | 工具權限**跟在聊天裡一樣**:它 AGENT.md 列的工具,上限是 item 的 App 設定(同一套解析),扣掉 subagent 本來就不能用的;點的人要有 `execute` 權限 | 〔user〕選「跟在聊天裡一樣」 |
| D4 | **文字進、文字出**:過程串流進度,最後回傳回答文字;寫的檔案留在 item 裡;要結構化就在 AGENT.md 叫它回 JSON | 〔user〕選「文字進、文字出」 |
| D5 | **留紀錄**:每次呼叫在 item 的 run 清單多一筆,看得到完整過程、能從那裡停止 | 〔user〕選「留,跟 workflow run 一樣」 |
| D6 | **可以並行**:不受「一個 item 同時一個 run」限制 | 〔user〕選「可以並行」 |
| D8 | **跟工具 API 同一個形狀**:工具被包成 `POST …/wui/tools/{名稱}/call`(頁面 `callTool`),subagent 照這個模式包成 `POST …/wui/agents/{名稱}/call`(頁面 `callAgent`)——路徑、命名、yaml 宣告、請求 `{input}`(對應 `{args}`)與最後的結果 `{output}` 都對稱;但因為 AI 一跑幾分鐘,**保留串流、run 紀錄與停止**(D5),也沒有工具的 60 秒上限 | 〔user〕「我們是讓mcp被包成api subagent也應如此」→ 選「形狀一樣,但保留串流與紀錄」 |
| D7 | 身分:算在 item 擁有者頭上,私人環境值用**點的人**的——照 `plan-wui-viewer-login.md` 的「誰的 private」表,與頁面的 `startRun` 相同 | 〔施工,沿用既有決定〕 |

## 3. 機制〔施工〕

### 3.1 路由(D8:跟工具 API 對稱)

| | 工具(既有) | subagent(新) |
|---|---|---|
| 路徑 | `POST /a/{slug}/items/{item_id}/wui/tools/{name}/call` | `POST /a/{slug}/items/{item_id}/wui/agents/{name}/call` |
| 請求 | `{args: {...}}` | `{input: "文字"}` |
| 結果 | `{output, exit_code}`(一個 JSON 回應) | `{output, failed}`,放在串流的**最後一個事件**;之前的事件是進度 |
| 頁面 | `workspace.callTool(name, args) → output` | `workspace.callAgent(name, input, onEvent?) → output` |
| yaml 宣告 | `tools:` | `agents:` |
| 時間 | 沙盒的 60 秒 | 一個 turn 的步數上限;沒有 60 秒 |
| 紀錄 | 無 | 一筆 run(D5) |

回 SSE(跟 `wui/run` 同一種串流)。

- `require_access(..., "execute")`(D3)。
- `{name}` 要在這個 item 的 subagent 定義裡(`.agent/` 或 App 內建,同 `run_agent` 的查法);沒有 → 404,說出
  有哪些。頁面 yaml 的 `agents:` 由前端 bridge 擋(跟 `tools:`、`workflows:` 同一層),後端再擋「item 有沒有」。
- 先訂閱、再開始(同 `wui_run` 的理由:開頭的事件不能掉)。

### 3.2 一次呼叫 = 一個 run(D5)而且可以並行(D6)

- 沿用 workflow run 的紀錄、run chat、停止鍵、SSE:一次呼叫存成一筆 `WorkflowRun`,**種類**標成「頁面叫的
  subagent」,記下頁面、subagent 名稱、輸入;run 清單把它顯示成「頁面 X 叫了 Y」。
- 每次呼叫開自己的對話,所以本來就能並行(D6,跟頁面的 `startRun` 一樣),不需要例外;為了不讓頁面一次叫爆,同一個 item 同時
  進行中的頁面呼叫有上限,超過的回 429 說明「同時太多」(上限是一個設定,預設 4——〔user〕「4個ok」;是新 knob,記 migrations)。
- 執行:用 `workflow_exec.drive_turn` 同一條路跑一個 turn(admit_turn 配額、run chat 持久化、取消 epoch),但
  turn 的系統提示是 subagent 的 AGENT.md、工具是它的 `allowed_tools` ∩ App 上限 − `SUBAGENT_FORBIDDEN_TOOLS`
  (= `_child_context` 的收窄,抽成一個兩邊共用的函式,不各寫一份)。
- 回答:turn 結束時把最後的非 reasoning 文字(同 `drive_subagent` 的收法)當成 run 的結果,最後一個 SSE 事件
  帶著它;失敗或沒寫回答時,跟 `run_agent_task` 一樣給一句說明。

### 3.3 頁面那一側(D1、D2、D4)

- `protocol.ts` 加一個動詞 `callAgent`;`bridge.ts` 擋沒在 yaml `agents:` 的名稱(同 `startRun` 擋 `workflows:`)。
- `runtime.ts`:`window.workspace.callAgent(name, input, onEvent?) → Promise<string>`;`onEvent` 收到的是跟
  `startRun` 一樣的平台事件(message_delta、tool_start/end…),頁面自己決定畫不畫。
- 頁面作者文件(`sample-skills/wui/SKILL.md`、`reference.md`)與 `docs/wui.md` 的動詞表加上它;
  `tests/apps/test_wui_skill.py` 的動詞表守衛會一起管到。

### 3.4 統計

工具使用統計(#932)把這種 turn 記成一個新的場合「頁面」;人 = 點的人(D7)。

## 4. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃;`plan-wui.md` 標註被推翻的那一條 | 文件測試、mkdocs `--strict` 綠 |
| P2 | 後端:run 種類 + 並行上限 + subagent turn(共用收窄函式)+ 回答收法 | 每條決定一個走真入口的測試;突變探針 |
| P3 | 路由 `wui/agent`(權限、查名稱、串流、拒絕的說明) | 403/404/429/422 各有測試 |
| P4 | 前端:`callAgent` 動詞、bridge 擋 `agents:`、runtime;run 清單顯示這一種 | 元件測試;動詞表守衛綠 |
| P5 | 文件(頁面作者 skill、`docs/wui.md`、migrations 條目)、統計場合 | mkdocs 綠 |
| P6 | 真瀏覽器檢查 + demo(MP4):頁面叫兩個 subagent 並行、看 run 清單、從清單停止 | 截圖、demo |

## 5. 不做

- 保證 JSON / schema 驗證(D4 選文字)。
- subagent 再叫 subagent(維持深度 1)。
- 頁面以外的地方(聊天、workflow)多一種叫法——它們已經有 `run_agent`。
