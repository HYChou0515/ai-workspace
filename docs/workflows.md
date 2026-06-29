# Workflows — 操作手冊

> **狀態：** issue #100 的規範性設計 spec。本文件就是 *目標*，也是 *驗收標準*：
> 當實作的可觀察行為符合這裡的規則時，它就算「完成」。刻意在計畫之前先寫
>（「以終為始」）。決策是透過一次 `/grill-me` session 鎖定的；被否決的替代方案
> 就地記錄下來，免得我們重新爭論。
>
> **要 author 一個 workflow？** 這份是 *spec*；實務上的 how-to（block 目錄、
> 慣例、`new`/`check` CLI）在 [`workflows-authoring.md`](workflows-authoring.md)（#287）。

一個 **workflow** 把這個 agentic workspace 從「只能互動」變成外部系統可以
**透過 API 觸發**、**headless（無人值守）** 跑出一個有用 **artifact**（產物）的東西
——而且重用 *既有的* workspace 機制（sandbox、file tool、agent loop、KB），不是重新發明。

兩個驅動的使用情境：

1. 一個外部呼叫端週期性地打 API，啟動一連串以 artifact 收尾的動作（例如一份 report）。
2. 有人上傳檔案；系統把每一個分類、消化（digest），再把結果歸檔到一組小而
   **預先定義好** 的 KB collection。

本手冊描述的是 **平台**（可重用的機制）。任何單一 workflow *如何* 表現
——「digest」是什麼意思、一個檔案怎麼切、routing 規則為何——都是
**App/profile 的實作**，寫在那個 profile 的程式碼裡，不在本文範圍內。

---

## 1. 心智模型

把它想成 **Temporal**，但 **journal 就是檔案系統**：

- **Orchestration（編排）** = workflow 的 `run()` 函式。它跑 **在後端**，
  掌管控制流（sequence／loop／gate），而且 **每次（重）執行都從頭重跑一遍**。
  它之所以 durable（耐久），是因為它的進度被記錄成 **檔案**。
- **Nodes（節點）** = `run()` 呼叫的工作單位（agent step、deterministic step、
  human gate）。一個 node 就是一個 **activity**：它把結果（一個 **artifact** 或一張
  **receipt（收據）**）寫進 workspace 的 `step_<name>/<key>`，連同一個
  **input-hash**。在後續一次執行中，若某 node 的 artifact 存在 **且** 它的
  input-hash 仍然吻合，就 **跳過** ——重用它的 artifact，不重做這份工。
  這是 Make 風格的 incremental 執行（§9）。它就是我們 resume／retry／rewind／
  crash-recovery 的全部故事。
- **一次 workflow run 與一個互動式 workspace 是同一個 item 的兩種模式**，
  共用同一個 `ChatTurnEngine` 和同一個 conversation。一個 agent node *就是* 那個
  item 上的一次 turn ——它的思考、訊息、tool 呼叫都串流進該 item 的 chat，
  完全像是有人在驅動。所以一次 run 留下完整的 transcript + 檔案，人類可以
  幾乎零額外成本地接手（§10）。

為什麼 orchestration 要在後端：只有後端 driver 能重跑、能持有 sandbox 生命週期、
（之後）能在 human gate 暫停。sandbox 是短命的計算資源；**FileStore 才是耐久的紀錄**。

---

## 2. 一個 workflow 住在哪裡

在 **profile** 這一層——**App 這一層沒有任何 orchestration 程式碼。**

```
apps/<slug>/
  app.json, model.py, prompts/        # App：WorkItem 型別 + branding + agent 上限
  profiles/
    <profile>/
      _profile.json                   # profile config + workflow MANIFEST（phases、input.json 路徑）
      _prompt.md, *.tpl, .skill/      # 既有的 profile 資產（prompt、seed 檔、skill）
      run.py                          # orchestration run(wf, inputs)         [後端，trusted]
      nodes/                          # 自訂 deterministic node 腳本           [在 sandbox 中跑]
```

- 一個 **profile = 一個完整的行為包**：prompt + tool 子集 + seed 檔 + skill +
  （可選）一個 workflow。
- 一個 profile 有 **0 或 1 個 workflow**。*有* 一個 → 可 headless 觸發。*沒有* →
  只能互動。（這就是為什麼 *建立一個 item* 與 *跑一個 workflow* 是解耦的——§14。）
- `run()` 是 **trusted 後端 Python**，用掃描 profile 的方式被發現，跟 App 被發現的
  方式一樣（放進去 → 註冊）。自訂的 deterministic node 腳本跑 **在 sandbox 中**（§7）。
- profile 用 **既有的 profile 檔案 seeding 機制**（就是那個 seed `notes.md`、
  `SOP.md`…的同一套），把它想要的任何預設檔案 **seed** 進 workspace。
  `input.json`（§14）只是其中一個被 seed 的檔案。

---

## 3. Authoring 模型

> **一個 workflow 是一個 Python `async def run(wf, inputs)`，搭配一個小小的 step
> 函式庫，再加一份小小的資料 `MANIFEST`。控制流就是 host language。沒有 workflow DSL。**

當控制流（loop／branch／retry／傳值）被硬塞進宣告式 YAML 時，workflow 就會剛好變得
「難以定義」。用 Python：

| 你需要什麼 | 你怎麼寫 |
| --- | --- |
| 對 items 迭代 | 一個普通的 `for` loop |
| 並行跑 items | 對每個元素的工作做 `asyncio.gather`（§11） |
| 帶 feedback 重試一個 step | 該 step 的 `retries=`（底層是一個 `while`） |
| 在 steps 之間傳資料 | 純變數 + workspace 檔案 |
| branch | 一個普通的 `if` |
| 「每個 agent step 都被 gate」 | `check=` 是 `agent_step` 的 **必要** 參數 |

**否決：** 一個宣告式 DAG/DSL。它只在「給非工程師做視覺化 authoring」或
「執行期可編輯定義」這兩種需求下才划算——兩者都不是必要的——而且它把
「難以定義」的痛苦帶回來。Observability（§12）**不** 需要 DSL。

### 讓其他一切都變便宜的兩個 authoring 慣例

1. **把一個 step 的 inputs 當成它的 arguments 傳進去。** 在 `run()` 裡讀 artifact，
   把資料餵進 step；**不要在 step 內部讀環境狀態（ambient state）。** 這讓一個 step 的
   **input-hash = `hash(它的 arguments)`**（§9）——計算起來再簡單不過，而且上游
   artifact 一變，下游就會自動失效。
2. **控制流必須產生一個可重現的 *step 身分集合*。** Loop 的迭代集合與 branch 的條件
   只能讀自 `inputs` 與 step artifact ——絕不能讀 wall-clock／`random`／一個全新的、
   沒走過 step 的 query。一個 step 的 *輸出* 可以完全 nondeterministic；只有它的
   *身分*（它的 artifact 落在哪裡）必須穩定，這樣重跑時 artifact 才會落在同樣的路徑上（§9）。

### MANIFEST（唯一的宣告式部分）

住在 `_profile.json`：

```jsonc
{
  // ... 既有的 profile 欄位 ...
  "workflow": {
    "title": "Classify & file uploads into collections",
    "phases": [                                   // diagram 的靜態骨架（§12）
      { "id": "classify", "title": "Classify + digest" },
      { "id": "ingest",   "title": "Ingest to collection" }
    ]
    // 省略 input_json ⇒ 推導出 `{profile.upload_dir}/input.json`（§14）；只有要覆寫時才釘死
  }
}
```

### Authoring 介面（示意；確切 signature 在計畫中釘死）

```python
async def run(wf, inputs):
    # wf     — run handle：workspace IO、capability 方法、run-scoped credential
    # inputs — 解析後的 input.json（內容是 profile 自己的事）
    ...
    return artifact_summary                        # 存在 WorkflowRun 上（§13）
```

- `wf.read(path)`、`wf.read_json(path)`、`wf.glob(spec)`、`wf.files` —— workspace IO。
- `await agent_step(wf, *, prompt, phase, tools=None, check, retries=0, cache=True)` —— §5.1。
- `await sandbox_node(wf, *, phase, run, check=None, cache=True)` —— 自訂 deterministic node，§5.2。
- `check.*` —— gate builder，§6。
- capability 方法，例如 `await wf.ingest_to_collection(collection, path, *, digest=None)` —— §8。
- `fail(reason)` / `StepFailed` —— 中止當前的 step/element，§6。
- `human_gate(...)` —— §10。

---

## 4. Node 類型

三種，**靠 *誰呼叫它們* 來區分，不是靠它們在哪裡跑。**

1. **agent node** —— 一次 LLM 驅動的 turn（§5.1）。LLM *決定*；它靠自己的 tool
   對 sandbox 做事。
2. **deterministic node** —— author 寫的、**沒有 LLM** 的程式碼（§5.2）。是 orchestration
   *動手*。以腳本形式跑在 sandbox；透過 HTTP 觸及平台 capability。
3. **human gate** —— 為了一個人類決策而暫停（§10）。

**decision/action 原則（可靠性的核心）：** LLM 永遠只 *決定*，並把它的決策記錄
*成資料*；那個 *action*（任何必須可靠的 side-effect ——ingest、export…）是由一個
**deterministic node** 執行，不是 agent。agent 永遠不持有那個會產生 side-effect 的
tool（§7）。

---

## 5. Node 細節

### 5.1 Agent node

- 透過 **既有的 `ChatTurnEngine`** 跑（後端 loop；它的 `exec`/file tool 作用在
  sandbox 上）。它就是該 item 上一次正常的 turn → 以 `Message` 持久化、透過 SSE 串流。
  這就是 transcript 連續性與免費人類接手的來源（§10）。
- **`tools=` ⊆ profile 的 tool 上限**（LLM 安全邊界；coherence 像 #89 的
  `validate_function_coherence` 那樣強制）。agent 的 tool 偏向 **讀／探索**；
  side-effect 是 deterministic node，不是 tool（§7）。
- **`check=` 是必要的。** 一個沒有 gate 的 agent node 是 schema error。
- **Artifact：** 該 step 把它的輸出寫到 `step_<name>/<key>`，重跑時若它存在且
  input-hash 吻合就跳過（§9）。一個失敗的 gate（耗盡 `retries` 後）意味著
  **不寫 artifact**，所以重跑會重試它。

### 5.2 Deterministic node

- **沒有 LLM。** author 寫的程式碼（`nodes/` 底下的腳本），跑 **在 sandbox 中**。
  需要一個平台 capability（ingest、KB read…）？它用 **run-scoped credential** 去打那個
  capability 的 **HTTP endpoint**（§8、§15）。它不 import 後端內部。
- 不暴露給 LLM；不受 tool 子集管轄（§7）。
- **Artifact / receipt：** 每個 deterministic node 都必須在 `step_<name>/<key>` 底下
  記錄一個結果，這樣它才可被 checkpoint ——即使它真正的效果發生在別處（例如一次
  ingest 寫一張 `step_ingest/<file>.done` receipt，內含 doc id），它在重跑時才能被跳過。

---

## 6. Gate／check

- **每個 agent node 都有一個 gate**（§5.1）；deterministic node 是它自己的 check
  （做完，然後驗證）。
- **Deterministic check 是主要的；LLM-judge check 是輔助的。** 能機械化驗證的地方
  就機械化驗證（檔案非空；選出的值 ∈ 允許集合；doc 確實落進了某個 collection）。
  把 LLM-judge check（另一次回傳 pass/fail 的 agent turn）保留給只能語意檢查的目標。
  一個 deterministic predicate 是硬保證；一個 LLM 去評判另一個 LLM，是一個不可靠的
  東西在檢查另一個不可靠的東西。
- **失敗時：帶 feedback 重試 `N` 次，然後中止該 step。** check 的失敗原因會被餵回
  *同一個 step 的* 重跑（一個 in-step loop，在同一次 run 之內）。`N` 次嘗試後該 step
  中止；在一個 loop 裡，每個元素的預設政策是 **skip + collect**（§11）。因為一個失敗的
  step 不寫 artifact，*之後* 的一次 run 也會重試它。
- 內建 check（示意）：`check.file_nonempty(path)`、
  `check.choice_in(path, key, allowed)`、`check.collection_has(collection, path)`、
  `check.exec(cmd)`、`check.llm_judge(criteria)`。

---

## 7. Tool vs deterministic node 腳本

兩者都能跑在 sandbox 裡；那 **不是** 區分它們的關鍵——**呼叫者才是。**

| | **agent tool**（含 tool package） | **deterministic node 腳本** |
| --- | --- | --- |
| 由誰呼叫 | **LLM**（一個 agent node） | **orchestration**（`run()`） |
| 需要 LLM schema | 是 | 否 |
| 對 LLM 可見 | 是 | 否 |
| 被什麼界定 | profile 的 **tool 子集** | **run-scoped credential 的 capability scope** |
| 為什麼這樣界定 | LLM 不可預測 → 安全邊界 | author 程式碼是固定的 → 對它能打哪些 capability 做 authz |

- **tool 子集只管 LLM。** deterministic node 不在其中，也不受它約束。
- 共用層是 **sandbox + capability**（§8），不是呼叫介面。一個 tool package 可被兩條
  路徑觸及；一個 deterministic node 不必是一個 package（它可以是一個純指令，
  例如某個 gate 的 `test -s report.md`）。
- **結論：** 任何可靠的 side-effect（ingest、export）都是一個 **deterministic node**，
  絕不是 agent tool。agent 的子集維持讀／探索形狀——所以「避免假性完成」比一個事後
  gate 還強：**agent 根本不持有那個可能搞砸該 step 的 tool。**

---

## 8. Capabilities（HTTP）與 decision/action 模式

- 平台操作（KB ingest、KB query…）都是 **HTTP endpoint**。sandbox 程式碼用
  **run-scoped credential**（§15）去打它們。同一批 endpoint 可以服務外部呼叫端，
  並在需要的地方被包成 agent tool。
- **`ingest_to_collection(collection, path, *, digest=None)`** —— 在
  `rm.using(user=<captured>)` 底下重用既有的 `Ingestor.store` + `index`，等到 `ready`。
  - **Idempotent（冪等）**：SourceDoc id 是 `encode_doc_id(collection, path)` → 重新
    ingest 是一次 **upsert**，絕不重複（重跑下安全）。
  - **要求該 collection 已存在**（不 auto-create）。
  - 寫一張 `step_ingest/<file>.done` receipt；對應的 gate
    `check.collection_has(collection, path)` 把該 doc 讀回來並斷言 `ready`。

### 帶參數 side-effect 的 decision/action

當 LLM 必須 *影響* 一個可靠的 side-effect（「把這個檔案送到 collection X」）時，
它 **不** 呼叫 API。它把那個參數記錄成 **資料**；一個 deterministic node 把它帶到
capability：

```python
# agent node：LLM 決定；把參數 X 記錄成資料（write_file 在它的子集裡）
await agent_step(
    wf, phase="classify",
    prompt=f"Read {f}. Pick its collection from {allowed}. "
           f"Write {{collection, digest}} to plan/{f}.json.",
    tools=["read_file", "write_file"],                       # 沒有 ingest tool
    check=check.choice_in(f"plan/{f}.json", key="collection", allowed=allowed),
    retries=2,                                               # 無效的 X → feedback → 重選
)
# deterministic node：orchestration 把 X 帶到 capability（LLM 不參與）
plan = wf.read_json(f"plan/{f}.json")
await wf.ingest_to_collection(plan["collection"], f, digest=plan["digest"], phase="ingest")
```

X（`plan["collection"]`）的旅程是 **LLM → 檔案 → deterministic node → capability**。
「LLM 帶一個參數」**不** 等於「LLM 呼叫 API」。gate 把 X 夾到允許集合內；
node 保證 exactly-once。

---

## 9. 執行模型 —— 檔案系統 *就是* journal

這是整個設計的核心。它取代任何獨立的 journal/replay 機制。

- **每個 step checkpoint 到 workspace**，落在該 run 的 **journal 家目錄**
  `/.workflow/<workflow_id>/`（舊的單數 workflow → `/.workflow/_default/`），
  在 `/.workflow/<workflow_id>/step_<name>/<key>`（key = loop 元素／呼叫身分，
  例如 `/.workflow/collections/step_classify/file_7.json`），連同它的
  **input-hash**（`= hash(該 step 的 arguments)`，依 §3 的慣例）。journal 住在它
  自己的資料夾裡，這樣它就不會再散落在 workspace 根目錄；而每個 workflow 的
  `step_*` artifact 都歸在那個 workflow 的資料夾底下（#136）。本文件其他地方用的
  裸 `step_<name>/<key>` 簡寫，永遠指那個 run 的 journal 家目錄 *裡面* 的這條路徑。
- **On-demand 的就地跳過。** 一次 run 從頭重跑 `run()`。**當控制流抵達某個 step 時**，
  該 step 先檢查它自己的 artifact：若它存在 **且** input-hash 仍吻合 → **跳過**
  （回傳快取的 artifact，不重做這份工，不重新呼叫 LLM，不重新貼回 chat）。否則 →
  **執行**，然後寫 artifact + input-hash。沒有預先掃描——檢查很便宜而且就地進行。
- **Auto-invalidation（自動失效）。** 因為 input-hash = `hash(args)`，編輯一個上游
  artifact 會改變 `run()` 往下游傳的東西 → 下游 step 的 hash 不再吻合 → 它重跑。
  **編輯上游會自動重跑受影響的下游** ——不用手動記帳。
- **`cache=False`（永不快取）。** 一個 step 可以選擇永遠重跑（或者因為它的 inputs
  總是在變而自然永遠重跑，例如「抓最新的」）。它的下游也跟著重跑，正確無誤。
- **Determinism 講的是 *身分*，不是輸出。** step 的輸出可以完全 nondeterministic（LLM）。
  必須可重現的是那個 *step 身分集合* ——由 §3 的控制流慣例保證（迭代穩定集合、
  只在 inputs/artifact 上 branch）。

這一個機制，免費給我們：

- **Resume／crash／restart 恢復** —— 重跑；已完成的 step 跳過（artifact 活在
  持久的 FileStore 裡）。
- **Retry／rewind** —— **停掉 run，在正常的 file UI 裡編輯或刪除 artifact，再按 Run。**
  刪掉 `step_X/<key>` 強迫 step X 重跑；編輯一個上游 artifact 透過 input-hash 重跑它的
  下游。沒有 rewind API、沒有 `retry_to` 清單、沒有 positional-prefix 規則。
  *（這些較早的機制全部移除——被這個取代。）*
- **「從頭來過」的重置** —— 刪掉該 run 的 journal 資料夾 `/.workflow/<workflow_id>/`
  （或其中的 `step_*` artifact）；保留 inputs。為此而需要的 #52 per-turn-snapshot
  依賴已不再需要。

---

## 10. 人類互動與接手

一次 run 與一個互動式 workspace 是 **同一個 item、同一個 `ChatTurnEngine`、
同一個 conversation 的兩種模式**。每個 agent node 是那個 item 上的一次 turn，
所以一次 run 留下完整的 transcript + 檔案。

- **Stop 然後接手。** 任何時候人類都可以 **Stop** 這次 run（一個控制動作，
  不是 chat 訊息；重用 `cancel_current`）。run 進入終態；item 開放給互動使用；
  人類從當下的檔案 + transcript 繼續。對一個平行 batch（§11），在途的元素被取消；
  已完成的（idempotent、已 commit）保留並回報。這是 agent 失控時的逃生口：
  停下、檢查、編輯 artifact、重跑。
- **當 `running` 時，item 是 workflow 驅動的** ——人類不能自由 chat 進同一個 turn
  佇列。一旦 run 進入終態（或 `awaiting_human`），自由 chat 才開放。
- **Human gate（v1）。** `await human_gate(wf, phase, title, summary, allow)`
  暫停 run 並記錄一個 **pending decision**（它的結果只是另一個 **artifact**，
  在 run 的 journal 家目錄裡的 `step_<gate>/decision.json` ——即
  `/.workflow/<workflow_id>/step_<gate>/decision.json`）。run 停下；人類透過
  `POST .../runs/{id}/decisions` 帶 `{choice, input?}` 回應；重跑時找到那個 decision
  artifact，gate 讀它，執行繼續。`allow` 列出 FE 提供的選項；一個 `revise` 選項會
  揭露一個自由文字 `input` 讓 body 可以據以行動。body 看到的 outcome：
  `approve` / `reject`（→ 結束 + 互動接手）/ `revise`（+ input，例如 `→collections`
  從那則 note 重新產生它的草稿）。**「Retry/rewind」不是一個 gate outcome** ——
  它是 §9 那個基於檔案的機制（刪 artifact + 重跑）。
  - **為什麼是 v1：** 兩個使用情境都以一個不可逆的 commit（發佈一份 report；
    寫進 collection）收尾，那 **必須先被確認**。標準形狀是 **produce → review → commit**：
    agent 產出可審閱的 artifact（安全），一個 `human_gate` 讓人類核准，唯有此後一個
    deterministic node 才 commit 那個 side-effect。因為 gate 坐落在 commit *之前*，
    一個 `reject` 不會留下任何已 commit 的東西。
- **Steer-and-resume（#288）。** 在一次 run 的活躍窗口之外——在一個 gate，或者它已進入
  終態（`done` / `error` / `cancelled`）——人類可以 **用文字重新導向 run**，而不是
  手動編輯檔案（§9）。他們在 run 的 chat 裡打一段自由文字指令（例如
  *"use the a, b collections and redo the upload"*）；一次唯讀的 **steerer** turn
  讀目前的 inputs + journal + transcript，提出一個 **steer plan**：要重寫哪些 input
  檔案、要 **invalidate** 哪些 step（刪掉 artifact → 強迫重跑）。這個 plan **在套用之前
  先被審閱**（produce → review → commit，跟 gate 同樣的形狀）：一張 confirm card 顯示
  檔案 diff + 哪些 step 會重跑 vs. 被保留（blast radius，影響範圍），人類
  **approve／reject／re-instruct（重下指令）**。一旦 approve，一個 deterministic step
  套用編輯 + 刪掉被 invalidate 的 artifact，然後 **同一個 run 繼續**（§9 重跑：
  input-hash 仍吻合的已完成 step **跳過** ——*incremental*，昂貴的前綴不重做）。
  一條 run 進行中的指令會先 **Stop** 這次 run（在途的 node 反正在 resume 時就會重跑），
  然後 steer。
  - **詞彙 = 編輯 inputs + invalidate step** —— 這兩個通用動作；下游重跑透過
    input-hash 串聯（§9）。它是平台層級的，不需要 **任何 author 程式碼**：steerer 可以
    重寫 journal（`/.workflow/`）*之外* 的任何 workspace 檔案，並 invalidate 任何 step。
    LLM 只 *提出*（decision）；deterministic 的 apply *動手*（action）——又是 decision/action
    切分（§8）。Steering 與一個 gate 的 `approve` / `reject` / `revise` **並存**：
    那些是 author 的 in-body outcome；steerer 是疊在其上、永遠可用的自由文字路徑。
  - **Endpoints：** `POST .../runs/{id}/steer {instruction}`（若 run 在跑就先 Stop →
    跑 steerer → 設 `pending_steer`，run 進入 `awaiting_human`）；
    `POST .../runs/{id}/steer/confirm {approve}`（approve → 套用 + 繼續；reject →
    丟棄該 plan；re-instruct → 帶一個新指令再呼叫 `steer`）。提出的 plan + 人類的回答
    會 journal 到 `/.workflow/<workflow_id>/steer/` 供稽核。
  - **仍然延後：** 真正的 *live* 注入（一則 note 被送進一個 *已在執行* 的 node，
    而不 Stop）——不需要；Stop-then-steer 已涵蓋這些情況。

---

## 11. 控制流

- 基本元素：**sequence**、**`for`-each**（純 Python）、in-step **retry**
  （`retries=`），與跨 run 的 resume（§9）。
- **平行 for-each 在 v1 裡。** `async`/`gather` 讓 *orchestration* 免費並行——但它
  本身 **不會** 並行化 agent turn，因為被重用的機制是 **serial-per-item（每 item 串行）**：
  `ChatTurnEngine` 是 FIFO-per-key、每 item 一個 sandbox、每 item 一個 chat。
  因此真正的並行會把每個元素 fan out 到它 **自己的 turn-key + 短命 sandbox**
  （以及它自己的 chat 子串流），受 **全域 concurrency cap**（§16）約束；
  parent `gather` 並彙總。
  - **結論（已接受）：** 一次 batch run 產出 **每個元素的子 log**，不是一個合併的
    conversation ——這正是 batch 該有的形狀（每個檔案的狀態 + 一個彙總，不是一個
    N 路交錯的 chat）。「一個可讀 chat」的模型是給互動／單軌 run 的。
  - Deterministic／純 I/O 的 node（例如一次 ingest HTTP 呼叫）不碰引擎，可以便宜地
    `gather`。
- **沒有 branching 基本元素。** 用一個對 inputs/artifact 的普通 `if` 來 branch；
  用資料 route（一個 step 寫一份 plan，一個 loop 消費它）而不是控制流 branch。

---

## 12. Observability（phase 層級）

- 我們支援 **phase 層級的觀察**（「run 到哪了／哪個 phase 壞了」），**不是** node 層級的
  視覺化 authoring。
- diagram = 一個 **靜態 phase 骨架**（`MANIFEST.workflow.phases`，跑之前就已知）
  **+ 即時 step events** 疊上去。每個 step 帶 `phase=`；動態細節（loop 進度、retry、skip）
  顯示在一個 phase node *底下*（「12/20，1 failed」），不是預先畫好的 per-element node。
- **Live** = SSE（擴充 `api/events.py`，鏡射到 `web/src/events.ts`：
  `PhaseEntered` / `StepStarted` / `StepPassed` / `StepFailed` / `StepSkipped` /
  `StepRetrying` / `AwaitingHuman`）。**Historical** = 查 `WorkflowRun`。
- **Caveat（別誇大）：** 這個骨架是 *宣告* 的 phase 集合；若程式碼對某個 input 跳過/
  重排 phase，diagram 可能漂移。把 phase 維持得粗、大致線性；把沒跑到的 phase
  標成 skipped。

---

## 13. WorkflowRun（持久化 resource）

一個新的 specstar resource 讓「在哪／什麼壞了」既能即時也能事後回答，而且讓 run 可列出。
**檔案系統就是 journal**（§9），所以 `WorkflowRun` 持有 *狀態*，不持有 step 結果：

- `status`：`pending | running | awaiting_human | done | error`（+ `cancelled`）
- `current_phase`、per-phase 狀態／進度、`failures`（per-element 收集）
- `item_id`、`captured_user`、`started`/`ended`、`result`（`run()` 的回傳值）
- `pending_decision`（+ 由誰決定）—— 在一個 gate `awaiting_human` 時設定
- `pending_steer` —— 在等待一個 steer plan 確認而 `awaiting_human` 時設定（#288）；
  FE 靠哪個 pending 欄位有被設定，來決定渲染 steer confirm card 還是 gate card

---

## 14. Trigger 與 API

**平台的輸入介面就只有兩樣東西。** 其他一切（input 資料夾、`input.json` 內容、
檔案怎麼擺）都是 **profile 的** 事，使用既有的自由 workspace。

1. **Config：`input.json` 在哪裡**（`MANIFEST.workflow.input_json`）。平台把這個檔案
   解析後的內容當作 `inputs` 提供給 `run()`；它不驗證它、也不規定它的形狀。
   **省略它**（#198），平台就推導出 `{profile.upload_dir}/input.json` ——那正是一次
   chat attach 落地的同一個 staging 資料夾（`upload_dir` 預設 `uploads`），所以
   attach 與消費這些檔案的 workflow 永遠不會漂移；只有要覆寫時才釘死一個明確路徑。
   profile **seed** 一個預設的 `input.json`（例如
   `{"files":["uploads/*"],"except":["uploads/input.json"]}`）；人類可以像對待任何檔案
   一樣，在按 Run 之前自由編輯它——**run 之前的 workspace 表現得跟一個非 workflow item
   一模一樣。**
2. **Run** —— `POST /a/{slug}/items/{item_id}/run`（非同步；可由 API 觸發）。在該 item 上
   啟動 orchestrator；run 依 profile 的指示讀 `inputs` + workspace。body 是可選的：
   - **空 body**（只有 `?workflow_id=…` query）—— UI 做的那種普通 trigger；它對
     workspace 裡已經有的東西開跑。
   - **`multipart/form-data`**（#197）—— 一個外部 trigger 在同一次呼叫裡上傳 workflow 的
     input **檔案**，因為我們是透過 workspace 跟 workflow 對話，不是透過一個 JSON body。
     每個 `file` part 的 **filename 就是它的 workspace 路徑**（允許子目錄，例如
     `inputs/data.csv`）；`workflow_id` 可以搭在一個 query param **或** 一個 form field 上
     （query 優先）。這些檔案在 run 開始 **之前** 被寫入（覆寫，last-write-wins）；一條
     逃出 workspace 根目錄的路徑會以 **400** 中止整次呼叫（沒有半寫的東西，沒有 run）。
     請求裡 **沒有 `input.json`** ——若一個 workflow 想要一份，它就只是其中一個被上傳的檔案。

**沒有獨立的「manual vs auto」模式** —— 兩者都化約為 *準備 item 的 inputs，然後 Run*：

- **人類：** 開一個 workflow-profile item，透過 file UI 丟檔案／編輯 `input.json`，
  按 **Run workflow**。
- **外部／週期性：** 建一個 item，然後 **把 input 檔案附在同一次 multipart 呼叫裡
  一起 Run**（如上）——一個自包含的 trigger。（先透過既有的 file 路由上傳檔案、
  再帶空 body 呼叫 Run，是等價的；multipart form 只是便利。）

按下 Run 後，平台做的就只有兩件事：**orchestrator 更新 `WorkflowRun` 狀態**，
以及 **agent node 像在跟使用者說話那樣串流進 item 的 chat**（§1、§5.1）。

- **Poll：** `GET /a/{slug}/items/{item_id}/runs/{run_id}` → 狀態 + result +
  per-phase 進度 + failures。
- **Stream：** `GET .../runs/{run_id}/stream` → SSE（重用 `subscribe_sse`）。
- **Decide：** `POST .../runs/{run_id}/decisions` → `{ choice, input? }`。
- **Discover：** `GET /a/{slug}/profiles` 列出 profile、標出哪些有 workflow、
  回傳每個 `MANIFEST`（title / phases），讓 FE 能渲染那個 Run 的操作入口。
- **一個 item 可以承載多次依序的 run**（prepare → run → 再 prepare → 再 run）；
  同一時間每個 item 至多 **一個活躍 run**。
- artifact 就是 workspace 檔案，透過 item 既有的 file 路由取得。

---

## 15. 身分與 auth

- **在 trigger 邊界重用既有的 `get_user` 接縫**；production 在它背後抽換一個真正的
  實作（這裡不另建一套 token 機制）。
- **在 trigger 時把 acting user 捕捉**到 `WorkflowRun` 上。背景 step（以及任何重跑）
  沒有 request context，所以它們在 `rm.using(user=<captured>)` 底下行動——`created_by`、
  KB ingestion 的歸屬、通知都維持正確（跟 index/wiki job pod 同一套模式）。
- **Run-scoped credential：** 透過 HTTP 打 capability 的 sandbox 程式碼會拿到一個注入
  進它 env 的短命 credential；它對應到被捕捉的 user、scope 在那次 run 允許的 capability，
  並在 run 結束時失效。
- **Gate 核准：** 任何有存取權的、已認證的人類都可以行動；**誰行動了會被記錄。**

---

## 16. 生命週期與資源

- **Sandbox：** 第一次 `exec` 時 lazily 建立。在進入 **終態** 時釋放（重用
  `registry.close_session` + `turn_engine.forget`），以及在 **`awaiting_human`** 時釋放
  （一次暫停可能持續好幾天；FileStore 持久保存檔案，所以 resume 會 lazily 重建 sandbox）。
  平行 for-each 用 **per-element 短命 sandbox**（§11）。
- **Items：** 一次 run 的終態 **不會自動關閉** item（它仍是一個 workspace，供檢查／
  重跑／接手）。由 API 建立的 item 會累積 → 由一個 **TTL／keep-last-K** 保留設定清掉，
  或由人類關閉。
- **Concurrency：** 對並行 run + sandbox 的一個 **全域 cap**（也涵蓋平行 for-each 的
  元素）；超出的部分排隊（`pending`）。這個 cap 是一個 config 設定。

---

## 17. 健壯性（headless）

- **Timeout：** per-step（每次 agent turn 的最大時長）**以及** 一個 per-run 的
  wall-clock 上限；超過任一個就中止為 `error`，記在 `WorkflowRun` 上。
- **Budget：** 一個 per-run 的 **max-steps** 硬上限（防無窮 loop）與一個可選的
  token/cost budget。
- **失敗通知（v1）：** **pull**（poll `WorkflowRun`：`error` + 哪個 phase + 為什麼）
  **+** 對 owner／watcher 的 in-app 通知（既有機制）。一個對外的 **webhook** 延後。

---

## 18. Versioning

- **input-hash 扛了大部分的重量。** 因為一個 step 的 hash 包含它的 arguments
  （其中包含解析後的 prompt），**編輯 workflow 會在下一次 run 重跑它影響到的 step**，
  並讓不受影響的 step 維持快取。這是 §9 的紅利，不是額外的機制。
- **破壞性變更靠加一個新 profile 來做**，不是就地改一個。一個 profile 被當成一個
  **不可變的行為版本**：出貨 `profile-v2`；既有的 item 繼續指向那個未被動過的舊 profile。
  不建任何真正的 module 層級版本釘選。

---

## 19. 平台 vs App 的邊界

- **平台 = 磚塊 + 強制 + 兩個介面：** `agent_step`、deterministic node、
  mandatory gate、`for`/parallel/retry、§9 的 filesystem-journal + input-hash skip、
  `WorkflowRun`、capability（`ingest_to_collection`…）、run-scoped credential、
  allowed-set clamp（一個 deterministic gate）、`input.json` 位置 config，與 Run endpoint。
- **一個 App/profile 把這些磚塊組合起來**，配上它的 prompt、規則、`input.json` 佈局、
  與 node 腳本。一個 workflow *如何* 表現（split / digest / routing）是 App 實作，
  不在這裡的範圍內。

---

## 20. 範例 —— 「intake」App（示意）

把使用情境 2 當作一個 profile 層級的 workflow，展示標準的 **produce → review → commit**
形狀。collection 集合 **預先定義在 profile 裡**；被 seed 的 `input.json` 說「檔案在
profile 的 `upload_dir`」（預設 `uploads/`，就是一次 chat attach 落地的同一個資料夾，#198）；
使用者丟檔案、按 Run。

```python
async def run(wf, inputs):
    allowed = wf.config["collections"]              # 預先定義在 profile 裡（不是 per-run）
    files = wf.glob(inputs)                          # inputs = 解析後的 input.json（檔案 spec）

    # Phase 1 — PRODUCE：對每個檔案 classify+digest。安全——只寫 plan/<f>.json。平行。
    async def classify(f):
        await agent_step(                            # agent node：DECISION 記錄成資料
            wf, phase="classify",
            prompt=f"Read {f}. Split per your profile; for each piece pick a collection "
                   f"from {allowed} and write a digest. Record the plan in plan/{f}.json.",
            tools=["read_file", "write_file"],       # 沒有 ingest tool — agent 不能 commit
            check=check.choice_in(f"plan/{f}.json", key="collection", allowed=allowed),
            retries=2,
        )                                            # → step_classify/<f>.json（未變則重跑時跳過）
    await wf.map(classify, files)                    # 平行 for-each，受 concurrency cap 約束（§11）

    # Phase 2 — REVIEW：人類在任何東西被 commit 進 KB 之前確認。
    plan = {f: wf.read_json(f"plan/{f}.json") for f in files}
    decision = await human_gate(
        wf, phase="review",
        title="Approve filing these into collections?",
        summary=plan,                                # 人類審閱整份 routing plan
        allow=["approve", "reject"],
    )
    if decision.choice == "reject":
        return {"status": "rejected"}                # 沒有 commit 任何東西；item 維持互動供接手

    # Phase 3 — COMMIT：deterministic、idempotent。只有在核准後才跑。
    failures = []
    async def commit(f):
        try:
            for piece in plan[f]:
                await wf.ingest_to_collection(piece["collection"], piece["out_path"],
                                              digest=piece["digest"], phase="ingest")
                await check.collection_has(piece["collection"], piece["out_path"])
        except StepFailed as e:                      # per-element 政策：skip + collect
            failures.append({"file": f, "error": str(e)})
    await wf.map(commit, files)
    return {"processed": len(files) - len(failures), "failures": failures}
```

一個發現壞結果的人類，**停掉 run，在 file UI 裡刪除或編輯 `step_classify/<f>.json`
（或 `plan/<f>.json`），再按 Run** ——只有受影響的檔案重跑（§9）。而且在 **review**
gate 被核准之前，沒有任何東西會抵達一個 collection。

---

## 21. Phasing 與非目標

整本手冊就是目標。建置是有序的（先地基），但它是 **一個 v1 範圍** ——
**`human_gate` 在 v1 裡**，因為兩個使用情境都以一個不可逆的 commit（發佈一份 report；
寫進 collection）收尾，那必須先被確認（§10）。filesystem-journal（§9）讓 durability
*以及* gate 都很便宜，所以沒有理由把它們拆開。

**v1（兩個使用情境需要的一切）：**
agent + deterministic node；帶 in-step retry-with-feedback 的 mandatory gate；
`for`-each **以及平行 for-each**（per-element sandbox + concurrency cap、skip + collect）；
**filesystem-journal + input-hash** 執行模型（resume / retry / rewind / reset /
crash-recovery、`cache=False`）；**`human_gate`**（produce → review → commit；
decision-as-artifact、`awaiting_human`、**decisions** endpoint、暫停時釋放 sandbox）；
**Stop 然後接手**；phase 層級的 diagram + events；`WorkflowRun` 狀態；**Run** endpoint
+ Discover + Poll + Stream；`input.json` 位置 config + profile seeding；`get_user`
身分 + captured-user + run-scoped credential；sandbox 生命週期；concurrency cap；
timeout + max-steps；pull + in-app 失敗通知；`ingest_to_collection`。

（建置順序：gate 在序列中較晚落地，因為它依賴引擎 + `WorkflowRun`，但它在範圍內——
v1 的 gate 沒有它就不算「完成」。）

**延後／非目標：** 對話式的 steer-and-resume **已在 #288 落地**（§10）——只有 *真正
live* 的 run 進行中注入（一則 note 進一個已在執行的 node，而不 Stop）仍延後；
~~宣告式 DAG authoring~~（§22 為 *使用者* authoring 重新打開了它的一個 **降階、非
Turing-complete** 版本——理由正是這裡列出的「給非工程師 authoring」前提如今成立）；
node 層級的視覺化編輯；控制流 branching 基本元素（用資料）；
對外的 webhook callback；真正的 module 層級版本釘選（用新 profile 慣例）；
真正的 SSO authz；把 LLM-judge check 當成超過偶一為之的逃生口之外的任何東西。

---

## 22. 使用者自己做 workflow（降階 DSL，#323）

§3 否決了宣告式 DSL，理由是「只在『給非工程師 authoring』或『執行期可編輯定義』才
划算」。**#323 正是那個前提成立的時候**：讓一個 *使用者*（非工程師）跟 AI 一起弄出一個
能跑的 workflow——就像 #298 讓他們跟 AI 弄出一個 skill。差別在 skill 是 passive 的
markdown（零執行風險），workflow 會 **執行**。所以使用者 **不能寫 code**：orchestration
`run()` 是 trusted 後端 Python（持有 turn engine／sandbox 生命週期／capability
credential，§1），把使用者的 Python 跑進 API 不安全。

**解法 = 降階的資料 + trusted interpreter。** 使用者寫一份 `workflow.json`（一個
**非 Turing-complete** 的 DSL），一個 trusted 的 generic `run()` 讀它、把每個 step 的
欄位當參數 dispatch 到 §5 那些 *既有* 的 primitive。沒有使用者 *code* 跑進 API；
§9 的 filesystem-journal + input-hash skip 原樣成立（DSL 固定、代入 deterministic →
step 身分穩定）。詳細的 build 計畫 + 完整 grill 決策見
[`plan-issue-323.md`](plan-issue-323.md)。

- **詞彙（Q7 天花板）：** `steps` 有序清單；step `type` ∈
  `agent` / `sandbox` / `gate` / `capability` / `map`（唯一的迴圈，one-level）；
  `{x}` / `{x.field}` 唯讀字串代入（**非任意運算式**；`{x.field}` 在 `x` 指向 `.json`
  檔時讀檔取欄位——正是 §8 的 decision→data→action routing）；`check` 是 §6 的宣告式
  builder；branch 用資料 routing（§11）。**沒有** revise-loop / branch / 巢狀 map——那些
  留給 dev 的 `run.py`。
- **安全不變式（Q4）= 「使用者 workflow 能做的，恰好等於它的作者親手能做的」。**
  capability（`ingest_to_collection`、`upsert_context_card`）是 interpreter 在
  **captured user 的 authz scope** 下跑的 DSL primitive；一個使用者的 `sandbox` step 是
  **compute-only**（不給 run-scoped credential），所以 side-effect 永遠只走受控的
  capability primitive。authoring 不產生任何新權限。
- **儲存／發現（Q5，照搬 §298 的 skill 模型）：** 一份使用者 workflow 住在
  `<workspace>/.workflows/<id>.json`（FileStore），**item-local**、live 讀、同名
  **shadow** 掉 package workflow，用既有的通用資料夾下載 export，由 dev promote。
  **不是** specstar resource。
- **一個 interpreter 服務兩層（Q6）：** 一個 *package* workflow 可以是 `run.py`
  （trusted Python）**或** `workflow.json`（被 interpret）。**promote = 把 json 複製進
  profile**，免 transpile（正如 skill promote 就是複製 `SKILL.md`）。
- **Co-design（Q8）：** 一個 `author-workflow` shared meta-skill 引導 AI 起草 DSL；
  一個 `save_workflow` tool 在寫入前 **驗證**（schema、phase 一致、`tools` ⊆ profile
  上限、capability 在允許清單、`check` 格式）並把無效的 DSL **退回原因讓 AI 修**
  （正如 `save_skill` 擋 body cap）。v1 **不**自動試跑——使用者按 Run 來測。
- **上限／authz（Q9）：** 與 package workflow **同一組** cap（§16–§17）；能存取該 item
  就能 author + run；captured-user scope。

**v1 範圍（已全數落地）：** DSL 引擎（schema + interpreter + validator）、package 端可
interpret `workflow.json`（= promote target）、`save_workflow` + `author-workflow`
+ workspace 列表（P1–P3，PR #332）；**workspace 就地自助執行**（orchestrator 注入
`load_workspace`、Run-route fallback、item-scoped `/workflows` 端點 — workflow 唯一
超出 skill 的地方）+ **FE Workflows panel**（每列 Run + 下載/匯入，掛在 AgentPanel）
（P4–P5）。**v2 延後：** revise-loop / branch / 巢狀 map；co-design 自動試跑；promote 時
transpile 成 Python；per-user quota。
