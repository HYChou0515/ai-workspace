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
  (第一版寫「codebase 已有同一哲學:`authoring.py:45` 對 `run.py` 的 lint」——**假的**,那行是 DSL 的 stale-cache
  lint,`run.py` 沒有任何 `cache` 檢查;第三輪真實性鏡頭抓到。)
- 代價:破壞性變更。既有省略 `cache` 的 `workflow.json` 解析失敗;而今天解析失敗的檔在面板是**靜默消失**
  (`workspace_workflow_metas` 跳過壞檔),排程列把它當健康的列(`known: true`、`runnable: true`、還給「下次」——
  `offered_workflow_ids` 只看檔名;第一版寫「排程列說找不到」,假的),sweep 開火才炸(3 次後燒掉 window)。
  user 同意:**把「解析失敗的 workflow 列出來並寫原因」一起做進面板和排程列**。

## 決定

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 哪些步驟必填 | DSL 的 **`agent`、`sandbox`** 兩種(`dsl.py:144`、`:170`)——有 `cache` 欄位的只有它們;`gate`/`capability`/`map`/`switch` 沒有這個欄位 | capability 呼叫本身是 upsert、map/switch 是控制流 |
| 2 | 錯誤訊息 | 缺 `cache` 的解析錯誤在 `parse_def`(唯一解碼點)改寫成教規則的一句:「`steps[N]`: `cache` is required(做出來沒有 `(agent/sandbox)`——msgspec 的位置不帶型別,不另外解 JSON 去查) — `false` for a step that reads the world or has a side effect (sends, fetches, looks at the clock; it runs every time), `true` for one that may be skipped while its inputs are unchanged.」其餘 msgspec 錯誤照舊 | 每扇門(`save_workflow`、載入、面板、範本複製)都經過 `parse_def`;訊息在這裡就一次到位 |
| 3 | 「解析失敗」四處同一句 | 一個判準 `workspace_store.workflow_problem`:**只看 `parse_def` 會不會丟**——這是載入器自己的判準(`load_workspace_workflow` 回 None 的條件),靜態驗證(`validate_def`)不擋載入所以不算(第一版寫「解析或靜態驗證失敗」,施工時對齊載入器改掉)。`offered.unparsable_workflow(read, item, id)` 對單一 id 問、任何 reader 都行;`workspace_workflow_listing` 對整個資料夾問。四個消費者:面板列表路由(壞檔也列、帶 `problem`)、排程路由(`run` 指向壞檔 → 該列 problem + 不 runnable)、sweep(開火前跳過 + say-once WARNING,與 `no_such_workflow` 同形)、`save_schedules`(拒絕,回 problem) | 判準一個地方做;parity 測試(路由 vs sweep)才守得住 |
| 4 | 面板列表回應形狀 | 不改形狀:壞檔以 `{id, title: id, phases: [], problem}` 列在同一個 list;前端型別加 `problem?: string`,畫成紅列、沒有 Run | 附加欄位,不破既有消費者 |
| 5 | `run.py`(profile workflow 的 Python API)的 `cache=True` 預設 | **不動** | 工程師寫的、進 repo 被 review 的碼,改預設會動到 repo 內每個 `run.py`;DSL 是 AI 在執行期寫、沒人審。**`run.py` 沒有任何檢查**(第一版說有 lint,假的),所以 `triggers.json` 定時 trigger 跑的 `run.py` 有同一個第二次開火形狀——寫進 `docs/workflows-authoring.md` 開頭,是作者要記得的事 |
| 6 | 既有檔怎麼辦 | repo 內全部補 `cache`(`sample-workflows/image-to-knowledge` 2 個 agent 步、playground `dsl` profile 1 個 agent 步——第一版寫 7/6 是全部步驟數;docs/skill/tests 的範例,含 `docs/workflows-syntax.html` 的 14 個——第一版漏了它);線上 workspace 的檔改不到 → 部署後面板和排程列**寫出原因**,由 AI/人用 `save_workflow` 補;`docs/migrations.md` §5 記一筆 | 遷移不能是「我的 workflow 不見了」 |
| 7 | 排程那節的教材 | §22.11、`author-workflow`「How to author」與「Running it on a clock」、附在 skill 後的機器產生 DSL 附錄(同一個 `CACHE_RULE` 常數;`save_workflow` 的描述本身不列欄位,指向 skill):會碰外界或有副作用的步驟 `cache: false`;全部 `true` 的 workflow 排程第二次什麼都不做——這是作者的選擇,平台不再猜 | 規則和錯誤訊息用同一句話 |

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

## 施工紀錄

- **P2**(`5e891cfc`;第一版寫 amend 前的 `b3870891`):`AgentStep`/`SandboxStep` 的 `cache` 無預設;`parse_def` 把 msgspec 的「missing required
  field `cache`」改寫成 `CACHE_RULE`(位置保留,連 map 裡的 `do[0]` 都帶到)。repo 內補 `cache`:2 個出貨 json
  (3 步)、6 個 docs(11 個範例)、18 個測試檔(137 個字面值,`True`——它們原本依賴的值)。新測試 glob 出貨的
  workflow.json 都解析得過。先紅的:缺 `cache` 的 agent/sandbox 各一條。
- **P3**(`d8e66406`):`workspace_store.workflow_problem`(載入器的判準)+ `workspace_workflow_listing`(壞檔帶原因)
  + `offered.unparsable_workflow`(單一 id、任何 reader)。四扇門:面板列表加 `problem` 欄位;`schedule_views(broken=)`
  → `run_problem`(`known` 保持 true);sweep 在**到期、claim 之前**問(每次開火一次讀,不是每列每 tick——#804 的
  「沒到期的 tick 只讀一次」測試守住了第一版放錯位置);`save_schedules` 拒絕並點名 `save_workflow`。八條先紅;
  五個突變各紅在自己那扇門(面板 1、路由 2 含 parity、sweep 1、工具 1、views 3)。
- **P4**(`392a955a`):前端 `workflow-broken-<id>`(原因、無 Run)、`schedule-broken-<index>`;兩條 vitest 先紅;typecheck 綠。
- **P5**:機器產生的 DSL 附錄用同一個 `CACHE_RULE` 加一句(測試釘住 agent/sandbox 的 required 含 `cache`、句子在);
  skill「The idea that makes a workflow reliable」(規則是設計原則,放那裡;第一版寫「How to author」)與「Running it on a
  clock」各加一段;§9、§22.2、§22.11;migrations.md 一列;mkdocs `--strict` 本機綠。

**9/14 live check 的三次開火為什麼成功**(推論,不是查證):#805 的 parity 測試 fixture 寫的 stamp workflow 是
`"cache": False`(`tests/api/test_schedules_route_parity.py:39`),live check 大概用了同一個形狀;直接證據只有 PR #805
留言裡 `stamps.log` 的三個時間戳(sandbox step 確實跑了三次)。我當時沒把「第二次開火會做事」當成要驗的性質,是漏的。
「兩次開火只執行一次」的現象現在由 `test_a_second_fire_skips_a_cached_step_and_runs_an_uncached_one` 釘住(第一版只有
刪掉的探針,沒留證據)。

## P7(第三輪:三把鏡頭審 P2–P5)

回歸鏡頭有貨、真實性鏡頭有貨,都是我的:

| 發現 | 鏡頭 | 修法(先紅後綠;突變恰好各紅一條) |
|---|---|---|
| `test_run_routes.py:779` 還釘舊規則「壞檔被跳過」,分支上紅,CI 會紅(P3 後沒重跑整個 `tests/workflow`) | 符合度 | 改成新規則 |
| 聊天的 New 選單把壞檔列進去(`ItemChatShell.tsx:127`),選了 → 422「no such workflow」;P4 只教了面板 | 回歸 | 有 `problem` 的不列;vitest |
| Run 路由(`/run`、`/runs/preview`)對壞檔說「has no workflow」;WUI `startRun` → 502 + AssertionError | 回歸、符合度 | 兩扇門都用 `wont_parse()` 說同一句(422);wui 加 `workflow_problem_for` 接縫,app.py 接 façade |
| **`GET /schedules` 在 `offered` 閘門之前拿 `run` 當路徑讀檔**:NFS store 對 `../../x` 500、熱 sandbox 會讀到 workspace 外的檔 | 回歸 | `unparsable_workflow` 只把平坦 `<id>.json` 映成路徑(空 id、保留 id、含 `/` 的一律不讀);路由只問 `offered` 裡的 |
| 停在 gate 的 run 跨部署後 Decide/steer → 先寫決定再 assert → 500 | 回歸 | `WorkflowUnavailable`,在 `_resolve_manifest` 回 None 時、**寫任何東西之前**丟;路由 409 |
| sweep 新的每列讀取沒接例外,一個 store 抖動整個 tick 丟 traceback | 回歸 | try/except → say-once WARNING、跳過該列 |
| `docs/workflows-syntax.html` 14 個範例沒 `cache` | 符合度、真實性 | 補上 + 節點表加一列 |
| 假句子:「`authoring.py` 對 `run.py` 有 lint」、「排程列以前說找不到」、「每 tick 退回 window」(3 次後燒掉)、「每次開火一次讀」(壞列每 tick 一次)、「兩次開火只執行一次」沒證據、skill 段落位置、SHA、決定 6 的步數、「two starter templates」、`workflow_problem` docstring 的「exactly」、兩處 `runnable` 說明漏「檔解析得過」 | 真實性 | 逐句改;現象補測試釘住 |

沒動的(有理由):`workspace_workflow_metas` 是 `listing()[0]` 的投影、8 個測試呼叫點,不是第二套規則,留著;`GET /schedules`
每個 distinct `run` 一次 façade read 而非 `read_many_existing`——只問 `offered` 裡的、一頁幾列,人的動作;壞掉的 workspace
檔遮住同名 package workflow 時 Run 路由現在說「won't parse」(不再 fall through 跑 package 的),sweep 也跳過,兩邊一致了。

**要不要再一輪**:P7 沒換機制——新增的是守衛(一個例外類別 + 三處 `if manifest is None`、一個 id→路徑判準、兩扇門各一句、
一個 try/except、一個前端過濾),每條有先紅的測試、突變各紅一條。照規則不開;由 user 決定。
