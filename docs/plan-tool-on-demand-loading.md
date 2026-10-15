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
- 同名不會踩 SDK 的檢查：`_validate_codex_tool_name_collisions`（`agent.py:95-118`）只對
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

- **工具使用統計**（plan-tool-usage-stats）不把目錄命中計入：它不是工具在做事，計進去會讓
  每個 command 多一次呼叫、平均時間也歪。意圖是「別讓它看起來像呼叫失敗」——不計入就滿足了
  （第三輪 N2 更正：原本寫「記成『載入』」，實作是不計，真要有那個指標是另一張票）。
- **聊天 UI 不為它畫工具卡**，最多一行「載入 `<command>` 的用法」，schema 不進卡片。

### 上界 —— 不需要，故沒做（P2）

原本寫的是「同一輪內同一個 command 最多自動載入一次」。雙胞胎設計讓那個迴圈**結構上
不可能**發生：載入後 stub 立刻失效、同名的完整工具接手，第二次呼叫打到的是真工具;
參數再錯是 command 自己的 dispatcher 回友善錯誤，不會再經過載入。一輪裡同時發出兩個
對同一個未載入 command 的呼叫也安全（兩個都拿到 schema，`mark_tool_loaded` 對集合是
idempotent）。加一個永遠跑不到的守衛只會是死碼，所以 P2 沒有做。
（釘住它的是 `test_calling_an_unloaded_command_loads_it_and_asks_for_a_retry`。）

### 預算要量真的那一份 —— 不做（user 決定）

這一段原本要求 `_tools_tokens` 改成量實際送出的那一份。**user 否決了**（「不需要預算吧
他自然會在 litellm 那邊死掉」、「ollama 那個就算了」），理由與代價寫在上面的決策表與
「Deliberately not doing」。保留這個標題只為了讓讀到舊版的人找得到它被推翻的地方。

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
- **釘選延後**。這一輪完全不碰前端：目錄與載入都在模型那一側，工具 modal 顯示的語意沒變。
  目錄那一次**仍然會畫一張工具卡**（`ToolStart` 比工具執行早，壓掉它要靠狀態推論、有競態），
  但卡片與**存下來的訊息**都不帶 schema：`ToolEnd` 的輸出換成「載入 `<名字>` 的用法」，而
  那一則訊息根本不寫進對話記錄（round 1 A1）。這段原本寫「不發
  `ToolStart`/`ToolEnd`/`ToolLog`」，與 Design 段自己的敘述矛盾，已更正為實際行為。

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
  `tools.extend(build_function_tools(...))`；`:547` `dedupe_tools`（first-wins）；
  `:558` `tools_section = format_tools_for_prompt(tools)`；`:560` 接在 base 之後；
  `:840` `instructions=base`。
- `agent/tool_prompt.py:32 format_tools_for_prompt`（`indent=2` 的完整 schema）；
  `:62 format_disabled_tools_for_prompt`（只有名字＋一行，目錄形狀的先例）。
- `api/turn_context.py:300 _overhead_for` → `:575 _tools_tokens`（只 `build_tools`）；
  `:767 history_items(max_tokens=…)`；`context_budget.py:345 history_budget`。
- SDK：`agents/run.py:766` `while True`、`:1028` `get_all_tools`、`:1055`
  `current_turn += 1`；`agents/agent.py:246 get_all_tools`、`:254` `is_enabled`、
  `:95-118` 同名檢查只對 codex 工具（`:101-102` 的早退、`:113` 的判斷、`:114-118` 的 raise）。
- 本機量測（`.workspace-tools`）：內建 14 個工具 schema 4,538 tok / prompt 複本 9,397 tok；
  5 個套件 14 個 command schema 8,413 tok / 複本 11,471 tok；合計 33,746 tok vs 預算扣 4,538。
  腳本：`llmlog_audit.py`（讀 LLM 呼叫記錄）＋ 以真 record builder/writer 產的樣本一筆。

## Review round 1 — 決策表（缺陷鏡頭 + 回歸鏡頭，2026-10-15，對 `b9e793d5`）

兩把鏡頭各自獨立跑，發現高度重疊。去重後 18 條；先列完整張表再動手，測試從表導出。
（真實性/符合度鏡頭的結果回來後追加在表尾。）

| # | 發現 | 真因 | 修法 | 怎麼證明 |
|---|---|---|---|---|
| **A1** | **「載入＋重試」那一對仍然寫進對話記錄**，而且帶模型憑空編的參數（`lot_number`），下一輪被 replay 回窗口 | P5 只改了 `ToolEnd.output`；`api/turns.py:439` 的 reducer 照樣用 `ToolStart` 的 `tool_name`/`tool_args` 產生一則 `role="tool"` | reducer 收 `ToolEnd` 時，若 call id 是目錄那次就**不產生訊息**。兩個建構點都看得到 `ctx`（`turns.py:1101` 在 `_drive`、`:1454` 在 `gen()`），所以把 `ctx.catalog_calls` 傳給 reducer。**直播的卡片留著**（`ToolStart` 比執行早，壓掉會有競態），但存下來的 thread 沒有那一則 | 探針：真 turn 跑完讀回 thread，`role="tool"` 只有一則；突變（拿掉跳過）要紅 |
| **A2** | **replay 送出重複的 function 名字＋完整 schema**，比這個 PR 之前還大（55,910 vs 22,118 字元） | `health/replay.py:227-239`、`:284` 直接走 `agent.tools`，不經 `is_enabled`；`load_tool` 也沒有 `is_enabled` | 新增**一個** sync helper `enabled_function_tools(agent, ctx)`（replay 跑在 `asyncio.to_thread` 裡、沒有 running loop，但測試會同步呼叫，所以不用 `asyncio.run`），replay 與 `_emit_llm_trace` 共用它 | **parity 測試以 SDK 為 oracle**：對多個 context，helper 的結果 == `await agent.get_all_tools(...)` 濾掉非 FunctionTool；replay 測試改成斷言 **list**（既有測試比的是 set，重名看不到） |
| **A3** | `tests/agent/test_tool_alias_model.py::test_the_system_prompt_lists_third_party_commands_by_their_new_names` 紅（CI 的 `tests/agent` 會紅） | P4 刪了 prompt 裡的清單，這條 #888 守衛沒跟著搬 | 斷言改讀 `{t.name for t in agent.tools}`——#888 的性質（模型看到的是 `<本地名>__<cmd>`）還在，只是搬了家 | 測試綠；突變（把 `model_tool_name` 改回扁平名）要紅 |
| **A4** | 用 5 個不同 command 的一輪會撞 `runner.max_turns`（預設 10），**活做完了但使用者收到 error** | 每個 command 第一次用多一步；而 `_TOOLS_NOTE` **完全沒提 `load_tool`**，所以模型不知道可以一次載多個 | `_TOOLS_NOTE` 加一句：可以用 `load_tool` 一次載好幾個、也可以給整個套件名。加上 A5 之後 1+K+1 這條路才真的存在。**不改 `max_turns` 預設**（那是運營方的旋鈕），但 runbook 要寫明它是出口 | 探針：K=5 的腳本在修法後步數 = K+2；runbook 有那一段 |
| **A5** | **模型從來不知道任何套件名字**，所以 `load_tool` 自己廣告的「給套件名就載入整包」是死路 | 目錄那行只用 `cmd.description` 的第一句，`describe_command` 的 bundle 那段不見了 | 目錄那行補上套件名（第三方的名字已經含了就不重複）。版本/作者/「不是最新」那句仍然只在載入後出現——那是刻意的，寫進文件 | 測試：真 bundle 的名字出現在送出的工具清單裡 |
| **A6** | `load_tool` 在**沒有任何 command 被授權**時也掛上去，而且回「Tools you can load: .」這種壞句子。每個 `ask_knowledge_base` 子代理、每個窄 `tools:` 的 workflow 節點都中 | 閘是 `if packages:`（item 裝了什麼），不是 `allowed_command_names(packages, allowed)`（這一輪授權了什麼）——而旁邊註解宣稱是後者 | 照 `request_env_granted` 的同一個判準；`_unknown_name_reply` 在授權清單為空時換一句話 | 測試：只授權 `read_file` 的 preset 沒有 `load_tool`；KB 子代理也沒有 |
| **A7** | `load_tool` 的回覆**沒有上限**：5 個套件名 → 36,054 字元；`["sci-plot"]*20` → 261,238（超過 200k 的 cap，因為 `cap_tool_outputs` 是由 `build_tools` 與 `build_function_tools` 呼叫的，而目錄與 `load_tool` 兩個 builder 都沒呼叫它——驗修法輪更正：關鍵是哪個 builder 呼叫 cap，不是在哪裡被接上）；3000 個亂名 → 928,888 | 目錄與 `load_tool` 是在 `_agent_for` 裡接上的，繞過 `cap_tool_outputs`；`names` 不去重、不限數量；`by_name` 把 `model_tool_name`、`pkg.name`、`pkg:cmd` 塞進同一個 dict 不去重，所以 bundle 與 command 同名的 `data-fetch` 會回兩次 | `names` 去重＋數量上限；回覆走同一個輸出上限（**驗修法輪 F4 改成上限管「載入什麼」**）；`by_name` 的命中去重 | 探針：重複名字只回一次；`["sci-plot"]*20` 的回覆長度 ≤ 上限 |
| **A8** | command 的 description 不是字串（published manifest 允許 `null`/`42`/`["a"]`）→ **整個工具清單建構炸掉**，那個 item 的每一輪都失敗 | `catalog_description` 直接 `.strip()`；舊路徑是 f-string（醜但能跑） | `str(text or "")` | 測試：`description=None` 仍能建 agent |
| **A9** | 縮寫讓目錄那行只剩 4 個字：`'Max. 1000 rows…'` → `'Max.'`、`'…, e.g. a trend…'` → `'Plot a chart, e.g.'`、`'etc.'`、`'vs.'`、`'U.S.'` | `.` + 空白就當句尾；而 `_SENTENCE_END_ASCII` 的註解宣稱「`e.g.` 不會」——那句是**假的** | 句尾要同時滿足：後面是空白/結尾、**下一個非空白字元是大寫或 CJK**（數字、小寫都當續句）、且 `.` 前的 token 不是縮寫（含內部點、單字母、常見縮寫表）。註解改成真的 | 參數化測試：`e.g.`/`i.e.`/`etc.`/`vs.`/`Max.`/`U.S.`/`3.5`/正常兩句 |
| **A10** | 只有一個很早的邊界時，切點比「完全沒有邊界」的最後退路更差：`'Plot ' + 200 字` → `'Plot…'`（5/100） | 在 `first[:limit-1]` 裡取**最後一個**邊界，但那個邊界可能極早 | 邊界太早（不到上限的一半）就退回切到上限 | 測試：`'Plot ' + 200 字` 不再是 `'Plot…'` |
| **A11** | 子代理**共用** parent 的 `loaded_tools` 與 `catalog_calls`（`_child_context`、`build_ask_kb_context` 都是 `dataclasses.replace`） | `_child_context` 的重設清單沒加這兩個欄位——而它的 docstring 正是寫「任何屬於 parent 這一輪累積的欄位都要列在這裡」 | 兩處都加進重設清單 | 測試：child 載入後 parent 的集合不變（突變：拿掉重設要紅） |
| **A12** | `_emit_llm_trace` 讀 `agent.tools`，所以運營方看的 trace 行把每個 command 列兩次 | 同 A2 | 共用 A2 的 helper | 同 A2 的 parity 測試 + 一條 trace 測試 |
| **A13** | `docs/subsystems/agent-runtime.md:3,35,83` 還在描述被刪掉的 inventory，而 **`docs/` 整棵會透過 `system-design` skill 餵給 app 裡的 AI**（#865） | P6 沒掃到 subsystem 參考 | 更新那三處 | grep 不再命中舊描述 |
| **A14** | `agent/tools.py:4153-4156` 的註解現在是假的（「會透過 `format_tools_for_prompt` 再出現一次，所以一輪出現兩次」） | 同 A13 | 改掉 | grep |
| **A15** | `_TOOLS_NOTE` 的目錄那段在**沒有套件**的 turn 也會附上（約 70 token 的死文案，講一件不會發生的事） | 固定文案 | 只有真的有目錄項目時才附那段 | 測試：沒有套件的 config，instructions 不含那段 |
| **A16** | `catalog_description(limit=0)` 回幾乎整串 + `…`；`limit=-5` 砍 5 個字；切點可能落在組合字元/ZWJ 中間（60,000 次模糊測試有 916 次） | `first[:limit-1]` 對小 limit 失去意義；邊界沒考慮組合字元 | limit 下限保護；收尾時退到不是組合字元/ZWJ 的位置 | 測試：`limit=0/1/-5`；emoji ZWJ 與組合字元的案例 |
| **A17** | `build_load_tool` 的 `except ValueError` 在 production 走不到 | `load_tool` 在 `builtin_names` 之前加入，所以**會**被 `wrap_with_args_recovery` 包；非 JSON 在進到 handler 之前就被 `MalformedToolArgsError` 擋掉（只有直接呼叫 `on_invoke_tool` 的測試走得到） | 保留那一行（直接呼叫的路還在，成本是一行），結論寫在這裡 | 不加走不到的測試；這一列就是記錄 |
| **A18** | `ty` 紅：`unresolved-import psycopg2`（`tests/api/test_tool_usage_postgres.py:95`）——**不是這個 PR 造成的**，master 已用 #953/`b5389900` 修掉，只是這條分支的 base 更早 | 分支 base 舊 | 從 master merge 進來 | `ty check` 綠 |

**不做的**（留原樣，理由寫在這裡）：
- 不改 `runner.max_turns` 的預設值（運營方的旋鈕，不是我的）。
- 不為「直播卡片」做前端：目錄那一次仍然畫一張沒有 schema 的小卡片，壓掉 `ToolStart` 會有競態（它比工具執行早）。user 定案的是「不留在對話記錄」，那一條照做。
- 不把版本/作者/「不是最新」那句放進目錄行（太長，而且載入後就有）。

### 真實性 / 符合度鏡頭（同一輪，追加）

**每一個量到的數字都精確**（14 內建 4,538 / 9,397；5 套件 14 command 8,413 + 11,471 = 19,884
→ 663 + 148 = 811；47.36 tok/command；sci-plot 的 `chart` 8,837；合計 33,746 對預算 4,538 =
7.436 倍、少算 29,208）。SDK 的行號與行為宣稱也全部為真。以下是它抓到的、前兩把沒看到的：

| # | 發現 | 修法 |
|---|---|---|
| **B1** | `catalog_calls` 沒去重，所以一次 `load_tool(["rca-tools", "data-fetch"])` 讓**人讀到**「載入 `data-fetch`、`plot-trend`、`data-fetch` 的用法」 | `mark_tool_loaded` 的 per-call list 去重；測試斷言那句話與 `catalog_calls["c1"]` |
| **B2** | **`build_load_tool` 的授權過濾沒有被釘住**：把它的 `allowed` 換成 `None`，42 條目標測試全綠；突變下，只授權一個 command 的 turn 可以載到沒被授權那個的完整 schema | parity 測試擴到**三個** builder（以 `build_function_tools` 為 oracle，問遍每個 command 的四種拼法） |
| **B3** | 計畫裡**三條被取代的規則還活著**（「預算要量真的那一份」、「上界」、「不發 `ToolStart`/`ToolEnd`/`ToolLog`」），而且有一句宣稱「砍掉的三段」其實沒砍。程式跟著另一半 | 三段都改寫成「為什麼沒做 / 已更正為實際行為」，不留兩套規則 |
| **B4** | `MarkLoaded = Callable[[Any, str], None]` 把型別檢查關掉了——鏡頭把 `.mark_tool_loaded` 與 `.tool_call_id` **兩個都改名**，`ty` 仍然綠 | 改成 `Callable[[ToolContext[AgentToolContext], str], None]`，`_mark_loaded` 也標上真型別 |
| **B5** | `litellm_runner.py` 呼叫 `format_tools_for_prompt` 上面那段註解、`describe_command` 的理由段、`extending-the-platform.md` 115 行後的那段，都還在描述被刪掉的清單 | 三處改寫；`agent/tools.py` 那條在前兩把已修 |
| **B6** | runbook 的 `ls -t <dir>/$(date +%F) \| head -1` 會抓到 `index.jsonl`（200 次實測命中 2 次），而它沒有 `request`；而且**最新那筆是那一輪的最後一通**，載入過的 command 在裡面本來就帶完整 schema | 改成 `0001-*.json`（那一輪第一通），並寫明為什麼不是最新那筆 |
| **B7** | 「字元數應該從數十萬掉到數萬」是把投影當預期——沒有東西保證落在哪一位數 | 改成「掉一到兩個數量級；本機同組工具 83,180 → 591，絕對值取決於你們的工具數」 |
| **B8** | 兩處 docstring 把 414,115 寫成因果（「that second copy is how…」），那個數字是運營方報的、本機無法重現 | 改成 "an operator reported…" |
| **B9** | `load_tool` 既不在 `TOOL_VERBS` 表裡，也不在那份 docstring 的「在表外，以及為什麼」清單裡——而那份清單已經短過五次 | 加一條：它只讀 in-process 的 `PackageInfo`（部署設定，不是 item 內容）、回描述與 schema、而且碰不到沒被授權的 command |
| **B10** | 行號兩處：`dedupe_tools` 在 `:547`（`:546` 是註解行）、`agent.py:95-113` 其實是 `95-118` | 更正 |
| **B11** | `_map_event` 的**非串流**呼叫點沒被釘住（`# pragma: no cover`，只有 `WORKSPACE_AGENT_STREAM=0` 才走到） | 不加測試（走不到的路寫不出會紅的測試）；明記在這裡，免得以後有人以為兩扇門都守住了 |

## Review round 2 — 驗修法（缺陷 + 真實性，2026-10-15，對 `ce3c70cc`）

兩把鏡頭打的是 **P7 的修法本身**。結論：P7 引進的一條規則讓結果比修之前更差，三個最大的
修法沒有任何測試釘住，兩個數字是錯的。P8 全部處理。

| # | 發現 | 修法 | 怎麼證明 |
|---|---|---|---|
| **F3** | **我在 A9 加的「下一個字必須大寫或 CJK」讓目錄行比 P7 之前更差**：`_is_cjk` 只涵蓋兩段範圍，所以韓文、泰文、阿拉伯文、希伯來文、天城文**永遠不可能滿足**它——ASCII 的 `.` 從此不再結束句子，整段變成目錄行、切在句中。全小寫英文同理。而且那條規則**什麼都沒買到**：A9 的十個案例光靠縮寫規則＋既有的空白 lookahead 就全過（鏡頭驗過） | 整條刪掉，保留縮寫規則 | 五條參數化測試（韓/泰/天城/全小寫/下一字是數字）；突變=把縮寫規則刪掉要紅那兩條只有它能擋的 |
| **F2** | **重複的 `tool_call_id` 會把那一輪後面每一則工具訊息都刪掉**（含真的 `write_file`），因為紀錄只用 id 比對、而且從不消耗。OpenAI 相容端點會原樣轉傳 server 的 id | 紀錄**用完就取走**：`_map_event` 改寫輸出時 `pop` 掉 `catalog_calls`，並把 id 記進新的 `ephemeral_outputs`；reducer 消耗那一筆。一筆對一則 | 真 `TestClient` 一輪、三次呼叫共用 `c1`：`write_file` 與真的 `data-fetch` 都在、目錄那一則不在。測試的 runner 走**真的 `_map_event`**（替身不能繞過被測那條路） |
| **F4** | `load_tool` 的字元上限只截**文字**：40 個 command 的 schema 照樣全被標成已載入 → 下一步的工具清單 394,560 字元，比這個 PR 要移除的 414,115 還糟 | 上限改成管「**載入什麼**」：放不下的 command 不標、不描述，回覆最前面說「這次已經滿了」 | 12 個肥 command 的 bundle：`loaded_tools` < 12、回覆裡出現的就是被載入的那些 |
| **F5** | 被內建工具遮蔽的 command，`load_tool` 還是交出描述與 schema，並叫模型照那組參數再呼叫一次（真正跑的是內建工具，參數不同） | `build_load_tool` 收一個 `shadowed` 集合，那些名字不進 `by_name`。⚠️ **P8 當時只加了參數、runner 沒有傳**（第三輪 D1 抓到，當時那句「runner 傳 `builtin_names`」是假的）；P9 才真的接線，並把 `builtin_names` 提前到 append 之前讀 | 套件 export `read_file`：`load_tool(["read_file"])` 不載入、回「沒有這個工具」 |
| **F6** | `_first_sentence` 被我改成每個 `.` 取一次尾端切片 → 二次式；40 萬字的描述 680 ms，而它跑在 event loop 上、描述來自第三方 manifest 且沒有長度上限 | 改成用索引看下一個字元 | 形狀測試：10 倍輸入不得花 30 倍時間 |
| **F7** | `[bundle]` 加在 100 字元上限**之外**（實測 139 字元，上限是 user 定的 100） | 後綴算進同一個上限 | 長 bundle 名 + 400 字描述 → 條目 ≤ 100 且以 `[bundle]` 結尾 |
| **F8** | `_CATALOG_NOTE` 叫模型看 `[bundle]` 標記，但第三方條目**刻意沒有**那個標記（第三方才是使用者自己裝的那一群） | 那句話補上「或名字裡 `__` 之前的那段」 | 文案測試 |
| **F10** | `limit=1` 讓 1 個字的描述變成 `…`；負數 limit 回傳比上限長的字串 | 「放得下就原樣」的判斷移到小 limit 守衛**之前** | 三個 limit 的測試 |
| **B2′** | **我寫的 parity 測試是空的**：`mark_loaded=lambda *_: None` 什麼都沒寫，所以 `set() <= …` 永遠成立——它為之而生的突變仍然零條變紅 | callback 走真的 context API，並且斷言回覆**沒有描述**沒被授權的 command | 突變（`_granted(packages, None)`）要紅 |
| **A2′/A12′** | **replay 與 trace 的修法沒有任何測試釘住**（把缺陷放回去：72 條、305 條全綠）。既有 replay 測試比的是 **set**，重名看不到，而且沒有一條帶 `packages` | 兩條新測試：replay 斷言**名字清單**無重複 + 第一步的 `properties` 是空的；trace 攔 `build_trace` 斷言名字不重複 | 兩個突變各自要紅 |
| **A11′** | `build_ask_kb_context` 那半沒測試（刪掉重設，795 條全綠） | 補一條 | 突變要紅 |
| **B6′** | runbook 的 `0001-*.json` **不是「那一輪的第一通」**：`_seq` 是**每個 process** 從 0 開始的，不是每輪；重啟後同一天會有兩個 `0001-`，跨午夜那天的目錄可能根本沒有 `0001-` | 改成從 `index.jsonl` 找那一輪（它有 `seq`/`ts`/`file`），開 `seq` 最小的那一筆 | 文件 |
| **B7′** | 「83,180 → **591**」的 591 是**舊的**（`b9e793d5` 的 `_TOOLS_NOTE`）；P7 自己加了 `_CATALOG_NOTE`，實際是 **822**。連帶「固定那段 148 tok」→ **206 tok**、「811 tok」→ **869 tok** | 三處數字更正 | 重量一次 |
| 其他 | `tool_authz` 的「two bullets up」其實是四條；三處註解寫 `round 1 F6/G1` 而表裡是 B1/B2；`agent-runtime.md` 又把 414,115 和「一半」綁在一起；`§設定一覽` 指向不存在的章節；A2 的「22,118」是 after 不是 before（before 是 52,267）；`_LOAD_REPLY_MAX_CHARS` 的理由寫錯（不是「位置」而是「哪個 builder 呼叫 cap」）；A17 的「需查」要寫出結論（已包；`except` 在 production 走不到）；A4 的「K=5 探針」其實是算術不是量測；「4,076 綠/7 紅是環境雜訊」方向寫反了（`--all-extras` 之後是 **4,083 綠 0 紅**） | 逐條更正 | grep / 重跑 |

**鏡頭確認為真**（摘要）：A1 的落點與兩個建構點、A3/A5/A7/A8/A9/A10/A15/A16/B1/B4/B10 的突變各自只紅它守的那條、`MarkLoaded` 改成真型別之後「改名就會紅」（有必綠對照）、SDK 的每個行號與行為、runbook 的 `request.messages[0]`／`api_key` 明文／`index.jsonl` 寫在 record 之後（200 次命中 2 次，ext4）。

## Review round 3 — 換過機制就再一輪（缺陷 + 回歸，2026-10-15，對 `2374558b`）

P8 的 F2 修法**換掉了機制**（新的兩跳：`_map_event` 取紀錄、reducer 消耗），F4 也換了上限的
語意，所以依規則再開一輪。缺陷鏡頭打新機制，回歸鏡頭打 `turns.py` 的影響面。

| # | 發現 | 修法 | 怎麼證明 |
|---|---|---|---|
| **D1** | **BLOCKING：F5 的 `shadowed` 是死參數**——runner 從來沒傳它，只有單元測試直接傳。P8 的 commit 訊息宣稱「runner 傳 `builtin_names`」，**那句是假的**。實測：套件 export `exec` 時，`load_tool(["exec"])` 照樣交出描述與 schema，並叫模型照那組參數呼叫（真正跑的是內建的 `exec`，參數是 `cmd`） | 真的接線。`builtin_names` 原本在 append **之後**才讀，提前到之前；遮蔽是**每輪**的事實（`allowed_tools=[…:exec]` 時內建的 `exec` 沒被授權、模型拿到的是套件那個），所以不能用模組層常數 | 走真 `_agent_for` 的測試；突變（`shadowed=()`）要紅 |
| **D2** | 上限的註解說「每個 command 都要放得下才載入」，但第一個 command 豁免（`and done`），所以單一 40 萬字元的 schema 照樣整個進下一步 | **決定：第一個豁免是刻意的**——schema 比預算大的 command，不載入就等於「看得到、被叫去呼叫、永遠呼叫不了」；而且模型直接呼叫它時走的是完全沒有上限的那條（stub 的自動載入）。所以這個預算是**批次**的上限，單一超大 schema 這裡不設限。註解改成這樣寫 | 一條測試斷言它仍然載入、且回覆長度超過上限 |
| **D3** | 新的「已滿」通知會把同一個 command 名字列兩次（bundle 名等於 command 名時解析到同一對）——正是第一輪 B1 的同一類，在新句子裡重新打開 | `dict.fromkeys` 去重 | 測試建出 bundle 名 == command 名 + 預算填滿；突變要紅 |
| **D4** | **同一個 `tool_call_id` 下兩次目錄命中 → 第二次的原始 schema 進了對話記錄**（`_map_event` 把整筆紀錄 pop 掉，第二次就認不出來）。平行呼叫共用 id 時更糟：真工具的輸出被當成目錄的改寫掉、目錄自己的被存下來 | 紀錄改成**每個 call id 一個 hit 佇列**（`dict[str, list[list[str]]]`）：一次命中一筆、一筆對一個 output。`load_tool` 一次載 N 個是**一筆**（帶 N 個名字），所以佇列長度等於要壓掉的 output 數，不是 command 數 | 兩次命中共用 `c1`，兩個 output 都不得帶 schema；突變（整筆取走）要紅 |
| **D5** | `tool_output` 沒有 call id（`_call_id` 的 `or ""` 退路）時，整個壓制機制認不出來，原始 schema 進記錄 | 空字串也當成一個 key —— 壓掉那個 output 比「可能誤壓到相鄰的真訊息」重要，因為記錄裡的 schema 會被 replay 進之後每一輪 | 空 id 的測試；突變要紅 |
| **D6** | F7 把 user 的 100 讀成「整條條目」的上限，所以 42 字元的 bundle 名會吃掉 42 個字元，只剩 54 給模型真正用來選工具的那句；而且 bundle 名接近 100 時 `max(2, …)` 的地板會讓條目仍然超過 100 | **100 還給 description**（user 的原話是「command description 最多 100 字元」），bundle 名另有自己的小上限（24） | 140 字元的 bundle 名：description ≤ 100、後綴 == 24；突變（把後綴算回去）要紅 |
| **D7** | 第二輪說非串流那個 `_map_event` 呼叫點「走不到所以寫不出會紅的測試」——**那個理由不成立**，`WORKSPACE_AGENT_STREAM=0` 就走到了（鏡頭寫了探針並驗了突變） | 補一條測試 | 突變（那一行拿掉 `ctx`）要紅 |

**鏡頭確認為真**：F3 的刪除是對的（三個版本的 `_first_sentence` 用 AST 取出來做 4,569 筆差分，**沒有一筆比 `ce3c70cc` 更差**；`sample-tools` 的 8 個真描述三個版本完全相同）；一輪只有一個 reducer（`stream()` 在 production 沒有呼叫者）；沒有「已 map 但未送達」的序列（`done` 哨兵在 `finally`、重試在上一輪事件都過了 reducer 之後才重入）；ctx 每輪新建，三個 `dataclasses.replace` 的拷貝點都處理過；`load_tool` 一次載 N 個只有一個 `remove`（正確）；被壓掉的訊息不會錯開 `[n]` 引用配對（per-name cursor）；`_finish_claim` 在 `if produced:` 之外，所以全被壓掉的一輪不會留下孤兒 claim。

**留著的已知限制**（寫在這裡，不是漏掉）：`_TurnReducer` 現在會**改動** ctx 的 list，所以它不再是輸入的純讀者——同一個 ctx 上跑兩個 reducer 會靜默吃掉一則真訊息。production 只有一個（`_drain_and_persist` 收到的是同一個物件），但這條界線要寫明。

### 回歸鏡頭（同一輪）：既有路徑零回歸，但新機制有一條順序相依的縫

**零回歸**（before/after 逐項 byte-identical）：存下來的 thread（7 種情境，含 `ToolLog`、
`#62` display、error 收尾、取消、沒有 `ToolStart` 的 `ToolEnd`、兩個真工具共用 call id）、
KB chat（6 種，含真的 `[1]` → `Citation`）、SSE 的 14 種事件形狀、內建工具集（6 種設定）、
replay 的 `tools` payload（`allowed_tools=[]` 連 messages 都一樣）、tool usage 的真呼叫計數、
`ctx.tool_outputs`。兩邊完整套件 4030 → 4096 綠，**沒有任何測試在 before 綠而在 after 紅**。

| # | 發現 | 修法 | 怎麼證明 |
|---|---|---|---|
| **R1** | **同一個 call id 下，真工具的輸出先到時，它會被當成目錄那一次改寫並丟棄，而目錄自己的 schema 被存進記錄**——P9 的 hit 佇列只用 id 比對，所以是「誰先到誰被壓」。我自己對 P9 跑探針確認過：`real rewritten = True / schema survives = True` | hit 連**它回答的那段文字**一起記，比對時比文字。那不是在嗅樣式，是比對「這一輪自己產生的值」的身分，所以與到達順序無關 | 參數化測試跑兩種順序：真輸出都要留著、兩個輸出都不得帶 schema。四個突變（改回只比 id／mapper 不傳輸出／stub 不記答案／`load_tool` 不記答案）各自要紅 |
| **R2** | 一輪能用的不同套件 command 從 ~9 掉到 ~4（`runner.max_turns` 預設 10） | 已在 runbook 寫明並指出旋鈕 | 文件 |
| **N2** | 目錄命中在使用統計裡**完全不計**，而計畫寫的是「記成『載入』」 | 計畫的字面改成實際行為：不計入（意圖是「別讓它看起來像呼叫失敗」，不計入就滿足了；真要有「載入」這個指標是另一張票） | 文件 |

**留著的已知限制**（補充 D5）：`_map_event` 仍然是先比 call id 再比文字，所以一個**沒有 call id**
的目錄輸出只能靠 `""` 這個 key 命中；若同一輪還有另一個沒有 id 的真輸出、而且文字恰好相同，
會被誤壓。兩個條件要同時成立才會發生，而代價（記錄裡的 schema 被 replay 進之後每一輪）比
誤壓一則重複文字的訊息大。
