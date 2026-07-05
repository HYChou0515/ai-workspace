# Plan — #429 Workflow 引擎:三個待補缺口(+ 第 5 節雜項)

> 本文是 `/grill-me` 對 #429 的定案紀錄。範圍 = **既有 workflow 引擎的行為缺陷/缺口**;
> 姊妹 issue #428(宣告層語法擴充)已 MERGED,本文多處引用其定案。
> 排程遵守 flat-integer phases(P1、P2、…),每完成一相位 commit 一次。

## 探勘校正(動手前先釐清現況,因為 issue 文字有出入)

grill 前的三份 code 探勘改變了 #429 的樣貌,先記錄事實基礎:

- **Gap 3(entity 寫入 capability)大半已落地。** `create_entity` 已在 DSL
  `CAPABILITIES`(`src/workspace_app/workflow/dsl.py:39`),經 `wf.create_entity`
  (`handle.py:222`)→ `EntityStore.create`,與 UI/agent **同一條配號+驗證管線**。
  agent 側 `create/update/query/link_entity` 四工具都在,PM app 亦已 grant。
  **DSL 缺的只有 `update_entity`**(`EntityStore.update`(`store.py:110`)已存在且帶
  樂觀版本檢查 `EntityConflict`)。→ 見 **P2**。
- **Gap 1/4(cache 失效)引擎行為確認。** `input_hash`(`engine.py:58`)只 hash step 的
  `args`;`map over glob`(`dsl.py:385`)成員以**路徑**為 key,**內容不參與**。→ 見 **P1**。
- **Gap 2(觸發器)是唯一大工。** 全專案無 cron 函式庫;workflow run 是 orchestrator 監管的
  **in-process asyncio task**(`orchestrator.py:213`),不進耐久 job queue。現成可接的機制:
  時間=`code_sync` 的 poll-loop + 掛鐘 gate(`lifecycle.py:91` / `code_repo.py:238`)、
  多 pod 選主=blob_gc 的 CAS lease、事件=specstar `event_handlers`
  (`index_coordinator.py:190` reindex-on-edit)。→ 見 **P6–P9**。

---

## 相位總覽

| Phase | 主題 | 動引擎? |
|---|---|---|
| P1 | cache 內容感知:`reads` 宣告式欄位 + stale-risk lint | 是 |
| P2 | `update_entity` DSL capability(順解「平行 run 撞同一 entity」) | 是 |
| P3 | gate vs steer 撰寫文件 | 純文件 |
| P4 | journal 孤兒 lazy-GC | 是 |
| P5 | per-element sub-handle 真平行 | 是 |
| P6 | 觸發器宣告層(`triggers.json` schema + validate) | 宣告/驗證 |
| P7 | 時間觸發 runtime(sweeper + CAS lease + start) | 是 |
| P8 | 孤兒重拾 + 時效 abandon | 是 |
| P9 | 事件觸發(event_handlers + where + 遞迴 guard + 水位) | 是 |
| P10 | agent 工具寫入接上 event dispatch(補齊單一寫入路徑 + origin 傳遞) | 是 |

---

## P1 — cache 內容感知:`reads` 宣告式欄位

**問題**:step skip 條件是 `input_hash(args)` 命中;若某步讀的是**檔案路徑**、但檔案**內容**變了、
而 args 只含路徑 → hash 不變 → 錯誤 skip → 拿到過期結果(靜默)。`map over glob` 最危險。

**定案**:不讓引擎自動追檔案相依(做不乾淨、又變魔法);改用一個**宣告式欄位 `reads`**,把
「維護 cache 正確性」的責任**從作者移到引擎**——這是本 phase 的核心,不是純文件。

### `reads` 欄位(新,碰引擎)

```jsonc
{ "type":"sandbox", "run":"python analyze.py", "reads":["logs/*.log"] }
```

- 引擎看到 `reads` → **自動**算那些檔的內容指紋、摺進 `input_hash`。作者**不用**自己
  interpolate 指紋進指令字串。
- glob 命中多檔 → 全部檔的內容指紋一起摺進去(這給了 `map over glob` 乾淨的內容感知失效)。
- 跟「rule 2 手動餵指紋」的差別:手動是把正確性外包給作者「記得做、且做對」;`reads` 是
  作者只**宣告意圖**「我讀這些」,引擎負責變成正確 hash——作者不可能算錯或漏掉。
- `validate_def` 靜態檢查 `reads` 的路徑形態(呼應「宣告即可驗」)。

### `workflow check` 的 stale-risk lint

lint **保守、低噪音、明說是啟發式**,絕不 parse 不透明指令去猜它讀什麼(不可解、又漏又吵、
會訓練大家無視警告)。只用便宜訊號 **WARN** 兩種型態:

1. 一步**既沒宣告 `reads`、也沒 `cache=False`**,而 `run` 字串出現路徑樣 token(含 `/`、
   `*.log`、副檔名)→ 提醒「可能讀了檔卻沒宣告」。
2. `map over glob` 的 `do` 內有 sandbox step 卻沒 `reads` → 最高風險型態,特別提醒。

- **stale-risk 永遠 warning,不升 error 擋存檔**——「沒宣告 reads」很多時候是對的(該步本就
  不依賴檔案內容),擋下去是誤傷。
- **旋鈕**:預設 `warn`;**嚴格模式(專案 opt-in)= sandbox step 一律必須表態**(宣告 `reads`
  或明寫 `cache=True/False`),否則 error。強制的是「必須表態」,不是「表態成什麼」。

### authoring 文件(三規則,附優先序)

1. **args 沒有的東西,對 cache 不存在**(讀檔節點的檔案內容不會自己進 args)。
2. **要讓內容參與 cache → 宣告 `reads`**(首選正解,引擎代算指紋)。
3. **判斷不了或算指紋比重算貴 → `cache=False`**(保險,誠實每次重跑)。

優先序:`reads`(首選)> `cache=False`(保險)> 手動餵指紋(下策,連 reads 都不想列時)。
附兩個對比例子(含 `reads`=安全 / 只含路徑=會吃到舊結果)。

---

## P2 — `update_entity` DSL capability

**問題**:DSL 只有 `create_entity`,沒有 `update_entity`;workflow 想改 PM 的 entity 只能走
`run.py` 的 raw `wf.write`(繞過配號+驗證),破壞「單一寫入路徑」。

**定案**:補 `update_entity` capability,語意=**內部 read-modify-write + 樂觀衝突重試**
(選項 A),**不對作者暴露 `expected_version`**:

```python
wf.update_entity(type, number, patch):
  for _ in range(N):
    cur = store.read(number)
    try:
      store.update(type, number, patch, expected_version=cur.version)   # merge-patch 絕對值
      break
    except EntityConflict:                                              # 平行 run 動過
      continue                                                         # 重讀重試
  journal key = f"{type}_{number}_{digest(patch)}"                     # 同 patch 重跑 idempotent skip
```

- 對齊 `create_entity` 的 journaled/idempotent 契約。
- **順手解掉 gap 5「兩個平行 run 同時改同一 entity」**——用既有 `EntityConflict` 樂觀鎖,不需
  另造鎖機制。
- `link_entity` 是「update 一個 ref 欄位」的特例,可由 `update_entity` 表達;是否另立 DSL
  capability 視需要決定(非必需)。

---

## P3 — gate vs steer 撰寫文件(純文件、行為不改)

`workflows.md` §22.7 已定並存分工,無行為要 reconcile。P3 = 把「**何時用哪個**」寫進 authoring:

- **gate**:作者設計、in-flow、有界暫停,**選單式**預定選項(approve/revise/reject);
  revise=有界回邊到具名步 + feedback。用於流程裡**本來就有**、每次都需人簽核的已知決策點。
- **steer**:平台級、out-of-flow、**自由文字** overlay,任意時點、任意重導(編輯 inputs +
  invalidate 步)。用於作者沒預期的臨時介入。

**判準(接「圖能否事先靜態畫出」總線)**:
> 「這個暫停點,是我寫 workflow 時就**畫得進圖裡**的嗎?」畫得進(發佈前一定要審)→ gate;
> 畫不進(要等 run 跑歪、看到才知道要介入)→ steer。

**決策表**加一欄「**留痕/可重播**」:gate 的 revise 走 journal、明天再跑會重現;steer 是
out-of-flow overlay、一次性不重現。幫作者判「這介入要不要每次都發生」。

**各一正例 + 一反例**:
- gate 正例:發佈前審週報。反例:**不要**用 gate 接「我臨時想改個方向」(不是每次都要的簽核,
  硬塞會讓每次跑都卡一個只有這次要的關)。
- steer 正例:跑到一半發現方向錯、自由重導。反例:**不要**用 steer 做每次都該有的簽核(那該
  設計成 gate,靠 steer 等於把計畫內關口變成「要記得每次手動介入」,會漏)。

---

## P4 — journal 孤兒 lazy-GC

**問題**:map 的 `items` 縮小時(glob 31→30),舊元素的 `step_<name>/<key>.json` 無人清理,
隨重跑累積成垃圾。

**定案(P4a)**:named-map 重跑、解出當前元素集後,prune `step_<name>/` 底下**不在當前集合**的
key 檔。清理綁在「map 重跑」這個天然時機,零常駐(不需另立 GC 週期)。

- transient glob 縮小(檔暫時消失又回來)→ 該元素 cache 掉、回來時重算=**cache loss 不是正確性
  bug**,可接受。
- 跟 fan-in null 佔位不衝突:被 prune 的是**整個離開集合**的元素(下游 re-map 新集合本就不含
  它);留在集合內但 skip 的元素照留佔位。
- **實作要防的手滑**:GC 只在集合**真的變小**時刪、只刪**確定離開**的 key(不是「這次沒解到就
  刪」),避免解集合暫態誤刪;與 revise 撞時(revise 重跑、集合不該變)也靠此守住。

---

## P5 — per-element sub-handle 真平行

**問題**:`wf.map` 的元素若含 **agent turn**,被 ChatTurnEngine 的 **FIFO-per-key**(每
item/conversation 一 lane)序列化 → 平行 map 對 agent 是假平行。sandbox node 本來就平行,
**瓶頸只在 agent turn**。#428 §7 定調:引擎給「per-element sub-handle」(每元素自己的 turn
key),DSL map 可**透明改用、無語法變更**。

**定案**:

- **DSL `map` 預設平行**(非 opt-in)——map 契約本就「skip+collect、順序無關」,平行是透明淨勝;
  side-effect 撞共享 collection/entity 由 **P2 樂觀重試 + 冪等**擋住。
- **`concurrency` cap 預設值由 model backend 並發能力推導**(**不是固定常數**):hosted/多
  replica → 大(真平行);本地單模型 → 趨近 1(自動退化序列,不製造假並發)。**同一份
  workflow.json 跨環境不改**。
- **cap 語意 = 「並發上限請求、受模型並發節流」**(`min(resource_cap, model_concurrency)`,
  request 非 guarantee)。文件誠實寫明(本地設 8 沒變快 = 模型端排隊,不是 bug)。
- **隔離**:sub-handle = 獨立 turn key +(重用現有)per-element 短命 sandbox;journal 路徑
  `step_<name>/<key>.json` 不變。
- **串流**:sub-turn 事件串進父 run SSE、掛 map phase node 底下(「12/20 · 1 failed」)。
  **skip+collect** 語意不變。
- **取消 ≠ 無差別 kill**:取消 = 停派新元素 + **等 in-flight 的 side-effect 步落盤**(或依 P2
  冪等)+ 砍純 agent turn。免得取消變成孤兒 side-effect 的新後門(與 P2 的 act-vs-落盤窗同源,
  觸發源換成人為取消)。
- primitive 也曝給 `run.py`。
- **card upsert 一併套 P2 樂觀重試**(一致性):`upsert_context_card` 有 revision,套同招近乎
  零增量成本;避免「entity 保證不 lost-update、card 卻 LWW」這種「看 resource 而定」的設計氣味。
  收尾語意與 entity 對齊(衝突 → StepFailed → map 內 `failures[]` → skip+collect,不覆蓋、不
  靜默)。

---

## 觸發器(P6–P9):決策樹 A–F

> gap 2。整體架構定案:**v1 走 in-process**(sweeper + CAS lease 選主 + `orchestrator.start()`),
> 重用現有 run path / SSE / one-active-run,不新增 JobType。但因觸發是**無人看管**,加一套
> 「孤兒可被下個 window 撿回 + 可被發現」的補救,否則 in-process 中斷 = 靜默漏 job。

- **A 執行路徑**:v1 in-process + CAS lease `(trigger_id, fire_window)` 選主 + sweeper 認孤兒。
- **B 宣告形式**:profile 級獨立 `triggers.json` + template/instance overlay;**使用者自排延後**
  (綁 per-user quota)。
- **C cron 規格**:受限結構化 period 排程 `{every,at,dow?,dom?,tz?}`,**不用 full cron**(cron
  的「瞬間觸發」語意與 poll-loop 容忍模型相剋;period 讓 `fire_window`=期別,A 的 lease/孤兒重拾
  才有乾淨的鍵)。
- **D 事件範圍**:v1 只收 entity create/update + 宣告式 `where`;檔案變更/SSE 延後。
- **E captured_user**:**E-decl**(trigger 宣告的固定 user);**actor 是資料、acting_user 是授權**
  (E-event 違反 Q4 且有 confused-deputy 提權面,相剋不能選)。
- **F 撞 one-active-run**:自孤兒 resume / 別人 skip / 同窗 no-op;先還舊債(先 resume 孤兒);
  孤兒有時效(abandon 可查)。

### P6 — 觸發器宣告層(schema + validate,無 runtime)

profile 級 `triggers.json`(或 `triggers/*.json`),template/instance overlay 可覆寫(部署時
開關、改時間、改 acting_user)。每條綁 `schedule|event → workflow_id + acting_user`。

C2 schema:`{ every: daily|weekly|monthly, at:"HH:MM", dow?, dom?, tz? }`

`validate_def` 靜態檢查:
- **`dom` 超過當月天數 → clamp 到月底**;`dom > 28` 出提示「將以月底為準」(否則月報靜默漏月)。
- **`every`↔`dow`/`dom` 合法組合**:`daily`→只 `at`;`weekly`→必 `dow`;`monthly`→`dom` 可選
  預設 1。非法組合=靜態錯。
- **`acting_user` 必填**(宣告時第一道)。

**已知限制(標進 issue)**:`tz` 開放 DST 時區時,「不存在/重複本地時刻」語意未定 → v1 限無 DST
tz 或標為 known limitation。full cron 日後當**加法擴充**(schedule 也接受 cron 字串),不破壞 C2。

### P7 — 時間觸發 runtime

- **sweeper**:API lifespan 加一個 poll-loop(照 `code_sync`),每個 window 用
  **once-per-period + 過了 target 才補觸發**的 gate(`現在 ≥ 本期 target 且 上次觸發 < 本期
  target`)——對 poll 節奏寬容、錯過的 window 會補跑。
- **選主**:每個到期觸發器用 **CAS lease per `(trigger_id, fire_window)`**(照 blob_gc lease),
  贏的 pod 起 run。`fire_window` = 期別(週=`2026-W27`、日=`2026-07-04`、月=`2026-07`)。
- **起 run**:`orchestrator.start(captured_user=trigger.acting_user)`;背景寫入走 #186 的
  `rm.using(acting_user)`,specstar `created_by/updated_by` 歸 acting_user。
- **captured_user 第二道(執行時)**:`start` 收到空 captured_user → **fail-loud 拒跑,絕不
  fallback system**(防 plumbing 掉值的授權版靜默出錯)。

### P8 — 孤兒重拾 + 時效 abandon

- **孤兒**:上個 window 起了、run 既非 completed 也非 awaiting_human、且**無 pod 持有其 lease** →
  當作該 window 未完成,靠 journal **resume 到斷點**。lease 帶 `(trigger_id, fire_window)` → 能
  分辨「我自己的孤兒」vs「別人的 run」。
- **F 撞 active run**:自孤兒 → resume(不重開);別人的 run(不同 trigger/使用者手動/別 pod live
  lease)→ **skip**(下個 window 重評估補上,skip 因水位可被發現);同窗 live → no-op。
  **skip-not-queue**(週期觸發本會再到期,queue 會堆積)。
- **F-1**:item 空閒且孤兒+新窗並存 → **先 resume 孤兒**(先還舊債;否則新窗永遠插隊、週期觸發
  累積隱形赤字)。
- **F-2 時效**:孤兒逾期 → 標 **abandoned-但可查**(進 D2d 可查機制,不刪=靜默、不無限拖)。
  - **時效單位跟觸發型態走**:cron = `N 個 window`(預設 1~2);event = **絕對時間 TTL**(event
    window 無規律間隔)。
  - **落地映射(誠實記錄)**:cron 的「N 個 window」以 **N 次 resume 嘗試**實作
    (`max_resume_attempts`,預設 2)——每個 sweep tick 對孤兒補一次 resume 並記一次;attempts 用
    盡即 abandon。孤兒偵測沿用 #227 慣用法:run RUNNING 但 `progress_at` 心跳過 `grace_ms`
    (預設 1h)即判 stuck;resume 走 `expected_etag` CAS 選單一 pod 重驅動,journal 讓已完成 step
    skip(冪等)。abandon = 終態 `error` + `result.abandoned` 標記 + 釋放 sandbox + notify_failure
    (可查、可從清單發現)。
  - **abandon = 單向落盤一次的狀態轉換**:判定一次寫死、不在邊界 active↔abandoned 抖動;重跑靠
    人從可查介面手動觸發(呼應「裁決落盤、重跑沿用」)。

### P9 — 事件觸發

- **⚠️ 探勘校正(落地時修正原設想)**:#419 entity 是 **file-first**(經 `EntityStore` 寫進
  `WorkspaceFile`;warm workspace 還先走 sandbox、根本不派 specstar event),所以「掛 specstar
  `event_handlers` 在 entity resource 上」**不成立**。改掛在**單一寫入路徑本身**——`EntityStore`
  `create`/`update` commit 後、in-request、在寫入 pod 上 emit 一顆 `EntityWriteEvent`(型別/編號/
  version/fields/actor/origin 都齊),由 `EventTriggerDispatcher` 消費。這保留了計畫要的「一次、就地」
  語意,只是換到 file-first 對的 seam(呼應本計畫「宣告即正確、對齊現況」原則)。型別/watermark 存
  `workflow/event_dispatch.py`;event schema + loader 在 `triggers.py`。
- **機制(修正後)**:`EntityStore` 寫入 → `on_write` sink → dispatcher,post-commit、in-request、
  在處理該寫入的 pod 觸發一次。
- **過濾**:trigger 宣告 `on: entity.<type>.created|updated` + 可選 `where`(欄位/狀態轉換條件,
  避免每次瑣碎編輯都觸發;由 `OnSuccessPatch` narrow)。
- **遞迴 guard(必做)**:
  1. triggered run 寫 entity 時打 run 標記 → event handler **跳過「由 workflow-run 造成的變更」**
     (擋直接自迴圈 A→A)。
  2. **全域觸發鏈深度上限**(擋間接環 A→B→A;actor 標記擋不住,因每步 actor 都是「某 run」)。
  3. 完整 origin 鏈追蹤 → follow-up;但**深度上限一定要有**。
- **投遞保證(D2d,不接受 best-effort 靜默漏)**:event 觸發維持 in-request 一次性(不上 queue、
  不常駐 sweeper),但每個 event-trigger 對它關心的 entity 集合記一個「**處理水位**」(最後成功起
  run 的 entity 版本/時間)。漏掉的事件(pod 在 commit↔handler 間死)→ 水位落後 → 任何時候一句
  查詢即可撈出「版本高於水位卻無對應 run 的 entity」按需補。**「可補但不主動補」**,介於
  best-effort 與 full reconciliation 之間。
- **captured_user**:E-decl(固定宣告 user);event 的 actor 只當**資料**讀。除權 → capability
  authz **fail-loud**,且 loud 要接上水位/可查機制(讓「某 trigger 因擁有者除權天天紅」可被發現,
  不是無人看的 log 靜默失敗)。

---

## P10 — agent 工具寫入接上 event dispatch(補齊單一寫入路徑)

**問題(P9 收尾時標記的接縫)**:P9 把事件 emit 掛在 `EntityStore.create/update` 這條**單一寫入
路徑**上,但 emit 是否真的發,取決於該 `EntityStore` 實例建構時有沒有注入 `on_write` sink。v1 只有
**人/UI(`entity_routes`)**與 **workflow handle(`orchestrator`)**兩條路徑注入了 sink;**agent 工具
(`create_entity`/`update_entity`/`link_entity`)建構的 `EntityStore` 沒帶 sink**。後果:agent 改 entity
→ 靜默、不 emit、不觸發 `on_event` workflow。這在最常見的來源(AI 動 entity)上違反「所有寫入無差別」的
初衷——使用者手動改會觸發、workflow 改會觸發,AI 幫忙改卻不會。

**定案**:把 agent 工具接上同一顆 dispatcher,並**連同 origin 一起傳**——這是關鍵,不能只接 sink。

- `AgentToolContext` 加兩欄:`entity_write_sink`(dispatch sink;`None`=不 emit,KB/wiki/測試零成本)與
  `entity_write_origin`(`EntityOrigin | None`)。工具 `_entity_store` 傳 `on_write=sink`,三個寫入工具
  (create/update/link)都傳 `origin=entity_write_origin` 與 `actor`。
- **origin 傳遞是護欄關鍵**:P9 的兩道遞迴 guard(自觸發跳過 + 深度上限)全靠 `event.origin`。若把 agent
  路徑天真地以 `origin=None` 接上,則 event-triggered workflow 內的 agent 寫入會以 **depth 0** 重新發事件,
  丟失所在 run 的 `(trigger, depth)` → guard 1 認不出、guard 2 從 0 重數 → **agent 一改 entity 就可能自我
  重觸發 / 繞環**。正解:
  - **純使用者 chat**(`build_chat_turn`)→ `origin=None`(depth 0,第一層寫入,本來就該觸發)。
  - **workflow agent-node**(`build_workflow_turn`)→ 帶所在 run 的 `EntityOrigin(trigger, depth)`,與
    handle 自身寫入用的 `WorkflowHandle.entity_origin` **同一顆**(升為公開屬性=單一真源)。由
    `WorkflowExecutor.wire_handle` 從 handle 讀出、經 `drive_turn` 傳進 `build_workflow_turn`。
- **sink 後設注入**:`EventTriggerDispatcher` 在 `create_app` 中比 `TurnContextBuilder` 晚建,故沿用既有
  pattern(`workflow_orchestrator.entity_write_sink = …`),在 dispatcher 建好後
  `turn_ctx.entity_write_sink = event_dispatcher.dispatch`。
- **DoD 回歸測試**:event-triggered workflow 內 agent 改 entity **不得繞過 depth cap**——把 agent 工具 sink
  接真 `EventTriggerDispatcher`,驗證 depth=cap 的 agent 寫入 fire 不出東西、且不自觸發;對照 `origin=None`
  的第一層寫入會 fire(證明線是通的、是 origin 擋住而非斷線)。

**仍延後(P10 之外)**:D2d on-demand backfill 的 operator route/CLI(watermark ledger 已具「可查落後」的
基礎)維持獨立 follow-up。

---

## 明確延後的 follow-up

- 使用者 item-local workflow 自排程(+ per-user quota)。
- full cron 運算式(對 C2 的加法擴充)。
- 間接環的完整 origin 鏈追蹤(P9 深度上限之外)。
- full reconciliation sweep / durable event queue(P9 水位之外的 exactly-once)。
- 主動偵測 acting_user 除權 → 標記 trigger(而非等下次觸發才 fail)。
- workflow-as-durable-JobType on worker pod(A 的 in-process v1 之外,讓 run 上 HPA、pod 重啟
  自動接手)。
- DST tz 語意(不存在/重複本地時刻)。
- ~~**P9 agent-tool 寫入路徑接上 event dispatch**~~ → **已於 P10 收掉**(見下)。
- **P9 D2d on-demand backfill 的 route/CLI**:watermark ledger + `processed_version` 已具備「可查
  落後」的基礎;把「version > watermark 卻無對應 run」做成一支 operator 查詢/補跑指令留 follow-up。

---

## 一貫原則(貫穿全 phase 的設計線)

1. **不要靜默出錯**:cache 過期、lost-update、漏 job、fallback superuser——凡「不報錯的錯」都
   拒絕。做不到即時正確就做到**可被發現/可查**(P1 `reads`、P2/card 樂觀鎖、D2d 水位、F-2
   abandon 可查、E 除權 loud 給會被看到的地方)。
2. **宣告即正確、靜態可驗**:把維護正確性的責任從作者移到引擎(`reads`、C2 schema validate、
   acting_user 必填)。
3. **裁決落盤、重跑沿用**:gate decision、abandon 狀態、journal——判定一次寫死,不在邊界抖動。
4. **一致性本身是價值**:entity 與 card 同走樂觀鎖,不讓「保不保證不 lost-update」看 resource
   而定。
