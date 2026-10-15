# Plan — 工具按需載入：目錄常在、schema 用到才送

## Problem

一個 turn 把**整個工具宇宙**內嵌進請求裡，而工具數量沒有任何上界。

運營方回報「第三方 tool 一多，模型就降智」，並量到他們的 system prompt 是
**414,115 字元**（≈10 萬以上 token，中文越多越高）。對照任何實際窗口：32k 等級是
3 倍超出，128k 等級也被工具字典吃掉大半。

機制有三段，都讀過碼、量過（master `78324d33`）：

1. **每個套件 command 都是一個獨立 function tool**（`tooling/registry.py:build_function_tools`），
   全部掛在同一輪上；沒有分頁、沒有篩選、沒有上限。
2. **同一份清單送兩次**：wire 的 `tools` schema，外加
   `agent/tool_prompt.py:format_tools_for_prompt` 把整份清單（含
   `json.dumps(schema, indent=2)`）渲染進 system prompt。那段是為小模型加的——讓它
   不要把 function tool 當成 shell binary——但它帶了完整 schema。
3. **預算算的是假數字**：`api/turn_context.py:_tools_tokens` 只量
   `build_tools(allowed_tools)`（內建、compact json），**不量套件/第三方，也不算那份複本**。
   本機實測（`.workspace-tools` 真 bundle，14 內建 + 5 套件共 14 command）：預算扣
   **4,538 tok**，實際送 **33,746 tok**（**7.4 倍**，少算 29,208）。單一 command 差很大：
   第一方的 `sci-plot` 一個 command 就 8,837 tok。

超出窗口之後**系統不知道自己超了**（上面第 3 點），所以不會拒絕、不會警告；由 endpoint
靜默砍掉請求前段——而 `base = config.system_prompt`（`api/litellm_runner.py:482`）是 system
message 的**第一段**，工具清單接在它後面（`:560`），所以第一個被丟掉的就是運營方自己寫的
提示詞。模型照樣流暢回答，只是沒看到指令。這就是「降智」。

**而且目前的部署看不見這件事**：`truncated silently` 的判斷要
`usage.prompt_tokens`，`include_usage` 只在 preset 設 `reports_usage: true` 才送（預設
False，`resources/agent_config.py:95`）；`num_ctx` 那兩行被 `is_ollama` 閘住、成功那行還是
`INFO`（這個 app 沒設 logging，`lastResort` 只收 WARNING 以上）；`request too long` 要
provider 的 400 內文命中既有字樣，proxy 改寫過就不命中。

## Locked decisions（來源：2026-10-15 與 user 的對話）

| 問題 | 決定 | 來源 |
|---|---|---|
| 要不要改成 sub-agent 代理整個套件（`ask_<package>`，#270 的形狀） | **不做**，這一輪只做按需載入 | user：「按需載入 ok subagent 先不用」 |
| 哪些常駐、哪些按需 | **常駐＝內建工具 + 一份可設定的釘選清單**；**按需＝所有套件 command**，第一方第三方一視同仁（第一方同樣會長大，規則分兩套一定漂） | user 答 Q1：「對」 |
| 目錄粒度 | **扁平，列出每個 command 的完整名字 + description，描述上限 100 字元**。（我提的「先列套件、再載入該套件」被否決） | user：「先列完整 command 即可 也可以列 command description 最多 100 字元」 |
| 載入的有效範圍 | **這一輪**。不是選擇題——工具清單本來就是 per-turn 建的（`AgentToolContext` 每輪重建），跨輪才需要額外存狀態 | user：「應該本來就是這輪吧」 |
| 模型直接呼叫還沒載入的 command | **自動載入它的 schema，把「這是參數 schema，請照這個再呼叫一次」當作這次呼叫的回覆**，並標為已載入 | user 答 Q4：「可以」 |
| 那次「載入＋重試」要不要留在對話記錄裡 | **不留**，turn-local，不寫進對話記錄、replay 看不到 | user 主動提：「我覺得不要」 |
| 釘選清單設在哪一層 | per-item、工具 modal（**但這一輪不做，見下一列**） | user：「不用調整」 |
| 釘選要不要在這一輪做 | **延後，另開票**。它存在的理由是省掉「每輪第一次用某個 command 多一跳」，而那一跳實際多痛**沒有量過**；而且它是這個計畫裡唯一需要前端的部分 | 我提議延後，user：「我覺得沒關係」 |
| 要不要修 overhead 的估算（`_tools_tokens` 只量內建，低 7.4 倍） | **不修**。它餵兩個地方：history 預算（事前裁切——那本身就是「無腦截斷」，不做）和靜默截斷偵測器（低估所以不觸發）。偵測器針對的是 Ollama 那種「不拒絕、直接砍前段」的 endpoint；user 判斷那條路不用管 | user：「不需要預算吧 他自然會在 litellm 那邊死掉」、「ollama 那個就算了」 |

## Design

### 一份表示法，不是兩份

今天工具資訊有兩份（wire schema + prompt 複本）。改完只有一份：**wire 的 `tools` 陣列
本身就是目錄**。

- 未載入的 command → **stub 工具**：真名字、description 收到 100 字元上下、**空的參數 schema**。
  一條約 30–40 token（ASCII；中文描述會到 100 出頭）。
  **收尾不硬切**：先取第一句（句號／換行為界）；第一句超過 100 就在**詞界或標點**收尾，
  允許小幅超出，絕不切在半字或半句中間。而且**截斷只影響「要不要選它」，不會永久丟資訊**——
  載入後拿到的是完整 description + 完整 schema。
- 已載入的 command → **完整工具**：原本的 `params_json_schema`。
- `format_tools_for_prompt` 的**整段 schema 複本拿掉**，換成一小段說明（它原本要解決的是
  「別把 function tool 當 shell binary」，一句話就夠，不需要每個工具的 JSON）。既有的
  `format_disabled_tools_for_prompt`（#480「可以請使用者開啟」那節）就是「只有名字＋一行」
  的先例。

目錄成本是 **command 數 × 約 30–100 token**，和 schema 大小脫鉤——今天的痛點（一個
`sci-plot` command 8,837 tok）就是 schema 大小。

### 切換用框架自己的 `is_enabled`，不自己攔呼叫

SDK 每一步都重新解析工具清單：`agents/run.py:766` 是回合迴圈的 `while True`，
`:1028` 的 `all_tools = await get_all_tools(...)` 在迴圈**內**（`current_turn += 1` 在 `:1055`）；
`agents/agent.py:246 get_all_tools` 對每個 `FunctionTool` 評估 `is_enabled`（`:254`，可以是
callable、可以 async）。

所以：**同一個名字註冊兩個版本**（stub 與完整），`is_enabled` 讀「這一輪已載入的集合」，
同一時間只有一個會被送出。

- 不需要 mutate `agent.tools`，不需要自己攔 provider 的未知工具呼叫。
- 同名不會踩 SDK 的檢查：`_validate_codex_tool_name_collisions`（`agent.py:95-113`）只對
  `_is_codex_tool` 生效，而且跑在 `is_enabled` 過濾**之後**。
- **模型呼叫未載入的 command 一定會發生**（目錄已經把真名字給它了），這不是要防的誤用。
  stub 的 handler 就是那個落點：載入 → 回 schema → 要它再呼叫一次。寫成「拒絕」是
  #537 踩過的坑——模型把拒絕讀成「別再試了」，整條來源直接啞掉。

### `load_tool`

一個常駐工具，吃 command 名也吃套件名（給套件名＝整包載入），一次可以給多個。
目錄有了真名字，模型多半直接呼叫就好；`load_tool` 是給它「先看用法再決定」的路。

### turn-local，不入歷史

「缺參數的呼叫 + schema 回覆」這一對是**整個設計裡最肥的東西**（schema 可能上千
token）。留進歷史就等於把省下的塞回去，走幾輪回到原點；靠 reducer 事後折疊也不行——
那是「窗口滿了才動」的機制，平常還是一直揣著。所以**根本不寫進對話記錄**。

留下來的是**成功那一次呼叫和它的結果**，那才是事實；schema 不必留，下一輪要用會再載。

連帶兩件：

- **工具使用統計**（plan-tool-usage-stats）要把它記成「載入」而不是「呼叫失敗」，否則
  看起來像模型一直叫錯工具。
- **聊天 UI 不為它畫工具卡**，最多一行「載入 `<command>` 的用法」，schema 不進卡片。

### 上界

同一輪內，同一個 command **最多自動載入一次**；第二次仍缺參數就把錯誤連同 schema 一起
交給模型，不再重載。沒有這條，一輪裡會原地繞（「迴圈要有結構性上界」）。

### 預算要量真的那一份

`_tools_tokens` 改成量**實際會送出的那一份**：常駐 + `load_tool` + 目錄 stub（＋本輪已載入
的完整 schema，若在估算當下已知）。這是把假數字改成真的，不改行為；但它同時讓
「超出窗口」從靜默變成系統知道的事實（`history_budget` 才有意義）。

## Deliberately not doing

- **不做 sub-agent 代理**（`ask_<package>`）。user 明確說先不用。
- **不做兩層目錄**（先套件、再 command）。user 否決；扁平的代價是目錄隨 command 數成長，
  已知並接受。
- **不加 App 層釘選**。per-item 一層。
- **不碰 `reports_usage`**。它只讓數字變真，不回答「有沒有被截」，而且預設值的選擇是
  刻意的（#748）。要不要在那個部署打開，是運營方的判斷。
- **UI 不在這一輪顯示「工具清單佔窗口 X%」**。那是另一張票：這一輪先讓佔用降到不會爆。
- **不設工具數量硬上限**。目錄成本線性且已知，硬上限會把「工具很多」這個合法用法變成錯誤。
- **不加任何事前裁切，也不修 overhead 的估算**。超出窗口就讓請求撞上去：會拒絕的 endpoint
  （vLLM 的 `maximum context length ...`）撞出來的訊息餵回既有的「讀出上限 → 縮 → 重送」路徑。
  **已知的代價**：對**不拒絕、直接砍掉請求前段**的 endpoint（Ollama、部分 proxy），這條路不會
  報錯，而被砍掉的前段正是 `base = config.system_prompt`；靜默截斷偵測器也會繼續因為低估而
  不觸發（`_sent_estimate` 用同一個數字）。user 判斷那種 endpoint 不用管（2026-10-15）。
- **釘選延後**。這一輪完全不碰前端：目錄與載入都在模型那一側，工具 modal 顯示的語意沒變；
  `load_tool` 與自動載入**不發 `ToolStart`/`ToolEnd`/`ToolLog`**（`api/events.py:33-60`），所以
  不會冒出一張裝著 schema 的工具卡——那是後端一個判斷，不是前端特例。

## Phases（一個 commit 一段）

1. **目錄與 stub（純函式）** — `tooling/registry.py`：`build_function_tools` 多一個
   stub 模式（真名字、描述收到 100 上下、空 schema）；描述收尾是一個獨立純函式，
   單元測試吃 ASCII / 中文 / 剛好 100 / 超過 100 / 空描述 / 第一句就超過 100（要在詞界收尾、
   不得切在半字）。
2. **每輪已載入集合** — `AgentToolContext` 上一個 per-turn 的 loaded set（含「這一輪
   自動載入過哪些」的上界計數），純資料 + 測試；不接線。
3. **接線：雙胞胎 + `is_enabled`** — `api/litellm_runner.py` 的工具組裝改成「未載入送
   stub、已載入送完整」，`load_tool` 註冊成常駐工具；stub handler 自動載入並回 schema。
   測試用 `ScriptedAgentRunner` 走真的組裝路徑，斷言送出的 tools 陣列內容（名字集合、
   schema 是空的還是完整的），並且第二次呼叫會真的執行。
4. **prompt 複本換成一段說明** — 拿掉 `format_tools_for_prompt` 的整段 schema，
   改成固定的一小段（「這些是 function tool，不是 PATH 上的執行檔」）。既有測試會紅，
   連同它們的斷言一起搬（「規則搬家要把測試帶走」）。
5. **不入歷史** — 那一對訊息標成 turn-local；`history_items` 的 replay 看不到它；
   統計記成「載入」。測試：一輪結束後讀回對話，裡面只有成功那次呼叫。
6. **文件 + runbook** — `docs/configuration.md`（目錄/載入的行為）、
   `docs/extending-the-platform.md`（寫套件的人要知道 description 會被截 100 字元，
   第一句要自足），以及 `docs/migrations.md` 條目：**行為改變、沒有旋鈕**。

（砍掉的三段：預算量真、釘選後端、釘選前端——見上面兩節的決策與理由。）

## Test plan（紅先行，targeted）

- 截斷純函式：ASCII / 中文 / 邊界 100 / 空描述 / 多行描述只取第一句。
- 組裝：同一個 command 的 stub 與完整版同名、同一時間只有一個被送出；
  `get_all_tools` 過濾後沒有重名。
- stub handler：第一次呼叫回 schema 並標為已載入；第二次真的執行；同一輪第三次（仍缺參數）
  不再重載而是把錯誤交回去。
- `load_tool`：吃 command 名、吃套件名、吃多個；不存在的名字給出「有哪些」而不是沉默。
- 不入歷史：一輪結束後 `history_items` 裡沒有 schema 那一對；成功那次在。
- 沒有工具卡：`load_tool` 與自動載入那次不產生 `ToolStart`/`ToolEnd`/`ToolLog`。
- 突變探針：把 `is_enabled` 的判斷改成恆真（＝全部送完整 schema）→ 組裝測試要紅；
  把「不入歷史」那一行拿掉 → 歷史測試要紅。每個突變只能讓它宣稱守的那條紅。

## `docs/migrations.md` 條目（運營方要做什麼）

**行為改變、沒有旋鈕**：模型每輪看到的不再是全部工具的完整 schema，而是目錄 + 用到才載入。

- **設定**：不用改。釘選是 per-item 的新設定，預設空（＝只有內建工具常駐）。
- **確認做完**：rollout 後在一個工具很多的 item 送一句話，看 LLM 呼叫記錄裡該輪的
  system prompt 字元數（路徑從 pod 自己的 `config.resolved.yaml` 的
  `observability.llm_log.dir` 讀，不要照 repo 預設值——fork 的 WORKDIR 不同）。
  預期從數十萬字元掉到數萬。
- **漏做的症狀**：沒有——這是單向的縮減；但如果某個 item 的工作流程原本依賴「模型一開始
  就看得到所有 schema」，它會變成每輪多一次載入的 round-trip。釘選清單是那個情況的出口。

## Verified ground truth（master `78324d33`；行號在那個 commit 上）

- `tooling/registry.py:265 build_function_tools`；`CommandInfo`（`:51`）有
  `name` / `description` / `params_json_schema`。
- `api/litellm_runner.py:482` `base = config.system_prompt`；`:541`
  `tools.extend(build_function_tools(...))`；`:546` `dedupe_tools`（first-wins）；
  `:558` `tools_section = format_tools_for_prompt(tools)`；`:560` 接在 base 之後；
  `:840` `instructions=base`。
- `agent/tool_prompt.py:32 format_tools_for_prompt`（`indent=2` 的完整 schema）；
  `:62 format_disabled_tools_for_prompt`（只有名字＋一行，目錄形狀的先例）。
- `api/turn_context.py:300 _overhead_for` → `:575 _tools_tokens`（只 `build_tools`）；
  `:767 history_items(max_tokens=…)`；`context_budget.py:345 history_budget`。
- SDK：`agents/run.py:766` `while True`、`:1028` `get_all_tools`、`:1055`
  `current_turn += 1`；`agents/agent.py:246 get_all_tools`、`:254` `is_enabled`、
  `:95-113` 同名檢查只對 codex 工具。
- 本機量測（`.workspace-tools`）：內建 14 個工具 schema 4,538 tok / prompt 複本 9,397 tok；
  5 個套件 14 個 command schema 8,413 tok / 複本 11,471 tok；合計 33,746 tok vs 預算扣 4,538。
  腳本：`llmlog_audit.py`（讀 LLM 呼叫記錄）＋ 以真 record builder/writer 產的樣本一筆。
