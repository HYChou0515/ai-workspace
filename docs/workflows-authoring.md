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

### Human gate

```python
decision = await human_gate(wf, *, phase, title, summary="",
                            allow=("approve", "reject")) -> Decision  # .choice, .input
```
為人停下來。第一次抵達時 run 會以 `awaiting_human` 暫停;一旦記錄了一個決定,重跑會 replay
已完成的 step、抵達這個 gate、找到那個決定,然後繼續。`summary` 是給人審閱的內容(一個字串或
任何可 JSON 化的值)。這就是標準的 **produce → review → commit** 接縫。

### Capabilities(`wf` 上的 deterministic 副作用)

這些是可靠、有 journal、idempotent 的副作用——decision/action 拆分裡的 *action* 那一半。
agent 從不持有這些;是你的 `run()` 在 gate 之後呼叫它們。

```python
await wf.ingest_to_collection(collection, path, *, phase="ingest", cache=True) -> doc_id
await wf.upsert_context_card(collection, keys, *, title="", body="",
                             phase="commit", cache=True) -> card_id
await wf.find_overwrite_card(collection, keys, *, title="") -> {...} | None  # 唯讀
await wf.convert(src, dest, *, phase="convert", cache=True) -> (out_path, kind)
await wf.create_entity(type_name, args, *, name, on_duplicate="update",
                       phase="commit", key="", cache=True) -> number
await wf.send_notification(recipient, topic, *, name, title="", body="",
                          phase="notify", cache=True) -> {sent, action, notification_id}
```

`create_entity` / `send_notification` 是 **non-idempotent capabilities**(#435):它們的
*action* 對外界有一次性副作用(建一筆實體、發一則通知),所以光靠「同 args 跳過」的 journal
去重不夠——一個 revise 改了欄位、或跨 run 手動重跑,args 會變但那個「真實世界的東西」還是同
一個。詳見下面的〈非冪等 capability 去重〉。`name` 是**必填**:它是去重身分(不是 args
指紋),同一個 `name` 站點在 revise／replay 時對應到同一個實體。

`wf.convert`(#324)把一份上傳**先轉成文字、再 file 進 collection**——只存轉好的 artifact,
絕不存原始 binary(topic-hub 的 →collections 在 `input.json` 開了 `convert` 時用它)。它跑的是
跟 index 同一套 KB parser(含 VLM describer),但不 chunk／不 embed,也不碰 `SourceDoc`。
`kind` 有三種:`markdown`(parser 把 binary／結構化檔轉成 markdown,落在 `.md` 名下,例如
`deck.pptx` → `deck.pptx.md`)、`passthrough`(本來就是純文字／程式碼,維持原副檔名)、`none`
(沒有 parser 讀得懂的 binary——此時 `out_path` 是 `None`,呼叫端**跳過**它,絕不把原始 bytes
落地)。回傳的 `out_path` 就是你接著要 file 的那條 workspace 路徑。

### 非冪等 capability 去重(#435)

一個 idempotent capability(`ingest` / `upsert_card`)重跑不會有事——它的 action 是
「upsert」,再做一次就覆蓋掉。但 `create_entity` / `send_notification` 的 action **本質是一次
性的**:再做一次就是「多一筆實體」「多一則通知」。這類 capability 走一層共用的**非冪等外殼**
(`workflow/nonidempotent.py`),把每次呼叫拆成兩筆各自 journal 的 `run_step`:

```
step_<name>/<key>.decide.json   ← 一個 Verdict(kind = new | duplicate | token)
step_<name>/<key>.json          ← 發布出去的 Result(number / notification_id …)
```

`decide` 先判斷「這個東西存不存在」,`act` 再依 verdict 分派(建新／合併／跳過)。因為 act 的
input hash 把 decide 的 verdict 也算進去,§9 的 hash-chaining 就白送你**三態重跑**
(verdict 沒變 → 跳過;verdict 變了 → 重判)——不需要另外寫一套兩段落盤。

**去重靠的是機制目錄,不是「策略層」**。policy 定義在**單一 capability 介面**這層,不是一個通
用策略層:

- **M1 — 查既有的真實來源(store)。** 兩種味道:*deterministic fingerprint*(例如
  `send_notification` 用 `{recipient}:{topic}` 去查通知 ledger,同一個主題只發一次),或
  *AI-semantic*(`create_entity` 問模型:這筆新實體跟別的來源已經 file 的某筆是不是同一個真實
  東西)。AI 那條是 opt-in 的:owner 沒 wire 模型時退化成純 journal 自我去重。
- **M2 — idempotency token。** 呼叫綁一個調用 token(journal 身分),`create_new` 政策就是
  「M1 減掉跨來源比對」——同一 run 內 revise 靠 `created.json` 安全,跨 run 的 `create_new`
  在 #429 落地前由 `workflow check` 靜態擋掉。
- **M3 — self-ledger(deferred)。** 給真正 blind 的外部 channel(送出去無法回查)用;in-app
  通知不需要,因為那筆 store record **本身**就同時是「送出」與「ledger」(原子)。

三個關鍵不變式:

- **journal-first、AI-second 身分。** 先看 write-once `created.json`(deterministic 自我去
  重,擋 revise 雙建);只有跨來源時才動用 AI。
- **self / cross 分流的合併。** 同源(自己之前建的)= overlay;跨源(別人建的)= 非破壞的
  **圍欄覆寫**——只改自己 `name` 擁有的 `<!-- wf:<name> begin/end -->` 區塊 + 填空欄位,絕不
  動人手寫的標題／內文。圍欄每次**覆寫**(不是 append),所以重跑不會累積(决议5,靠構造冪等)。
- **decide-AI 只守可逆的 act(决議8)。** M1-AI 從構造上只會守「非破壞的 enrich」這種可逆動
  作,所以 fail-open 永遠安全:模型出錯／幻覺 → 當成 NEW,頂多多建一筆(可逆),絕不誤 merge 進
  一筆不存在的紀錄。

DSL(`workflow.json`)對 non-idempotent 步驟強制要 `name`;把 `on_duplicate` 設成
`create_new` 但 #429 還沒到,`workflow check` 會靜態報錯擋下。

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
```

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
