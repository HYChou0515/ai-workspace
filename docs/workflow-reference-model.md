# Workflow DSL 統一引用模型與待實作語法規格（#428）

> 這份是 **#428** 六項 DSL 語法擴充的**釘死規格**——`/grill-me` 一整場的鎖定結論，供實作者
> 一份對得上。它是 [`workflows.md`](workflows.md) §22（降階 DSL）的延伸，只談**宣告層的語法**；
> 既有引擎的行為缺陷/缺口（cache 失效語意、時間·事件觸發、entity 寫入 capability…）是姊妹
> issue **#429**，不在此。撰寫端 how-to 見 [`workflows-authoring.md`](workflows-authoring.md)。
>
> 現況程式碼觸點：`src/workspace_app/workflow/dsl.py`（schema + interpreter + `validate_def`）、
> `engine.py`（`run_step` = filesystem-journal）、`steps.py`（node adapters）、`gate.py`
> （`human_gate` / `Decision`）、`handle.py`（`wf.map` / `glob` / capability）。

---

## 0. 總線（護欄——先立，避免實作時越線）

- 插值 `{...}` 只做**定址**，永遠不加 eval / 運算 / 自訂函式。
- `switch` 的 `cases` 必須**有限、預先列舉**；不提供無界迴圈。
- `map` 維持**單層**：元素內不可含 `map` 或 `gate`（見 §3 的情境規則）。
- **判準：整張流程圖必須事先靜態畫得出來。** 畫不出來的需求不進 DSL，留給 dev 的 `run.py`。
- 唯一的「環」是 `gate revise`（§6）——一條**人閘住的有界回邊**，且在圖上**顯式**（一條
  `{steps.<gate>.feedback}` 引用邊）。

---

## 1. 統一引用模型 `{steps.<name>.<field>}`（地基，🔴 破壞性）

### 1.1 物理模型——具名產出＝既有 journal 條目，不另立新檔

**`{steps.x.field}` 讀的就是 x 這步既有的 journal `result`，取它的 `field`。** 不為「具名產出」
另立平行儲存（`/steps/<name>.json`、`<run>/…` 都**不要**）——那會跟既有 journal 打架（兩份真相）。
具名產出是在既有 journal 上長一個「用名字定址」的**讀取層**，不是新的寫入位置。

- journal 位置不變：`/.workflow/<workflow_id>/step_<name>/<key>.json`，內容 `{hash, result}`，
  **跨 run 共用**（這是 skip 的靈魂，不加 `<run>` 層）。
- `{steps.x.field}` 解析成「讀 `step_x/<當前作用域 key>.json` 的 `result.fields.field`」。
  - **頂層具名 step**：一次 run 只有一個結果，`<key>` 固定（由 args-hash 決定）→ 引用明確、
    跨 run skip 不變。
  - **map 內的 step**：每元素一個 `<key>`。`{steps.x.field}` 在 map **內**指「當前元素的 key」；
    map **外不可**引用單一元素（只能 `.outputs`，見 §5）。

### 1.2 `result.fields`——`run_step` 的實體改動

現況 `agent_write_step` 的 `result` 是 `{out, bytes}`，**不含結構化欄位**，所以
`{steps.x.type}` 現在無值可讀。這是 #1 的實體改動：

- 宣告了 `outputs`（§2）的 **agent** step：`agent_write_step` 把模型輸出的 JSON **parse**、
  對 `outputs` schema **驗證**後，存進 `result.fields`（例：`result = {out, bytes, fields:{type,score}}`）。
  `{steps.x.type}` 讀 `result.fields.type`。
- 宣告了 `outputs` 的 **sandbox** step：它的腳本**自己寫一個 json 檔**，引擎**讀入**當
  `result.fields`、對 schema 驗證。
- `out`（內容檔，如 `report.md`）與 `fields`（具名欄位）是**兩種產出**。`{steps.x.field}`
  **只碰 fields**，不碰 `out`。一個 step 可兩者兼有。

### 1.3 `{p.field}`（map 變數的欄位存取）——保留，不移除

被 #1「取代」的是 **fan-in 慣例**（寫資料夾＋re-glob＋`{p.field}` 讀檔 → 具名 `.outputs`，
見 §5），**不是** map 變數本身。`{p.field}` 續留，並自然涵蓋兩種元素：

- `over` 是 glob → `p` 是路徑字串 → `_index` 讀該 `.json` 檔取欄位（現況機制）。
- `over` 是清單值（§4）→ `p` 是值（例：一個 obj）→ 直接 index 取欄位，不讀檔。

`_index` 已同時處理兩者（str 結尾 `.json` → 讀檔；dict → index），**無機制移除**。

### 1.4 dsl.py 觸點

- 各 Step Struct 補 `name`（可選；被引用者必填；見 §1.5 唯一性）。
- `_lookup` / `_resolve` 的 ns 增加 `steps` 命名空間；`{steps.x.f}` → 由 `x` 名字算出 journal
  路徑 `step_x/<scope-key>.json`，讀 `result.fields.f`，複用 `_index`。
- `run_step` / `agent_write_step` / `sandbox_node`：宣告 `outputs` 時把 fields 存進
  `result.fields`（agent parse+驗證、sandbox 讀檔+驗證）。

### 1.5 驗收（靜態）

- [ ] `name` 唯一性由 `validate_def` 檢查（作用域見 §5.3：頂層一組命名空間；map 內是各自子域）。
- [ ] `{steps.a.f}` 能在後續 step 正確解析到 a 的 `result.fields.f`。
- [ ] 未宣告的 `name`、不存在的 step、或**向前引用**（引用一個定義在其後的 step）→ 靜態報錯。
      唯一例外：`{steps.<gate>.feedback}` 出現在該 gate 的 `revise_to` target（§6，圖裡唯一的環）。

---

## 2. `outputs` schema（型別地基＋enum 選配）+ 引用靜態檢查

### 2.1 宣告

step 可宣告 `outputs`：欄位名 → 型別 ∈ `str|int|float|bool|list|obj`（地基）；欄位可選配值域
`{"type":"str","enum":[...]}`。`list`/`obj` 為**淺型別**（只驗「是清單／是物件」，v1 不做巢狀
schema；`enum` 只用於純量）。

```json
{ "type":"agent", "name":"classify", "phase":"classify",
  "prompt":"…reply JSON {type, score}",
  "outputs": { "type": {"type":"str","enum":["latency","errors","other"]},
               "score": "float" } }
```

### 2.2 outputs 不符 ＝ check 失敗 → retry

`outputs` 不只是靜態 schema，它是**執行期的隱式 gate**：

- agent step：模型 JSON 缺欄位 / 型別不符 / enum 越界 → 視為 check 失敗 → **回灌原因、retry**
  （複用 `retries` 與 `_retry_prompt`）。耗盡 retries 才 `StepFailed`。
- sandbox step：腳本寫的 json 不符 → `StepFailed`。

### 2.3 靜態檢查（`validate_def`）

- 下游 `{steps.x.f}` 的 `f` **不存在於 x 的 `outputs`** → 靜態錯。
- 型別相容（例：`over: {steps.x.items}` 要求 `items` 為 `list`）→ 不符靜態錯。
- `enum` 欄位被 `switch` 消費時，供 §3 做窮盡檢查。
- 未宣告 `outputs` 的 step 仍可運作，只是**不能被 `{steps.x.f}` 取欄位**（可被 `.outputs`
  以 artifact 清單形式收集，見 §5.1 零步退化）。

**相依**：#1。

---

## 3. `switch` 條件節點（唯一缺的分支原語）

### 3.1 語法

```json
{ "type":"switch", "on":"{steps.classify.type}", "phase":"route",
  "cases": { "latency":[…], "errors":[…], "other":[…] },
  "default": [] }
```

`on` = 一個插值值；`cases` = 值 → step 序列；`default` = 序列。只走一條、其餘不走。

### 3.2 巢狀規則——從「位置」升級為「兩種情境」

真正的約束不是「gate 必須在頂層」，而是「**不在平行/迴圈情境裡**」（gate 在 map ＝ N 個平行
暫停、「resume 哪個元素」無定義）。故：

> **情境 A（循序頂層）**——頂層 steps、以及 **top-level switch 的 case 內**：可含任意 step，
> 含 `gate` / `map` / `switch`。因為循序，gate（單一暫停）與 map（等同頂層 map）都安全。
>
> **情境 B（map 元素內）**——`map.do`，**以及巢狀在 map.do 裡的 switch 的 case**：
> **禁 `map`、禁 `gate`**（Q7 原樣保留），允許 `agent` / `sandbox` / `capability` / `switch`。
> **禁令穿過巢狀 switch 遞移**（switch 不得當繞過 Q7 的後門）。

- 巢狀 switch（case 內再放 switch）**允許**、不設人工深度上限；但 `validate_def` 加一個**防呆
  健全上限（如 32 層）** 擋 pathological 輸入。

### 3.3 `on` 的穩定性硬約束（switch 不 journal 的隱藏前提）

switch 是**純控制流、不 journal**（等同 `run.py` 的 `if`）：replay 時重新解析 `on` → 選同一條
case，case 內各步靠自己的 journal skip。**這只在 `on` 跨 replay 穩定時成立**，故：

> **`on` 只接受穩定引用**——`{config.*}` / `{inputs.*}` / 具名 step 的 `fields`（皆已 journal /
> 天生穩定）。`validate_def` **擋掉對非穩定來源的 `on`**。

### 3.4 未匹配值的語意（default 在/不在的分野）

- `default: []` **有寫**（即使空）＝作者明說「其餘值不做事」→ 合法 no-op。
- `default` **整個缺席** ＝作者宣稱「已窮盡」→ 未匹配值 → **執行期報錯（loud）**。
  - 在 **map 元素內**時，這個報錯是**元素級 `StepFailed` → 進 `failures[]`、其餘元素照跑**，
    **非 run 級**中止。
- enum-typed `on` 再加**靜態層**：cases 出現 enum 外的值 → 靜態錯；漏 enum 值且無 default → warning。

### 3.5 dsl.py 觸點

- 新增 `SwitchStep`（tag `switch`；`on:str`、`cases:dict[str,list[Step]]`、`default:list[Step]`）。
- `_exec_step`：解析 `on` → 選 `cases[值]`（或 `default`）→ 依序執行；switch 本身不寫 journal。
- `validate_def`：情境 A/B 規則（含遞移）、`on` 穩定性、default 分野、enum 窮盡、深度上限。

**相依**：#1、#2。

---

## 4. `map over` 清單值 / range

### 4.1 語法

`over` 除 glob 字串外，也接受：

- **清單值**：`"over":"{steps.extract.items}"`（`items` 型別須 `list`）。
- **range**：`"over":{"range":"{inputs.n}"}`，展開 N 次，`as` 綁到索引。

### 4.2 元素 key 衍生規則（三型態一起定義）

`<key>` 決定 journal 身份 / skip / collect 對齊：

| `over` 型態 | key | 順序 |
|---|---|---|
| glob | 相對路徑 | **sorted**（filesystem 列舉序不保證，需補救） |
| 清單值 | 預設**陣列位置索引**；可選 `key_by:"<field>"` 明指身份欄位（元素須 obj） | **陣列序，不再排序**（作者傳清單即定序） |
| range | 索引 `0..n-1` | `0..n` |

- **不自動偵測 id 欄位、不用 content-hash**（content-hash 會讓兩內容相同元素撞 key → 第二個被
  當第一個 cache 而 skip，是**靜默正確性 bug**）。要免「重排即重跑」就用 `key_by` 給穩定欄位。
- **`key_by` 撞值不吞**：兩元素 `key_by` 欄位同值 → **執行期報錯／warning「key_by 不唯一」**，
  絕不當 cache 命中而 skip 掉第二個。
- 「三型態一致」＝**各自 deterministic**，不是「套同一條排序」。（寫死，防日後有人「為一致」
  把清單也排序。）

### 4.3 dsl.py 觸點

- `MapStep.over` 型別放寬：`str`（glob）| `str`（清單插值）| `{"range": …}`。
- `MapStep` 增 `key_by: str = ""`。
- `_exec_step` 的 map 分支：先判 `over` 型態；非 glob 時把每項物化成元素（維持身份），再進既有
  per-element 迴圈（skip+collect 不變）。

**相依**：清單形態依賴 #1。

---

## 5. fan-in 具名 `{steps.<map>.outputs}`

### 5.1 `.outputs` 來源規則

`map` 命名後，其整批輸出用 `{steps.<map>.outputs}` 引用——一個**純 `fields` 的清單**，
順序＝元素序（§4.2）。來源：

- `do` 中**唯一宣告 `outputs` 的那步** → 就收它（預設，免寫 `collect`）。
- **多步宣告 `outputs`** → **必須** map 明寫 `collect:"<inner name>"`，否則 `validate_def`
  靜態錯（**不猜「最後一步」**——那常是沒 fields 的副作用步、錯得安靜）。
- **零步宣告 `outputs`** → `.outputs` 退化成「每元素的 `out` artifact **路徑清單**」
  （文件明講，**不可**變成意外的空陣列）。

**只收 `fields`**（不含 `out`/`bytes`）：讓 `.outputs` 型別 ＝ `list[<被收集步的 outputs 型別>]`，
使 #2 的靜態型別能**穿過 fan-in**。

### 5.2 元素 key、下游對齊、switch 缺席

- **key 不進 fields**：`fields` 只含作者宣告欄位；引擎**另存 key↔元素**對應，只在**錯誤回報**與
  **下游回指某元素**時現形（資料流裡隱形、診斷裡現形）。
- **下游沿用上游 key**：`map over "{steps.map.outputs}"` 時，元素 key ＝**上游來源 key，非下游
  重編位置**——否則 switch 造成的元素缺席讓 `.outputs` 長度變動、位置漂移對不齊。
- **collect 步落在未跑到的 switch case**（該元素走了別條）→ 該元素在 `.outputs` 中**缺席（null）
  並歸入 skip 計數**，**不報錯**（資料驅動的正常結果）。

### 5.3 作用域驗證

- 從 map **外**引用元素**內部** step（非 `.outputs`）→ 靜態錯（作用域規則）。
- `name` 唯一性作用域：頂層一組命名空間；每個 map 的 `do` 是各自子域（同名不同 map 不衝突，
  但都不可被外部單獨引用）。

**相依**：#1、#4。

---

## 6. gate `revise`（吃 user 回饋）

### 6.1 語法

```json
{ "type":"agent", "name":"draft", "phase":"draft",
  "prompt":"擬週報。若有修改意見：{steps.review.feedback}", "out":"report.md",
  "outputs": { … } },
{ "type":"gate", "name":"review", "phase":"review", "title":"審週報",
  "summary_from":"report.md",
  "allow":["approve","revise","reject"], "revise_to":"draft" }
```

### 6.2 機制——interpreter-owned `_Revise` re-drive（不新增 replay）

1. gate 是具名步；它記錄的 `Decision.input` 以 **`{steps.<gate>.feedback}`** 曝光
   （無 revise 時預設 `""`）。
2. 人在 gate 按 `revise` ＋一句話 → `record_decision` 寫 `decision.json {choice:revise,input}`。
3. 重跑 **pass①**：走到 gate 讀到 revise → **持存 feedback**、**刪掉這個 gate 的
   `decision.json`**、raise `_Revise`。
4. `build_run` 用 `while True` 包 step loop：`_Revise` → `continue` 重跑 **pass②**。pass② 在
   seed 時把持存的 feedback 載進 ns → `revise_to` 步的 prompt 含 `{steps.<gate>.feedback}` →
   **args-hash 變 → §9 自動重跑該步**，下游靠 hash-chaining 連鎖重跑；走到 gate 時 `decision.json`
   已刪 → `AwaitingHuman` → run 再次暫停成 `awaiting_human` 供**重審新草稿**。
5. **有界保證**：每次 revise 都需人按一次；`_Revise` 一定收在下一次 gate 的 `AwaitingHuman`
   （re-pause），機器不會自迴圈。這是圖上「唯一的環」被人閘住的合法有界環。

**invalidation 是資料驅動、非位置式**：feedback 折進 `revise_to` 的 input-hash，§9「改上游→
自動重跑下游」已做完；**唯一要顯式刪的是 gate 自己的 `decision.json`**（人給的決定不在
hash-chain 裡，不清就被自己的 journal skip 掉 → 無限 revise）。

**副作用連鎖（文件須講明）**：revise_to→gate 之間**依賴 revise_to 產出的副作用步（capability）
會連鎖重跑**——靠 capability 冪等兜底。不是只有 draft 那步重跑。

### 6.3 feedback ＝ token `{steps.<gate>.feedback}`（不是魔法變數、不是自動 append）

選 token 的**根本理由**：它把 revise 這條 back-edge 變成**圖上顯式的引用邊**——整套設計的總線是
「圖可靜態畫」，revise 是唯一的環；魔法變數／自動 append 會讓那條環**隱形**。token 讓它現形。

- `validate_def` **特例放行**這條**唯一合法的向前引用**（target 跑在 gate 之前卻引用 gate 的
  feedback）——它恰好對應圖裡唯一的環。
- **強制** target prompt 引用 `{steps.<gate>.feedback}`，否則 revise 靜默 no-op（footgun）→ 靜態錯。
- **撰寫慣例**：target prompt 要能在 feedback ＝ `""` 時獨立成立（示範用「若有修改意見：{…}」
  這種可選補述語氣）。這是慣例、非硬約束。

### 6.4 `revise_to` 的五條靜態約束

1. 必須是**存在的頂層步**，且**位置在 gate 之前**（不能向前 bounce）。
2. **不能指 map／switch case 內的步**（元素歧義；對應 §5.3 作用域）。
3. `revise ∈ allow` **⇔** `revise_to` 存在（缺一即靜態錯）。
4. target prompt 必須引用 `{steps.<gate>.feedback}`（§6.3）。
5. **revise_to 與其 gate 之間不得有其他 gate**（單一 revise 區間只一個暫停點，保持
   「revise → 重做 → 回這個 gate 重審」的乾淨語意）。

### 6.5 與 steer（#288）的分工（並存）

| 面向 | **revise（#6）** | **steer（#288）** |
|---|---|---|
| 誰決定改什麼 | **作者**靜態宣告（`revise_to`＋feedback token） | **LLM steerer** 動態提 plan |
| 觸發 | gate 上的按鈕 | 活躍窗外的自由文字，隨時 |
| 影響範圍 | **靜態已知**（revise_to→gate） | 計算後用 confirm card 秀 blast radius |
| 要 LLM turn | 否 | 是（steerer 唯讀 turn） |
| 要 confirm card | 否（作者設計時已預先核准此 loop） | 是 |
| 改動幅度 | 一個宣告步帶 feedback 重跑 | 任意 workspace 檔＋任意 step invalidate |
| 何時用 | 預期內的修訂 | 預期外的重導 |

一句話：**revise 是作者織進 workflow 的有界 in-loop；steer 是永遠疊在其上、給預期外情況的自由
文字 overlay。** 兩者共用 §9 底層原語（invalidate＋re-drive），差在「誰決定 invalidate 什麼」。
**並存語意**：revise ＝ gate UI 按鈕（情境內）、steer ＝ 活躍窗外 overlay（情境外），物理上不會
同時對同一暫停點生效（寫進 [`workflows.md`](workflows.md) §10「並存」那節）。

### 6.6 dsl.py 觸點

- `GateStep` 增 `revise`（於 `allow`）、新增 `revise_to:str`。
- `_exec_step` 的 gate 分支：`revise` → 持存 feedback + 刪 `decision.json` + raise `_Revise`。
- `build_run`：`while True` 包 step loop、seed 時載入各 gate 的持存 feedback。
- `validate_def`：§6.4 五約束 + §6.3 forward-ref 特例。

**相依**：#1（feedback 走統一引用）。

---

## 7. §7 零星項的範圍裁定

- **順序一致性（§7-item2）＝已在 #4/#5 解掉、非獨立工項。** glob=sorted、清單=陣列序、
  range=0..n、`.outputs` 收集＝元素序、下游沿用上游 key——是 `over`／fan-in 規格的不變式
  （§4.2 / §5.2），寫進本文即可。
- **per-element sub-handle 真平行（§7-item1）＝移出 #428、路由到 #429。** 它是**引擎能力**
  （給 dev `run.py` 生可獨立平行跑 agent turn 的 sub-handle），**非 `workflow.json` 語法**，正落在
  #428（語法）vs #429（引擎缺口）分界的 #429 側。DSL `map` 定位是「批次容錯 skip+collect，
  **不負責效能**」；agent turn 被 ChatTurnEngine 序列化是**已文件化的效能限制、非正確性 bug**。
  sub-handle 日後在 #429 落地時，DSL `map` 可**透明改用**（interpreter 內部換掉 `wf.map` 實作）
  **而毋須任何語法變更**——證明它與 #428 正交。

---

## 8. 破壞性與遷移

- **#1 是唯一的破壞性變更**（`name` 唯一性、`{steps.x.field}` 取代路徑式具名引用）；#2~#6 皆
  **向後相容的新增**。
- 現存唯一的 DSL 檔：`src/workspace_app/apps/playground/profiles/dsl/workflows/file-uploads/workflow.json`
  （＋其測試 `tests/workflow/test_dsl.py` 等）。**P1 一次改寫**：
  - 第一個 map（classify）：給 `name`＋`outputs:{collection,digest,source}`；agent 產出 fields。
  - gate 的 `summary_from`：改為吃 `{steps.classify.outputs}` 引用（或維持一個供人審的 `out` 檔；
    細節在 P1 落地時定）。
  - 第二個 map：`over "plan/*.json"` → `over "{steps.classify.outputs}"`；`p` 從路徑變值，
    `{p.collection}` / `{p.source}` 照舊（§1.3）。
- **`schema_version` 維持 1、原地擴充**（無外部 DSL 使用者，改寫唯一範例即可，避免 migration
  邏輯）。「NEVER bump version」指 release 版號；DSL schema 是內部契約，此處也無必要動。

---

## 9. Build plan（flat integer phases）

依 issue 相依（1→2→3、1→4→5、6 獨立）攤成尊重相依的線性序：

| Phase | 內容 | 相依 |
|---|---|---|
| **P1** | #1 統一引用：`name` 唯一性、`{steps.x.field}`＝讀 `result.fields`、agent/sandbox result 長 `fields`、reference 存在性/作用域/向前引用靜態檢查、`{p.field}` 保留；**改寫 file-uploads.json ＋測試** | — |
| **P2** | #2 outputs schema：型別集＋enum、引用欄位存在/型別靜態檢查、**agent 輸出對 outputs 不符＝check 失敗→retry** | P1 |
| **P3** | #3 switch：`SwitchStep`、A/B 情境（含遞移）、`on` 穩定性約束、default 分野、防呆深度上限、不 journal、exec＋validate | P1, P2 |
| **P4** | #4 over 清單/range：`over` 型別放寬、key 衍生＋`key_by`＋撞值報錯、順序語意、物化元素 | P1 |
| **P5** | #5 fan-in `.outputs`：來源規則（唯一步／多步 `collect:`／零步退化）、只收 fields、下游 key 沿用、switch 缺席、作用域驗證 | P1, P4 |
| **P6** | #6 gate revise：`revise∈allow`、`revise_to`、`_Revise` re-drive、feedback token、五約束、forward-ref 特例 | P1 |

每個 phase 走 `/tdd`（red-green-refactor），每完成一 phase commit 一次。

---

## 10. dsl.py 觸點總表（一頁速查）

| 檔 | 改動 |
|---|---|
| `dsl.py` schema | 各 Step 補 `name`；`AgentStep`/`SandboxStep` 補 `outputs`；`MapStep` 補 `key_by`、放寬 `over`；新增 `SwitchStep`；`GateStep` 補 `revise_to` | 
| `dsl.py` interp | `_lookup`/`_resolve` 加 `steps` ns；map 物化非-glob `over`；`_exec_step` 加 switch 分支、gate revise 分支；`build_run` 加 `while True`＋feedback seed | 
| `dsl.py` validate | `name` 唯一性/作用域、reference 存在性/型別/向前引用、A/B 情境（遞移）、`on` 穩定性、default/enum、`.outputs` 來源、revise_to 五約束、深度上限 | 
| `engine.py` | `run_step` 存 `result.fields`（agent parse+驗證 / sandbox 讀檔+驗證）；`_Revise` 例外 | 
| `gate.py` | `Decision.input` 曝光為 feedback；revise 分支持存 feedback＋刪 decision | 
| `handle.py` | map 物化元素身份；引擎側 key↔元素對應（診斷/下游回指） | 
