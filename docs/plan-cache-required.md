# Plan: `cache` is required on every DSL step; a workflow that won't parse says so everywhere

## 問題(2026-09-16,master `dc2f6409`)

user:「schedule 會因為 workflow 的 cache 而沒辦法做第二次」。重現(探針,同一 item、同一 workflow、
同一 payload 開兩次):**step 內容只執行 1 次**,第二次 run `done`、每步 skip。

**為什麼**(§9,`engine.py:100`、`handle.py:232`):step 收據放在 `/.workflow/<workflow_id>/step_<name>/`,
**每個 workflow 一份、不是每次 run 一份**;hash 沒變就拿收據。排程每次送一樣的輸入 → 第二次全 skip。
這是「重跑=續跑」的設計,對人按 Run 是功能;DSL 的 `cache` 預設 `true`,而寫 workflow 的 AI 不知道排程要
把「碰外界／有副作用」的步驟設 `cache: false`——`save_schedules` 回覆、`author-workflow` skill、§22.11 都沒提
(#805 漏的)。同類:profile `triggers.json` 的定時 trigger(`build_trigger_start`)不送 payload,第二個 window 一樣。

**user 的決定(grill,2026-09-16)**:
- 語意:排程第二次開火要「整個 workflow 從頭做一遍」——但不是用 run id 加鹽讓每步強制重跑
  (那會殺掉「上游沒變、下游合法跳過」的優化),而是回到 §9 的本意:**由作者決定每一步要不要快取**。
- 機制:**`cache` 改成必填**,沒有預設值——作者(AI 或人)每一步都得表態,錯誤訊息本身就是教材。
  codebase 已有同一哲學:`authoring.py:45` 對 `run.py` 的 lint「making 'take a stance' mandatory」。
- 代價:破壞性變更。既有省略 `cache` 的 `workflow.json` 解析失敗;而今天解析失敗的檔在面板是**靜默消失**
  (`workspace_workflow_metas` 跳過壞檔),排程列說「找不到這個 workflow」(誤導),sweep 開火才炸。
  user 同意:**把「解析失敗的 workflow 列出來並寫原因」一起做進面板和排程列**。

## 決定

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 哪些步驟必填 | DSL 的 **`agent`、`sandbox`** 兩種(`dsl.py:144`、`:170`)——有 `cache` 欄位的只有它們;`gate`/`capability`/`map`/`switch` 沒有這個欄位 | capability 呼叫本身是 upsert、map/switch 是控制流 |
| 2 | 錯誤訊息 | 缺 `cache` 的解析錯誤在 `parse_def`(唯一解碼點)改寫成教規則的一句:「`steps[N]` (agent/sandbox): `cache` is required — `false` for a step that reads the world or has a side effect (sends, fetches, looks at the clock; it runs every time), `true` for one that may be skipped while its inputs are unchanged.」其餘 msgspec 錯誤照舊 | 每扇門(`save_workflow`、載入、面板、範本複製)都經過 `parse_def`;訊息在這裡就一次到位 |
| 3 | 「解析失敗」四處同一句 | `offered.py` 新增一個函式:列出 item 自己的 workflow 檔裡**解析或靜態驗證失敗**的 `{id: problem}`(用 `validate_workflow_json`)。四個消費者:面板列表路由(壞檔也列、帶 `problem`)、排程路由(`run` 指向壞檔 → 該列 problem + 不 runnable)、sweep(開火前跳過 + say-once WARNING,與 `no_such_workflow` 同形)、`save_schedules`(拒絕,回 problem) | 判準一個地方做;parity 測試(路由 vs sweep)才守得住 |
| 4 | 面板列表回應形狀 | 不改形狀:壞檔以 `{id, title: id, phases: [], problem}` 列在同一個 list;前端型別加 `problem?: string`,畫成紅列、沒有 Run | 附加欄位,不破既有消費者 |
| 5 | `run.py`(profile workflow 的 Python API)的 `cache=True` 預設 | **不動** | 工程師寫的;`authoring.py` 的 lint 已要求 sandbox 步驟表態 |
| 6 | 既有檔怎麼辦 | repo 內全部補 `cache`(`sample-workflows/image-to-knowledge` 7 步、playground `dsl` profile 6 步、docs/skill/tests 的範例);線上 workspace 的檔改不到 → 部署後面板和排程列**寫出原因**,由 AI/人用 `save_workflow` 補;`docs/migrations.md` §5 記一筆 | 遷移不能是「我的 workflow 不見了」 |
| 7 | 排程那節的教材 | §22.11、`author-workflow`「Running it on a clock」、`save_workflow` 描述:會碰外界或有副作用的步驟 `cache: false`;全部 `true` 的 workflow 排程第二次什麼都不做——這是作者的選擇,平台不再猜 | 規則和錯誤訊息用同一句話 |

## Phases

| phase | 內容 | 驗收(先紅後綠) |
|---|---|---|
| P1 | 這份 plan | — |
| P2 | `AgentStep`/`SandboxStep` 的 `cache` 拿掉預設;`parse_def` 改寫缺 `cache` 的訊息;repo 內所有 DSL 檔/範例/測試補 `cache` | 紅:缺 `cache` 的 workflow.json → `DslError` 帶那句教材;一條測試 glob repo 內所有 workflow.json(含 sample-workflows、apps 的 dsl profile)都解析得過;既有 DSL 測試全綠 |
| P3 | `offered.py` 的解析失敗清單;面板列表路由列壞檔;排程路由該列 problem;sweep 開火前跳過 + say-once;`save_schedules` 拒絕 | 紅:四扇門各一條;parity 加一個 CASE「run 指向壞檔」:路由不 runnable、sweep 不開火;突變(拿掉那個函式的某個消費者)恰好那扇門紅 |
| P4 | 前端:壞檔紅列(`problem`、無 Run);排程列已會畫 `problems`,確認文案 | vitest:壞檔列出、沒有 Run 鈕;排程列顯示 problem |
| P5 | 文件:§22 `cache` 必填 + 範例、§22.11 排程教材、`author-workflow`、`save_workflow` 描述、`docs/migrations.md` | 每句對著碼 |
| P6 | 推、draft PR、三把鏡頭 review 一輪(改了 schema 契約,算換機制)、CI | — |

**不做**:run id 加鹽;`triggers.json` 那邊不用改碼——它跑的是 profile 的 `run.py`(規則 5)。
