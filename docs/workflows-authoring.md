# 撰寫 workflow

> 這篇是 **dev 端**（Python `run.py`）的 how-to。想看三個擴充面（tool / skill / workflow）
> × dev/user 的全景,以及 **user 端**用 `workflow.json` DSL 共創 workflow 的那條路,請先讀
> [`extending-the-platform.md`](extending-the-platform.md)。

撰寫 workflow 的實用指南——block catalog、各種慣例,以及讓你不會掉進「開機就 crash」迴圈的
工具。想了解背後的*為什麼*（設計、decision/action 拆分、filesystem journal），請讀規格文件
[`workflows.md`](workflows.md);這篇是 how-to。

> TL;DR — `python -m workspace_app.workflow new <app> <profile> <id>` 會 scaffold 出一個
> 可執行的 workflow;接著編輯它的 `run.py`;`python -m workspace_app.workflow check`
> 會在你啟動 app 之前告訴你哪裡有問題。

## workflow 是什麼

一個 workflow 就是**一個 `async def run(wf, inputs)`**（orchestration）加上 profile 的
`_profile.json` 裡一小段**資料 manifest**(它的 id、title,以及 UI 用來繪製的 phase 骨架)。
控制流就是普通的 Python——`for` / `if` / `await`——跑在一個 *step* 函式庫之上。這條開發者
寫的 `run.py` 路徑沒有 DSL;不過 #323 另外為*使用者*開了一條宣告式的 `workflow.json`(跟 AI
一起 author、存成資料而非 Python,由 trusted interpreter 跑在同一批 step primitive 之上)——
細節見 [`workflows.md`](workflows.md) §22 與**視覺手冊** [`workflows-syntax.html`](workflows-syntax.html)（每個語法配動畫 + sample JSON),這篇不重複。

它落在 **profile** 層級:

```
apps/<app>/profiles/<profile>/
  _profile.json                       # 宣告這個 workflow(id、title、phases…)
  workflows/<id>/run.py               # async def run(wf, inputs) — orchestration
```

`run.py` 是以檔案路徑載入的（所以帶連字號的目錄也能用）——請用**絕對 import**
(`from workspace_app.workflow import ...`),絕不要用相對 import。

```python
from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_write_step
from workspace_app.workflow.handle import WorkflowHandle


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    await agent_write_step(wf, phase="note", out="note.md", prompt="Write a hello note.")
    return {"status": "done"}
```

`run()` 回傳一個可 JSON 化的摘要（成為這次 run 的結果）。`inputs` 是解析後的
`input.json`(見 [Inputs](#inputs))。

## 快速上手

1. **Scaffold** 一個起點——挑最接近你需求的 recipe:

   ```bash
   uv run python -m workspace_app.workflow new myapp default ingest-logs --recipe review-commit
   ```

   Recipe:`minimal`（一個 agent step,跑到 *done*）、`review-commit`（produce →
   human gate → commit,跑到 *awaiting_human*）、`batch`（`wf.map` 跑過上傳檔）。
   它會寫出一份有註解的 `run.py`,並在 `_profile.json` 裡註冊好與程式碼相符的 phases。

2. **編輯** `run.py`——改 prompt、加 step、接上你的 commit。

3. 啟動前先 **Check**:

   ```bash
   uv run python -m workspace_app.workflow check        # 所有 app
   uv run python -m workspace_app.workflow check myapp  # 單一 app
   ```

   `check` 會靜態回報:`run.py` 缺檔／沒有 `run()`／無法解析、id 為空或重複、phase id 為空
   (**錯誤**),以及你程式碼裡有 `phase="…"` 字面值卻忘了在 `_profile.json` 宣告
   (**警告**——也就是 drift／typo 的情況)。只要有任何錯誤它就會以非零碼結束,所以很適合
   當 pre-commit hook。

4. **重啟** app——workflow 在開機時被探索。

## block catalog

以下所有東西若無特別註明,都是從 `workspace_app.workflow` import 的。`wf` 是
[`WorkflowHandle`](#the-wf-handle)。

### Agent node(LLM)

一個 agent node 會在 item 上跑**一個有 gate 的 LLM turn**——它會像互動 turn 一樣 stream 進
chat。gate 是必要的:沒有 gate 的 agent node 無法表達。

```python
await agent_step(wf, *, prompt, phase, check, name=None, key="",
                 tools=None, retries=0, cache=True) -> Any
```
這是通用形式。`check` 是必填的 postcondition(見 [Gates](#gates));`retries` 會把失敗原因
回灌進 prompt 後重跑。`tools` 是這個 agent 被允許的 tool 子集(⊆ profile 上限)。

```python
await agent_write_step(wf, *, prompt, phase, out, name=None, key="",
                       tools=None, retries=0, cache=True, check=None) -> Any
```
常用的簡寫:模型**把檔案內容當成它的回覆產出**(它*不會*呼叫 `write_file`——不管小模型*還是*
大模型,產長 tool 參數都不可靠),然後這個 step 把它寫到 `out`,預設以 `file_nonempty(out)`
做 gate。給它**唯讀**的 tool。

### Deterministic node(無 LLM)

```python
await sandbox_node(wf, *, run, phase, check=None, name=None, key="", cache=True)
    -> {"exit_code": int, "stdout": str}
```
在 sandbox(沙箱)裡跑一個指令。無 LLM;這就是純粹的作者程式碼。拿它做可靠、可腳本化的工作;
如果它的成功與否不是一目了然,就加 gate。

### `reads`:讓「檔案內容」參與 cache(#429 P1)

step 的 skip 條件是 `input_hash(它的 args)`——**args 沒有的東西,對 cache 不存在**。所以一個
讀檔的節點,如果它讀的檔**內容**變了、但你傳的 args(指令字串、prompt、路徑)沒變,它會**錯誤地
skip、拿到過期結果**,而且不會報錯。`sandbox_node` 的 `run` 是不透明指令、`map over glob` 尤其
危險(命中路徑不變、內容變了)。

正解是**宣告 `reads`**——列出這個節點依賴的檔;引擎會自動把它們的**內容指紋摺進 input-hash**,
於是編輯任何一個宣告的檔就會重跑這個節點。你**不用**自己去算 hash 再 interpolate 進 args:

```python
await sandbox_node(wf, run="python analyze.py", phase="a", reads=["logs/*.log"])
await agent_step(wf, ..., reads=["spec.md"])          # agent 也吃 reads
await agent_write_step(wf, ..., reads=["src/**/*.py"])
```

`workflow.json` 一樣:`{"type":"sandbox","run":"analyze","phase":"a","reads":["logs/*.log"]}`
(entry 可 interpolate,如 `"{config.dir}/*.log"`)。

**維護 cache 正確性的三條規則,依優先序**:

1. **首選:宣告 `reads`。** 讓引擎代算指紋——作者不可能算錯或忘記 interpolate。
2. **保險:`cache=False`。** 當你連要讀哪些檔都列不出來(例如「抓最新的」),就誠實每次重跑。
3. **下策:手動把指紋餵進 args。** 只有連 `reads` 都不想列時才用——把責任揹回自己身上。

`workflow check` 會對「看起來讀了檔(指令含路徑樣 token)卻沒宣告 `reads`、也沒 `cache=False`」的
sandbox 節點出一個**警告**(以及 `map over glob` 裡沒宣告 `reads` 的 sandbox 步)。它是**啟發式、
低噪音、不擋存檔**的提醒(讀無檔的節點本就該沒有 `reads`)。專案想更嚴可用 `workflow check
--strict`,把這種未表態升成 error——強制「必須表態」(宣告 `reads` 或明寫 `cache`),但表態成什麼
仍是你的判斷。

### Human gate

```python
decision = await human_gate(wf, *, phase, title, summary="",
                            allow=("approve", "reject")) -> Decision  # .choice, .input
```
為人停下來。第一次抵達時 run 會以 `awaiting_human` 暫停;一旦記錄了一個決定,重跑會 replay
已完成的 step、抵達這個 gate、找到那個決定,然後繼續。`summary` 是給人審閱的內容(一個字串或
任何可 JSON 化的值)。這就是標準的 **produce → review → commit** 接縫。

### Gate vs Steer:何時用哪個(#429)

兩者都「暫停等人 → 續跑」,容易混。分工的**一句話判準**——接的是同一條「圖能不能事先靜態畫出」
的總線:

> **「這個暫停點,是我寫 workflow 時就*畫得進圖裡*的嗎?」**
> 畫得進去(發佈前一定要審)→ **gate**;畫不進去(要等 run 跑歪、看到才知道要介入)→ **steer**。

| | **gate**(含 `revise`) | **steer**(#288) |
|---|---|---|
| 誰設計的 | 作者**織進** workflow 的一部分 | 平台能力,**非**作者程式碼 |
| 何時 | 流程裡**預定**的決策點(in-flow) | **任意時點**的臨時介入(out-of-flow) |
| 形式 | **選單式**預定選項(approve/revise/reject) | **自由文字** overlay(改 inputs＋invalidate 步) |
| 留痕/可重播 | 走 journal;**明天再跑會重現** | overlay;**一次性、不重現** |

**「留痕/可重播」是最實用的一欄**:gate 的簽核點明天再跑會**再出現**(它是流程的一部分);steer 的
臨時介入**不會**。所以問「這個介入要不要*每次*都發生」——要 → 寫成 gate;只是這次 → steer。

正例＋反例(擺明兩個誤置方向):

- **gate** 正例:發佈前審週報。
  反例:**不要**用 gate 去接「我臨時想改個方向」——那不是每次都要的簽核,硬塞進 workflow 會讓
  **每次**跑都卡一個其實只有這次要的關。
- **steer** 正例:跑到一半發現方向錯、自由重導。
  反例:**不要**用 steer 去做每次都該有的簽核——那該在設計時就畫成 gate;靠 steer 等於把一個
  計畫內的關口變成「要**記得每次**手動介入」,**會漏**。

### Capabilities(`wf` 上的 deterministic 副作用)

這些是可靠、有 journal、idempotent 的副作用——decision/action 拆分裡的 *action* 那一半。
agent 從不持有這些;是你的 `run()` 在 gate 之後呼叫它們。

```python
await wf.ingest_to_collection(collection, path, *, phase="ingest", cache=True) -> doc_id
await wf.upsert_context_card(collection, keys, *, title="", body="",
                             phase="commit", cache=True) -> card_id
await wf.find_overwrite_card(collection, keys, *, title="") -> {...} | None  # 唯讀
await wf.convert(src, dest, *, phase="convert", cache=True) -> (out_path, kind)
await wf.create_entity(type_name, args, *, phase="commit", cache=True) -> number
await wf.update_entity(type_name, number, patch, *, phase="commit",
                       cache=True, retries=3) -> version
```

`create_entity`／`update_entity`(#419／#429 P2)走**跟 UI／agent 同一條** `EntityStore` 配號＋
驗證管線——**絕不**用 raw `wf.write` 自己挑號寫進 entity 目錄(單一寫入路徑)。`update_entity` 是
**樂觀鎖＋衝突重試**:它讀現值版本、帶版本 merge-patch,若撞上**平行 run** 動過同一筆
(`EntityConflict`)就重讀重試——所以兩條 run 改同一 entity **不會靜默 lost-update**。兩者都有
journal:同 `args`／同 `(number, patch)` 重跑是 idempotent skip,絕不重複建號或重複套用。
`workflow.json` 版:`{"type":"capability","call":"update_entity","type_name":"issue",
"number":"{q.n}","args":{"status":"done"}}`。

`wf.convert`(#324)把一份上傳**先轉成文字、再 file 進 collection**——只存轉好的 artifact,
絕不存原始 binary(topic-hub 的 →collections 在 `input.json` 開了 `convert` 時用它)。它跑的是
跟 index 同一套 KB parser(含 VLM describer),但不 chunk／不 embed,也不碰 `SourceDoc`。
`kind` 有三種:`markdown`(parser 把 binary／結構化檔轉成 markdown,落在 `.md` 名下,例如
`deck.pptx` → `deck.pptx.md`)、`passthrough`(本來就是純文字／程式碼,維持原副檔名)、`none`
(沒有 parser 讀得懂的 binary——此時 `out_path` 是 `None`,呼叫端**跳過**它,絕不把原始 bytes
落地)。回傳的 `out_path` 就是你接著要 file 的那條 workspace 路徑。

### Gates

一個 gate 是一個 postcondition `async (wf, result) -> CheckResult`。內建的:

```python
file_nonempty(path)                       # agent 確實寫了一個非空檔案
choice_in(path, *, key, allowed)          # path[key] ∈ allowed(把 agent 的選擇夾在範圍內)
collection_has(collection, path)          # ingest 確實把 doc 落地為 ready
```
需要時自己寫——回傳 `CheckResult(True)` 或 `CheckResult(False, "why")`;那個原因會回灌進
agent 的 retry。`fail("reason")` 會中止當前的 step／element(`StepFailed`)。

### The `wf` handle

檔案 IO（路徑相對於 workspace;開頭的 `/` 可有可無）:

```python
await wf.read(path) / read_text(path) / read_json(path)
await wf.write(path, data) / write_json(path, obj)
await wf.exists(path) / delete(path)
await wf.glob(patterns, exclude=None) -> [paths]      # 已排序、deterministic
```

平行 for-each(手冊 §11):

```python
failures = await wf.map(fn, items, *, concurrency=8)  # 跳過+收集;回傳 [{item, error}]
sub = wf.sub_handle(element_key)                       # #429 P5:每元素獨立 turn lane
```

**真平行 agent turn(#429 P5)**:`wf.map` 的元素若含 agent turn,同一個 handle 上會被
ChatTurnEngine 的 FIFO-per-key **序列化**。要真平行,對每個元素取一個 **sub-handle**
——它**共用** workspace／journal／capabilities,但 agent turn 跑在**自己的 turn lane**上。DSL
`map` 已經**自動**這麼做(每元素一個 `wf.sub_handle(ekey)`),所以 `workflow.json` 免改。

`concurrency` 是**請求上限**,實際並發是 `min(concurrency, 模型後端的並發能力)`——**request,
不是 guarantee**。單一本地模型(如 Ollama)後端並發≈1,同一份 workflow 會**自動退化成序列**
(你在本地設 `concurrency: 8` 沒變快,是模型端在排隊,不是 bug);hosted／多 replica 才吃得到
平行。**取消**時:停止派發新元素、砍掉 in-flight 的 agent turn;若某步的副作用已執行但**還沒落
journal**,該步下一輪**重跑**——capabilities 都**冪等**(create-by-args／update-by-patch／
ingest-by-doc-id／card-by-key),重做不重複,孤兒 side-effect 在 re-run 時**自癒**、不會靜默遺失。

Context:`wf.config`（manifest 的 `config`)、`wf.user`(捕捉到的 actor)、
`wf.upload_dir`（profile 的暫存資料夾,預設 `uploads`)、`wf.workflow_id`、
`wf.journal_dir`。

### Engine primitive

`run_step(wf, *, name, key="", phase="", args, execute, check=None, retries=0,
cache=True)` 是上面那些 adapter 的底層。只有當你想要一個自訂、有 journal、會發 phase 的
deterministic node(例如一個不屬於 capabilities 的 commit)時,才直接動用它。

## 重要慣例

- **把一個 step 的輸入當成它的參數傳進去。** step 的 cache key 是 `hash(args)`——所以
  編輯某個上游 artifact 會改變下游的 arg,並自動重跑它。不要在 step 內部讀環境狀態。
- **讓 `phase=` 維持字面值。** 它必須對上 `_profile.json` 宣告的某個 phase(那正是
  `check` 交叉比對的對象)。把 step 身分的動態部分放在 `name=` / `key=`,別放在 `phase=`。
  Phase 應該粗顆粒、大致線性——它們是進度圖,不是每一個 step。
- **filesystem 就是 journal。** 每個 step 寫出 `/.workflow/<id>/step_<name>/<key>.json`。
  重跑會跳過那些 artifact 已存在且 input-hash 相符的 step。要強制重跑,就編輯／刪除那個
  artifact 再按 Run;`cache=False` 永遠重跑。沒有 rewind API——編輯檔案*就是*介入手段。
- **decision/action 拆分。** LLM 只負責*決定*並把它的決定寫成資料;可靠的副作用
  (`ingest_to_collection`、`upsert_context_card`、你的 `sandbox_node`)是 agent 從不持有
  tool 的 deterministic node。這比事後的 gate 更強。
- **一個 agent node 可以持有的 tool。** 當你為 `agent_step` 列出 `tools=` 時,給 app/workflow
  agent **`ask_knowledge_base`** 來諮詢 KB(它會委派給一個 KB sub-agent,把吵雜的 retrieval
  擋在你的 context 之外)。在 app workflow 裡**絕不要**列 `kb_search` / `search_wiki`——
  那些是 KB/wiki agent 自己的 retrieval 葉節點,需要 app 沒有的 retriever(會失敗)。唯一的
  例外是 `lookup_glossary`——一個便宜、deterministic、exact-key 的 card 查詢,你可以直接
  授予(#270)。

## Inputs

平台只把一個 input 檔案露出給 `run()`:`input.json`,預設在 `{upload_dir}/input.json`
(用 manifest 的 `input_json` 覆寫)。它的*形狀*是你 workflow 自己的事——平台不驗證它。用
`await wf.read_json("uploads/input.json")`(或你 manifest 指向的任何路徑)來讀它,或者
靠 driver 傳進來的 `inputs`。profile 會像其他 starter 檔案一樣 seed 一份起始的 `input.json`。

## manifest

```jsonc
{
  "workflows": [
    {
      "id": "ingest-logs",                 // 穩定、唯一;定址 run.py + picker
      "title": "Ingest logs",              // 在 Run picker 顯示
      "tag": "batch",                      // 一個小小的種類標籤(batch | single | …)
      "description": "…",                  // launcher 卡片上的一行
      "hint": "Drop files into uploads/.", // 一行的 inputs 提示
      "phases": [                          // 唯讀的進度骨架(手冊 §12)
        { "id": "classify", "title": "Classify" },
        { "id": "commit", "title": "Commit" }
      ]
    }
  ]
}
```

你 `run.py` 發出的每一個 `phase="…"` 字面值都應該出現在 `phases` 裡。scaffold 會幫你讓它們
保持同步;`check` 會在它們 drift 時警告。

## Recipe 範例集

scaffold 的三種起始形狀——開箱即 `check`-乾淨:

| Recipe | 形狀 | 跑到 |
| --- | --- | --- |
| `minimal` | 一個 `agent_write_step` | `done` |
| `review-commit` | produce → `human_gate` → deterministic commit | `awaiting_human`(approve 後完成) |
| `batch` | `wf.map` 跑過 `uploads/*`,每個檔案一個 agent node | `done` |

讀生成出來的 `run.py`——它有註解,是看一個 block 落在情境中最快的方法。內建的
`apps/topic-hub/profiles/default/workflows/`(memory、collections、consolidate)是更完整的
真實範例。

## 疑難排解

- **它在開機時 crash。** 跑 `check`——它會點名檔案和修法。(開機也會 `exec` `run.py`,所以
  它還會額外抓到靜態 `check` 抓不到的 import / `NameError` 失敗——那些請讀 traceback。)
- **進度圖不對／某個 phase 永遠不亮。** 你程式碼裡的某個 `phase=` 字面值沒被宣告(或反過來)。
  `check` 會對前者警告。
- **改了 prompt 之後某個 step 不重跑。** 它應該要重跑才對——prompt 在 input-hash 裡。如果你改的是
  step 讀的某個 *artifact*,那也會讓它重跑。要強制的話,刪掉 `step_*` artifact 或傳 `cache=False`。
