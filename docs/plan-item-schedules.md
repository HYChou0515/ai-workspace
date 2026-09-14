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
- 以誰的身分跑:item 的 owner,自己的 run 對話(`_start_page_schedule`)。同頁面。

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
