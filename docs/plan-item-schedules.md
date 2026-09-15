# Plan — item 層級的排程:讓 AI 把自己做的 workflow 放上時鐘

一個一般聊天 item 裡,使用者跟 AI 說「每天九點跑這個」,AI 能把它做出來、
說得出下一次什麼時候跑,而且真的會跑。

## 起因

2026-09-12 使用者回報兩件事:

1. 跟 AI 說要設排程,AI 回「查過了,沒有 `schedule.json` 這種東西」。
2. 在 playground 做了一個 workflow `hourly-test-mail`,頁面按下去說
   **「This app does not offer hourly-test-mail to its pages」**。

兩件事的根源相同:排程機制(#788 WUI 第三輪)是為「**頁面**把工作放上時鐘」
設計的,不是為「**AI** 幫人設 cron」設計的,後者今天沒有入口。

## 現況盤點(master `3b8765a0`)

### 三個入口、兩套「哪些 workflow 可以啟動」的判準

| 入口 | 程式位置 | 認 profile 內建的 | 認 item 自製的 `.workflows/<id>.json` |
|---|---|---|---|
| Workflows 面板的 Run | `api/workflow_routes.py:125-143` `_workflow_manifest_or_404` | ✓ | ✓(同名自製蓋過內建) |
| 頁面 `startRun` | `api/wui_routes.py:393-401`,經 `workflows_for` | ✓ | ✗ → 403 |
| `schedules.json` sweep | `workflow/user_schedule_sweep.py:422-434`,同一個 `workflows_for` | ✓ | ✗ → 靜默 skip,只寫一行 server log |

`workflows_for` = `api/app.py:2247` `_workflows_for_item` → `profile_workflows(slug, profile)`,
「interactive profile 為空」。playground 的每個 profile 都沒宣告 workflow,所以一般聊天
item 的候選清單是**空集合**;`hourly-test-mail` 是 AI 用 `save_workflow` 存進
`.workflows/` 的,結構上就不在候選內。錯誤訊息把它說成「沒授權」,其實是 gate 根本不看那個
資料夾——也因此沒有任何設定能把它「授權」進去。

### AI 不可能知道有排程這回事

- `schedules.json` 的說明**只存在** `sample-skills/wui/`(SKILL.md L222-237、reference.md
  L228-301)。prompt、tool 描述、其他 skill 全部零提及。
- `wui` skill **沒有授權給任何 app**(`apps/shared_skills.py:52` 自述 "declared by NO app
  yet";五個 app.json 的 `agent.skills` 都沒有它)。AI 的 `## Available skills` 索引裡沒有這一項。
- 就算授權了,skill 的一行描述是「Build a WUI — a page/form/dashboard」;被問「每天九點跑報表」
  的 AI 不會打開一個講做網頁的 skill。
- 檔名是複數 `schedules.json`;AI 搜 `schedule.json` 找不到——最不重要的一層。

### 已經存在、這次直接沿用的

- **`.workflows/schedules.json` 現在就符合 `is_schedule_file`**(`api/schedule_index.py`:
  兩段以上、不在 `DEFAULT_IGNORES`),sweep 會照收,trigger key 只是把 folder 記成
  `.workflows`。item 層級的位置在 sweep 這層是**零改動**。
- **turn 結束對帳**(`api/schedule_reconcile.py:45` `reconcile_item_schedules`,接在
  `chat_send` 的 `on_turn_end`):列一次 workspace,把所有 `is_schedule_file` 的路徑登記進索引。
  用 `files.ls`,是整棵 sandbox 的 walk,不過濾 dot-folder(隱藏 `.workflows/` 是前端 IDE 樹
  自己做的),#802 沒動它。所以 AI 用 shell 寫的 `.workflows/schedules.json` 也會被登記。
- **時間語彙、檢查器、租約、上限**:`workflow/user_schedules.py`(`EVERY`、
  `validate_user_schedules`、`usable_rows`、`trigger_id_for`)、`_TriggerWindow` CAS 租約、
  `server.max_page_schedules`。全部共用,不新增第二套時間語法。
- 以誰的身分跑:item 的 owner;**一條排程重用同一個對話**(`chat_for_schedule` 以 trigger id 取得或建立,每次開火都寫進去)。同頁面。

### 前提(deploy 旋鈕)

`server.trigger_check_interval_sec` 預設 **0 = 整個排程功能關閉,而且沒有任何訊息**
(`config/schema.py:65`)。這不是這次要改的,但這次要讓它**說出來**(決議 8)。

### 和 `triggers.json` 的關係

workflow 那邊已有排程機制 `triggers.json`,但它**住在 repo 裡**
(`apps/<slug>/profiles/<profile>/triggers.json`),由開發者寫,每條寫死目標 `item_id` 與
`acting_user`,讀它的程式只從 repo 檔案系統讀。`schedules.json` 是它的「使用者側那一半」
(`user_schedules.py` 第一行:"the other half of `triggers.json`"),時間語彙直接沿用
`triggers.py` 的 `Schedule` struct。item 層級的 `.workflows/schedules.json` **不是第三套機制**,
是同一個使用者側的第二個宣告點。

| | `triggers.json` | `schedules.json` |
|---|---|---|
| 誰寫 | 開發者 | 使用者 / 頁面 / AI |
| 放哪 | repo 的 profile 資料夾 | workspace 裡 |
| 目標 item | 每條寫死 | 所在的 item |
| 以誰的身分跑 | 每條寫死 | item 的 owner |

**不**把新檔案叫成 `.workflows/triggers.json`:形狀不同(dev 版有 `item_id`、`acting_user`、
`type` 標籤、event 觸發),同名會讓人以為可以寫 dev 版欄位進去,然後靜默被跳過。

## 決議(grill-me,2026-09-14)

| # | 題目 | 決議 | 否決的替代 |
|---|---|---|---|
| 1 | 宣告點 | **一套規則、兩個位置**:頁面 `<page>/schedules.json` 不動;item 層級 `.workflows/schedules.json`。validator / sweep / gate / max_rows 全部共用 | 只留 item 層級——頁面的 `writeFile` 被關在自己資料夾裡,要動 bridge 契約;根目錄 `schedules.json`——`is_schedule_file` 明文排除根目錄,多一條路徑規則 |
| 2 | 誰能被啟動 | **三個入口共用一個 resolver**:`offered_workflows(item_id)` = profile ∪ workspace,同名 workspace 蓋過 profile(沿用 orchestrator 語意)。砍掉 `_workflows_for_item` 和 `_workflow_manifest_or_404` 裡各自拼的那段。安全面沒放寬:workspace workflow 在 `save_workflow` 時已對 profile 的 tools 上限驗過,面板早就讓任何有 item 權限的人跑它 | 只改排程、頁面維持 profile-only——同一個 id 頁面說沒提供、排程跑得起來,兩套規則並存 |
| 3 | AI 怎麼寫 | **新工具 `save_schedules`**:先檢查再存,問題回給 AI 修(同 `save_workflow` 的形狀)。走 façade,所以會被索引 | 直接 `write_file` + skill 叮嚀格式——一列寫錯靜默跳過,叮嚀擋不住靜默失敗 |
| 4 | AI 怎麼知道 | 三處:`author-workflow` skill 加一節(五個 app 都授權、自動化這件事的必經入口);`save_workflow` 存成功的回覆句提一句;**格式寫在工具說明裡**,`every` 的值從 `user_schedules.EVERY` 產生、不手打。授權同 `save_workflow`:app.json 列了它的就加 `save_schedules`。`wui` skill 頁面那份說明不動(頁面不能呼叫工具) | 把 wui skill 授權出去——描述不會讓 AI 為了排程打開它 |
| 5 | 人怎麼看 | **Workflows 面板**加「排程」區:每列顯示週期/時間/時區/跑哪個/下次時間;`run` 指到不存在的 workflow 標紅;每列「移除」= 前端讀檔、拿掉那列、用既有檔案寫入 API 寫回(不新增後端路由,自然走到會被索引的那條路)。不做新增/編輯表單 | 不顯示——AI 說「設好了」你只能信它;`.workflows/` 在檔案樹是隱藏的 |
| 6 | shell 寫的檔 | **不做**——master 已有 turn 結束對帳,且 `files.ls` 看得到 `.workflows/`。只補一條測試釘住這條路徑 | 在 mirror 加 hook——正式環境(host-managed durable)`registry._writeback` 走 `sandbox.persist`,碰不到 mirror;plan-wui.md P40/P48 已踩過 |
| 7 | 覆寫語意 | **整份換掉**:參數是完整清單,呼叫一次 = 檔案變成這份。要加一條,AI 先 `read_file(".workflows/schedules.json")` 再連舊的一起傳(工具說明會講)。回覆回顯清單 + 各列下次時間 | 提供 add/remove——兩種語意並存讓 AI 猜;整份覆蓋的結果永遠等於它最後說的那份 |
| 8 | 部署沒開排程 | **照存,大聲說**:工具回覆和面板都顯示「這個部署沒有開啟排程,存了也不會跑,請找管理員」 | 拒絕存——使用者得先找管理員再回來重講一次;檔案本身無害,開了就開始跑 |

## Phase

平整整數,每個 phase 一個 commit,TDD(先有會紅的測試)。

- **P1 統一 resolver。** `offered_workflows(item_id) -> list[WorkflowManifest]`(或 id 清單 +
  manifest 查詢)在 `api/app.py` 定義一次;`wui_routes` 的 `startRun` gate、
  `user_schedule_sweep` 的 `workflows_for`、`workflow_routes._workflow_manifest_or_404` 三處改用它;
  刪掉 `_workflows_for_item` 與 `_workflow_manifest_or_404` 裡自己拼的那段。sweep 這條是
  `asyncio.to_thread` 呼叫的同步函式,而讀 `.workflows/` 是 async 的 `files.ls`——介面要一併調整,
  不要在 thread 裡開 event loop。
  紅測試:頁面 `startRun` 一個只存在於 `.workflows/x.json` 的 workflow,現在是 403;sweep 一列
  `run: x` 現在被 skip。兩條都要先紅。
- **P2 `save_schedules` 工具。** `agent/tools.py`:參數 `schedules: list[dict]`;
  `validate_user_schedules` 檢查格式;每列 `run` 對 P1 的清單驗(不存在 → 回「先用
  `save_workflow` 存它」);超過 `max_page_schedules` → 拒絕;`files.write` 整份寫
  `.workflows/schedules.json`(canonical JSON,和頁面寫的同形);回覆:存了幾列、各列下次觸發時間
  (用 `fire_window` 算)、部署未開排程時的警告(`trigger_check_interval` 是否為 0 要從
  `create_app` 傳進工具 context)。`TOOL_VERBS["save_schedules"]` 授權表(同 `save_workflow`);
  五個 app.json 加上它。
- **P3 讓 AI 找得到。** `sample-skills/author-workflow/SKILL.md` 加「要定時跑」一節;
  `save_workflow_impl` 存成功的回覆句加一句;`save_schedules` 的工具說明含格式表,`every` 那格
  從 `EVERY` 產生。測試:工具說明裡列的 `every` 值 == `EVERY`(突變任一邊會紅);skill eval
  scenario(`sample-scenarios/author-workflow/`)一條「使用者要每天跑」→ `must_call: save_schedules`。
- **P4 Workflows 面板排程區。** ~~後端:meta 多回 `schedules_enabled`;前端讀檔、自己算下次時間~~
  **實作時改掉**:「下次時間」若在前端算,等於用 TS 再寫一份 `period_target`/`next_run`(兩套
  規則),而且看不到租約帳本(已經跑過這一期的列會被說成「下一輪」)。所以後端開一個
  **`GET /a/{slug}/items/{id}/schedules`**:用 sweep 同一個 parser、同一個 `next_run`、同一個
  帳本,回每一列(含被 linter 拒絕的列,帶 `raw` 和 `problems`,檔案順序)+ `known`(`run` 在
  P1 清單裡)+ `next_at`/`due_now`/`tz` + `enabled`。`schedule_views` 是唯一的解讀實作,
  `save_schedules` 的回覆和這個路由都用它。前端只渲染:週期詞彙由 `raw` 在地化(純字彙,不算
  時間)、`known=false` 標紅、`due_now` 顯示「下一輪」、`enabled=false` 警告條、「移除」= 前端用
  既有檔案寫入 API 把整份檔案少那一列寫回(保留其他列,壞列也保留)。FE 測試八條:列出/空/
  run 不存在/壞列/到期/未啟用/移除寫回/取消不寫。
- **P5 釘住與文件。** 測試:用 sandbox `exec` 寫 `.workflows/schedules.json` → turn 結束 → 索引有它
  (它一到就綠,守的是新路徑)。`docs/workflows.md` §22 加一段「定時跑」;`docs/migrations.md`
  記「無 config 變更;`trigger_check_interval_sec` 仍是前提」;`docs/wui.md` 若有列舉宣告點要同步。

**Live check(DoD 的一部分,不是可選):** 新 worktree 起 app(`WORKSPACE_TOOLS_DIR`、等 30 秒),
`trigger_check_interval_sec` 調成幾秒,在 playground 開一個 item:AI 存 workflow → AI 存排程
(回覆有下次時間)→ Workflows 面板看得到那列 → 等一個週期 → run 對話出現、workflow 真的跑了 →
面板按「移除」→ 下個週期不再跑。每一步截圖或 log 貼進 PR。

## 範圍外(記下,不做)

- workflow run 結束不接 turn 對帳:排程跑的 workflow 用 shell step 自己寫新的 `schedules.json`,
  要等下一次聊天 turn 才被登記。既有小縫,與這次目標無關。
- 頁面用 shell 寫的任意深度排程檔:頁面本來就用 `writeFile` → PUT 寫,已有索引。
- 面板裡新增/編輯排程的表單:那是 AI 的工作。
- `trigger_check_interval_sec` 的預設值:不改,只讓它說出來。

## 已知限制(沿用 plan-wui.md,不重述理由)

- DST 回撥那一小時,sub-daily 排程少跑一小時份。
- 改一列 = 新排程,可能在同一週期再跑一次。
- 刪掉排程的對話不會停掉排程;要停就移掉那一列。

## 第一輪 review(2026-09-14,P1–P5 之後,四條,全部成立 → P6)

判準照 plan-wui.md:看「幾條源自上輪修法」。這一輪 4 條裡 **4 條都是 P1/P4 引進的**(表格「誰造成的」
欄:P1、P1、P4、P4;之前寫成 3 條是算錯),全在沒有測試看著的地方:

| 找到什麼 | 誰造成的 | 修法 |
|---|---|---|
| sweep 的 `workflows_for` 走 façade 的 `files.ls`(warm-first)—— 正式環境 `kind: http` 上每 tick 把被回收的 sandbox 重建一次,正是 sweeper 用 `read=filestore.read` 刻意避掉的事,一個參數之隔又放了回來 | P1 | 規則仍只有一份(`offered_workflow_ids`),**listing 由呼叫端交進來**:request 給 façade 的 `ls`,sweep 給 durable store 的;`read=` 旁的原文守衛擴到 `workflows_for`(突變回 façade 會紅) |
| `.workflows/schedules.json` 被列成一個叫 `schedules` 的 workflow —— 工具與 gate 接受、orchestrator 跑不動、面板的 Run 清單(有 parse)又不列,「一份清單」和面板由構造上就不一致 | P1 | 一個判準 `is_workspace_workflow_path`(平坦 `.workflows/<id>.json`、排除 `schedules.json`)給 id 清單和 manifest 清單共用;`SCHEDULES_FILE` 搬到 `workspace_store`(和 `WORKSPACE_WORKFLOW_DIR` 同一個葉模組),拼一次、無循環 import |
| 面板「移除」用 ≤30s 的快取整份重寫 —— 面板開著時 AI 用 `save_schedules` 加的列會被靜默寫掉,正是程式碼註解宣稱避免的事 | P4 | 移除前 `fetchQuery(staleTime: 0)` 重讀,用 `sameShape` 認列(索引可能位移),列已不在就什麼都不寫 |
| 缺 `every` 的列後端當 daily、前端畫 `?` | P4 | 前端 `undefined` 走 daily 分支 |

live check 是在 P6 之後做的(`docs/plan-item-schedules.md` 上方的 DoD),結果貼在 PR #805。
**AI 那半沒有 live 驗到**:本機只有 CPU-only 的 `qwen3:8b`,兩次都在 litellm 600s 內出不了第一個
token;工具由 11 條單元測試走真函式覆蓋,`sample-scenarios/author-workflow/` 兩個情境是有模型的
部署該跑的 live check。

## 第二輪 review(2026-09-14,P6 之後,三條,全部成立 → P7)

3 條裡 **1 條源自上輪修法**(P6 在讀端保留了 `schedules` 這個名字,寫端沒擋),其餘兩條是 P4 的縫。
~~沒有換機制,只加守衛,所以不再開第三輪~~ **這句講太滿**:第三條改了 BE→FE 契約(`raw` 從 dict 變任意 JSON
值),第二條把 sweep 的上限分支換成共用函式;user 一句「還需要一輪嗎」之後開了第三輪,見下。

| 找到什麼 | 誰造成的 | 修法 |
|---|---|---|
| `save_workflow("Schedules")` slug 成 `schedules`,寫進 `.workflows/schedules.json` 把整份排程蓋掉,而那個 workflow 又跑不動也列不出 | P6 | `RESERVED_WORKFLOW_ID` 在寫入咽喉點 `save_workspace_workflow` 拒絕(每個呼叫者都受約束),工具先攔並指向 `save_schedules` |
| 列表路由沒套 sweep 的整檔上限:超過 `max_page_schedules` 時 sweep 一列都不跑,面板卻每列給「下次」 | P4 | `over_cap(raw, max_rows)` 一句話,sweep 的 log 和路由的 file-level `problems` 共用 |
| 非物件的列被換成 `{"_": 5}` 寫回 | P4 | `raw` 保留原 JSON 值(`Any` / `unknown`),前端描述時才降級,重寫時原樣寫回 |

## 第三輪 review(2026-09-15,P7 之後,三把鏡頭平行:回歸 / 真實性 / 符合度)

user:「還需要一輪嗎?」——需要;而且 user 接著說「我沒辦法接受 review 超過 3 輪還需要……施工品質太差」。
這輪的發現形狀相同:**寫下去的當下就能自己抓到**(宣稱沒有測試支撐、docstring 說的路徑不是測試走的路徑、
守衛零覆蓋、判準數字算錯)。P8 的施工規則因此改成:每條修法先有走宣稱路徑的紅測試;「A 和 B 一樣」只能用
parity 測試支撐(sweep 是 oracle);每句面向人的話逐句對程式碼;推前跑 coverage 與自己的三把鏡頭。

| 找到什麼 | 鏡頭 | 誰造成的 | 修法(P8) |
|---|---|---|---|
| 路由和 sweep 讀法**不一樣**四處:未索引的檔路由列成會跑;非 UTF-8 路由 500、sweep 用 replace;超上限每列仍給「下次」(**這條是 P7 自己宣稱修了卻只加了檔案層級句子的殘留**);`every: null` 前端畫 "null" | 真實性、回歸 | P4/P6/**P7** | **parity 測試**(`test_schedules_route_parity.py`):同一份檔餵路由與 `sweeper.tick()`,斷言「路由 `runnable` 的列 = sweep 開火的列」,8 種輸入 + 未索引;後端 `schedule_views` 算出 `runnable`(無問題 ∧ 認得 ∧ 未超上限 ∧ sweep 開 ∧ 已索引),只有 runnable 才有「下次」;路由 `decode("utf-8","replace")`、回 `indexed`;`usable_rows`/`schedule_views`/`declared_count` 共用 `file_rows`+`parse_row`(之前是兩份逐列解析) |
| 「每次執行開新對話」——假的,一條排程重用同一個對話 | 真實性 | P2/P3/P4 | 工具回覆、skill、i18n、plan 改成事實;PR 留言在 push 時更正(commit 改不到 PR) |
| `raise ReservedWorkflowId` 零覆蓋;範本路由碰到會 500;`resolve_offered_workflow` 沒套保留名判準 | 回歸、符合度 | P6/P7 | 直接打咽喉點的測試;範本路由 422;resolver 套 `is_workspace_workflow_path` |
| Remove 用 `sameShape`(陣列當集合)認列,只差陣列順序的兩條排程會刪錯 | 回歸 | P6 | 改成保序的 `sameJson`;測試:兩列 `with.ids` 順序相反,按第二列只刪第二列(突變回集合語意會紅) |
| P5 釘住測試沒走 exec 路徑、docstring 卻說涵蓋 | 符合度 | P5 | 兩條測試、各說各釘的東西:**直接寫進 store**(無 sandbox,只有對帳能索引;量過:對帳改 no-op 就紅)和**寫進熱 sandbox**(這種部署 mirror 的 `on_write` hook 先索引,釘的是那扇門不是機制);斷言都在 `TestClient` 裡面——第一版斷言在外面,被 shutdown 的 writeback 滿足,對 reconcile 任何突變都不紅 |
| `wui_routes` 的 "This app does not offer … to its pages" 就是起因裡的誤診句;`wui/reference.md` 兩句 `run` 規則是 P1 前的 | 符合度 | P1 | 改成「This item has no workflow named X(it has: …)」;reference.md 兩句改成 P1 後的規則 |
| plan「4 條中 3 條源自 P1/P4」算錯(4/4);「P7 只加守衛」講太滿;P3 commit 說情境「用真模型釘住」但沒跑過;PR body 說 7 個 commit(8) | 真實性 | 我的宣稱 | plan 已更正;PR body 在 push 時更正 |
| 面板 Run 在 master 是 profile 優先、P1 改 workspace 優先,未列進 behaviour changes | 真實性 | P1 | PR body 在 push 時補列 |
| 匯入 `schedules.json` 後排程區 30 秒內是舊的 | 回歸 | P4 | 匯入後一併 invalidate |

## P8 自審(推之前,三把鏡頭審 8b1298cd 的 diff)

新規則「推前先自己審」抓到 **15 條**(去重),全部修進 P8 才推。判準「幾條源自上輪修法」:15 條裡
**15 條都是 P8 自己的**——這輪的 review 找到的是我這輪寫的東西,不是舊債。最重的三條:

- **釘住測試的斷言在 `TestClient` 外面**,被 shutdown 的 writeback 滿足,對 reconcile 的任何突變都不紅——
  和 P5 同一個形狀,發生在宣稱要改掉這個形狀的那一輪。修法:斷言搬進 `with`、逾時就紅;拆成兩條各釘一扇門。
- **parity 的 "all good" 案例 5 次紅 1 次**:同一 tick 開兩個 workflow,第一個喚醒 sandbox,第二個的 manifest
  被 façade 導到還沒 restore 完的 sandbox(master 既有的 `_warm` 不看 `.ready`,memory 標「未修」)。
  修法:tick 前先 `/exec` 喚醒;重跑 10 次 10 綠。
- **`runnable` 的 `enabled` 門沒有測試守**(突變掉它 37 條全綠):補路由、工具、views 三條(突變後三條紅)。

其餘:超上限句子塞進每列 problems 讓面板把合法列標成「寫得不對」(改回檔案層級一句);索引讀不到路由 500
(改「未索引」+ log);`load_workspace_workflow` 本身拒絕保留名(orchestrator 那扇門也蓋到);
`no_such_workflow` 一句話供頁面 403、工具拒絕、sweep log 三處共用(頁面那句不再出現工具名);
`every`/`at` 的 falsy 值對齊 Python 的 `or`;匯入後 invalidate 補測試;`app.py` 的「opens its OWN
conversation」docstring;§22.11 補 `runnable`/未索引告示/5 秒窗口;第三輪表格三處歸因與完成式更正。

**live check(短的)**:見 PR 留言——面板一列有「下次」;直接寫進 store 的檔出現「還沒登記」告示;送一個 turn
後告示消失。開火那條鏈沒再動,不重跑。
