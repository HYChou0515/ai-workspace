# 擴充平台：Tools / Skills / Workflows

平台有三個讓 agent 變強的擴充面——**tool**（agent 能呼叫的動作）、**skill**（教
agent「某類任務怎麼做」的方法論）、**workflow**（把多個 step 串成可重跑的自動化）。
每個擴充面都有兩種作者：

- **dev 自建**——把原始碼／資料 **commit 進 repo**。它會出貨給那個 app／profile 的**所有人**,
  是 trusted code，走完整的 prebuild／重啟／CI／100% coverage gate。
- **user 自建**——在**執行期跟 AI 一起共創**，存成單一 workspace 裡的 **FileStore 資料**,
  live 讀取、可下載／匯入、可由 dev **升格**成內建。因為要在受信任的 API 邊界內保持安全,
  它是**受限**的。

這篇是把三個面向 × 兩種作者排成同一張表的**總覽**;每個面向的細節文件在各段末尾連出去。

## 一眼看懂：誰能建什麼

| 擴充面 | dev 自建 | user 自建（執行期 + AI 共創） |
|---|---|---|
| **Tool** | ✅ Python tool-package（`sample-tools/`） | ❌ **無**——安全考量,見下 |
| **Skill** | ✅ `sample-skills/` + `SHARED_SKILLS` 註冊 | ✅ `author-skill` + `save_skill` → `.skill/`（#298） |
| **Workflow** | ✅ Python `run.py`（圖靈完備） | ✅ `workflow.json` **降階 DSL**（#323）——**最難的一塊** |

三個 user 自建路徑刻意共用同一套模型（照搬 #298 的 skill 流程）:**跟 AI 共創 → 存進
workspace 的點開頭資料夾 → 側邊面板列出／下載／匯入 → dev 把它 commit 進 profile 就升格成
內建**。差別只在**執行風險**:skill 是被動 markdown（零風險,放手讓使用者寫）;workflow
會**執行**,所以 user 端被降階成一個受控的 JSON DSL;tool 需要跑任意 Python + 持有 credential,
所以**沒有** user 自建路徑。

---

## Tool（只有 dev 自建）

一個 tool 是 agent 能呼叫的**動作**。實作是一個**自成一格的 Python package**,跑在 sandbox
裡(不是 host app 進程),透過一個固定的 argv 契約被呼叫。

### 檔案佈局

```
sample-tools/<name>/
  pyproject.toml            # package 定義 + 依賴 + ruff TID252（禁相對 import）
  uv.lock                   # 凍結依賴（prebuild 用 --frozen，可重現）
  src/<pkg>/
    cli.py                  # 三段 dispatcher（entry point）— iterate COMMANDS
    core.py                 # 共用邏輯
    commands/               # 多 command package 才需要
      __init__.py           # COMMANDS dict ←「這個 package 有哪些 command」就在這
      summarise.py          # 一個 command = Args + DESCRIPTION + run()
      plot.py
  tests/
```

現有範例:`sample-tools/{data-fetch, csv-column-summary, sci-plot, rca-tools}`,外加
`python-stack`(一個沒有 command 的 venv carrier,workspace 內建 `python` 就靠它)。

### 三段 launcher 契約

每個 package 的 launcher 服從三段 argv 契約(backend → sandbox 零侵入,只傳三個字串):

```bash
$ ./launch                          # 零參 → 列出所有 command（JSON array）
[ {"name": "summarise", "description": "..."}, {"name": "plot", "description": "..."} ]

$ ./launch summarise                # 一參 → 該 command 的 metadata + JSON schema
{ "name": "summarise", "description": "...", "params_json_schema": { ... } }

$ ./launch summarise '{"csv":"x.csv"}'   # 兩參 → 執行；stdout / stderr / exit_code 回傳
```

作者寫一個 command 只要三樣東西——我們**不強制 decorator 或 framework**:

```python
# sample-tools/csv-column-summary/src/csv_column_summary/commands/summarise.py
from pydantic import BaseModel, Field

class Args(BaseModel):                              # 1. LLM 看到的參數 schema（自我描述）
    csv: str = Field(description="Path to the CSV file in the workspace.")

DESCRIPTION = "Summarise each column of a CSV ..."  # 2. LLM 看到的一行說明

def run(args: Args) -> None:                        # 3. 拿驗證過的 args 執行
    ...
    print(json.dumps({...}))                        # stdout = 給 agent 的 JSON；stderr = 進度
```

`cli.py` 把 command 湊成一個 `COMMANDS` dict 再自寫 `main()`(範例見
`data-fetch/src/data_fetch/cli.py`);嫌煩就用 framework 的
`workspace_app.tooling.dispatcher`(decorator 版,opt-in)。

> **絕對 import only。** tool package 的程式碼會在 prebuild 時被**複製／搬遷**,相對 import 一
> 搬就爆。ruff `TID252` + `ban-relative-imports = "all"` 會擋下來。

### 一個 package 有哪些 command？（沒有宣告檔）

**沒有任何外部設定檔宣告 command 清單**——它是 package **自我描述的程式碼**,由 launcher 的
**stage-1**(零參執行)吐出來:

- **多 command**——在 `commands/__init__.py` 的 **`COMMANDS` dict**。`cli.py` 的 dispatcher
  **iterate 這個 dict** 產生清單。新增一個 command = 這 dict 加一行 + 一個模組
  (`Args` + `DESCRIPTION` + `run`),不改別處。
  ```python
  # sample-tools/csv-column-summary/src/csv_column_summary/commands/__init__.py
  from csv_column_summary.commands import plot, summarise
  COMMANDS = {"summarise": summarise, "plot": plot}   # ← 這就是 command 的來源
  # cli.py: print(json.dumps([{"name": n, "description": m.DESCRIPTION} for n, m in COMMANDS.items()]))
  ```
- **單 command**——連 dict 都不必,直接在 `main()` 的 stage-1 寫死
  `[{"name": "data-fetch", ...}]`,也沒有 `commands/` 資料夾(範例:`data-fetch`)。

系統怎麼「知道」:**prebuild** 跑一次零參 `./launch`,把這份清單**凍結**成
`.workspace-tools/<name>/commands.json`,再對每個 command 跑 `./launch <cmd>` 把 schema 凍結成
`schemas/<cmd>.json`。host 端 `tooling/registry.discover_packages` 開機時**只讀這些凍結檔**,
從不 introspect package。所以 command 清單住在 package 自己的 `cli.py`/`COMMANDS` 裡,
`commands.json` 只是 prebuild 產出的**快照**——改了 command 就要重跑 prebuild 才會生效。

### 用 decorator 版 dispatcher（省掉手寫 `main()`）

上面的 `cli.py` 是**手寫** dispatcher(讓契約攤在眼前)。嫌煩就用 framework 的
`workspace_app.tooling.dispatcher.Dispatcher`——**opt-in**,用 `@d.command(name, description)`
註冊,再從 console_script entry point 呼叫 `d.main()`,三段 argv 路由它全包了:

```python
# sample-tools/<name>/src/<pkg>/cli.py — decorator 版（多 command，各自不同 Args）
import json
from typing import Literal
from pydantic import BaseModel, Field
from workspace_app.tooling.dispatcher import Dispatcher

d = Dispatcher()

class SummariseArgs(BaseModel):
    csv: str = Field(description="Path to the CSV file in the workspace.")

@d.command("summarise", "Summarise each column of a CSV ...")   # ← 註冊即等於「新增 command」
def summarise(args: SummariseArgs) -> None:
    ...
    print(json.dumps({...}))

class PlotArgs(BaseModel):                                       # 不同 command → 不同 Args
    csv: str = Field(description="Path to the CSV file in the workspace.")
    column: str = Field(description="Column to plot.")
    kind: Literal["hist", "box", "line"] = Field("hist", description="Chart type.")
    out: str = Field("plot.png", description="Output image path.")

@d.command("plot", "Plot one column of a CSV as an image.")     # ← 第二個 command
def plot(args: PlotArgs) -> None:
    ...
    print(json.dumps({"out": args.out}))

def main() -> None:        # pyproject.toml [project.scripts] 指向這裡
    d.main()               # stage-1 列出 summarise + plot / stage-2 各自 schema / stage-3 執行
```

每個 `@d.command` 各自綁一個 Args model,`d.main()` 就能對 `./launch summarise` 與
`./launch plot` 回不同的 schema——兩個 command 共用同一個 venv(依賴裝一次)。

要點:

- command 清單改由 **decorator 註冊**(取代手寫的 `COMMANDS` dict)——一樣是 package 自我描述的
  程式碼,stage-1 依名稱排序輸出,prebuild 凍結成 `commands.json` 的流程不變。
- handler **恰好一個參數**,annotation 必須是 **pydantic `BaseModel` 子類**;Args model 就從這個
  annotation 抽出來(單一真相來源:同時驅動 LLM 看的 JSON schema **與** 執行期驗證)。違反(參數
  數不對、annotation 不是 BaseModel)在**註冊時**就 `TypeError`——fail-loud,不會拖到執行期。
- Dispatcher 本身**零 domain 邏輯**、除 pydantic 外零依賴;不想用照樣手寫 `main()`,framework 不挑。

### 註冊 + prebuild + 授權

1. **註冊來源**——在 `src/workspace_app/tooling/packages.py` 的 `PACKAGES` dict 加一行
   `"<name>": SOURCE_DIR / "<name>"`。
2. **Prebuild**——`uv run python scripts/prebuild_tools.py`。它為每個 package 建一個 relocatable
   venv + portable python + `launch`,並把 schema 固化成檔,產物落在
   `.workspace-tools/<name>/`(`commands.json` + `schemas/<cmd>.json` + `launch` + `python/` +
   `.venv/`)。以**內容 hash** 判斷是否 skip,改了原始碼要重跑。(`.workspace-tools-uvrun/`
   是給開發用的輕量 symlink 版。)
3. **授權**——在某個 app 的 `app.json` `agent.tools` 陣列列出它。用 colon 語法細選:
   `"csv-column-summary"` 收全部 command,`"csv-column-summary:plot"` 只收 `plot`。
4. **重啟** app——開機時 `tooling/registry.discover_packages` 掃 `.workspace-tools/` 建
   `PackageInfo`,`build_function_tools` 依 `allowed` 展開成 `FunctionTool`(扁平 command name
   撞名會在開機 raise)。tool 的 name/description/JSON schema 也會被
   `agent/tool_prompt.format_tools_for_prompt` render 進 system prompt 末段,免得小模型把
   function tool 當成 PATH 上的 shell binary。

`app.json` 是**上限**;profile 的 `_profile.json` 可再收窄成子集;per-item 的 `tool_prefs`
再做三態覆寫(#322 的三層 resolve,見 `apps/catalog.py`)。

### 為什麼沒有 user 自建 tool

新增 tool 要跑**任意 Python** 並可能持有 credential——把它開放給執行期使用者不安全,所以這是
**deploy-time 的 dev 動作**(plan §B.9 明列為非目標)。使用者需要臨時計算時,走 **skill 的
`scripts/`**:它們跑在 workspace 內建的 python-stack(pandas / numpy / scipy / matplotlib),但
**裝不了新依賴**。當一段 script 穩定、需要自訂依賴、或值得被驗證後重用,那就是 **dev 把它
升格成 tool-package** 的時機。

**細節**:[`subsystems/tooling-and-sandbox-host.md`](subsystems/tooling-and-sandbox-host.md)
(子系統參考)、[`plan-skills-and-tools.md`](plan-skills-and-tools.md) §B(設計與決策)。

---

## Skill（dev + user，格式相同）

一個 skill 是一份簡短、可重用的**方法論**指令檔——「某一類任務該怎麼做」——agent 會用
`read_skill(name)` 按需載入(progressive disclosure),不是一開始就塞進 system prompt。dev 端
與 user 端**用同一個 `SKILL.md` 格式**,只是註冊與生命週期不同。

### `SKILL.md` 格式（兩端共用）

```
<name>/
  SKILL.md           # frontmatter（name + description）+ 方法論本體
  references/        # 選用 — 內文指到時 agent 用 read_file 讀
  scripts/           # 選用 — agent 透過 exec 在 python-stack 上跑
```

```yaml
---
name: triage-reflow
description: 分流 reflow 缺陷。當用戶說「reflow」/「焊接不良」或開了空白工單時使用。
---

# 方法論本體（markdown）
```

body 硬上限 `SKILL_BODY_CAP = 50_000` 字元(兩端都套)。核心載入邏輯在
`src/workspace_app/apps/skills.py`。

### dev 自建

1. 把 skill 放到 `sample-skills/<name>/`。
2. 在 `src/workspace_app/apps/shared_skills.py` 的 `SHARED_SKILLS` dict 註冊它。
3. 在某個 app 的 `app.json` `agent.skills` 列出這個名字(並在 `agent.tools` 授予
   `save_skill`,若要同時開放 user 共創)。

另有兩種內建位置:烤進 profile 的
`src/workspace_app/apps/<slug>/profiles/<profile>/.skill/<name>/`(隨每個新 workspace 內附,唯讀),
以及 repo 根的 `skills-lock.json`(把 skill 名對映到遠端 GitHub 來源)。`read_skill` 的解析順序是
**workspace(user) → shared(app.json) → profile**,前者 shadow 後者。

### user 自建（#298）

在任何 workspace app 裡跟助理說「幫我做一個 skill」,agent 會載入內建的 `author-skill`
meta-skill,走**界定→抽取→草擬→審閱→儲存→收尾**六步,最後呼叫 `save_skill(name, description,
body)` 把檔寫進 workspace FileStore 的 `.skill/<name>/SKILL.md`(你永遠不必手動編輯)。它:

- **每個 turn live 重讀**(不 cache),存進去下一個 turn 就 `read_skill('<name>')` 可用;
- 只活在**這個 workspace**,靠 chat header 的 **Skills 面板**(`SkillsModal`)**下載**成資料夾 zip
  或**匯入**別的 workspace(端點 `GET /a/{slug}/items/{item_id}/skills`);
- 它的 `references/` / `scripts/` 能用,是因為它們就住在 sandbox 掛載的那個 workspace 裡。

**為什麼 user 自建 skill 安全又容易**:skill 是**被動 markdown**,本身不執行任何東西(頂多
agent 讀了照做),零執行風險,所以放手讓使用者跟 AI 隨意寫。這正是它與 workflow 的分水嶺。

**細節**:[`skills-authoring.md`](skills-authoring.md)(user 共創流程)、
[`plan-skills-and-tools.md`](plan-skills-and-tools.md) §A(dev 機制與決策)。

---

## Workflow（dev + user，形狀差異巨大）

一個 workflow 把多個 step(agent turn、sandbox 指令、human gate、有 journal 的副作用)串成一條
可重跑、可 resume、以 filesystem 為 journal 的自動化。**這是 dev 端與 user 端差最多的擴充面**——
因為 workflow 會**執行**而且握有特權。

### dev 自建：Python `run.py`（圖靈完備）

一個 dev workflow 是**一個 `async def run(wf, inputs)`** 加上 profile `_profile.json` 裡的一小段
manifest:

```
apps/<app>/profiles/<profile>/
  _profile.json                  # 宣告 workflow（id、title、phases…）
  workflows/<id>/run.py          # async def run(wf, inputs) — orchestration
```

控制流就是普通 Python(`for` / `if` / `await`),跑在一套 step 函式庫之上:`agent_step` /
`agent_write_step`(有 gate 的 LLM turn)、`sandbox_node`(無 LLM 的指令)、`human_gate`
(produce→review→commit 接縫)、`wf.map`(平行 for-each)、`wf.ingest_to_collection` /
`wf.upsert_context_card`(有 journal、idempotent 的副作用)、以及自訂 gate。它是 **trusted
Python**——持有 turn engine、sandbox 生命週期、capability credential。用
`python -m workspace_app.workflow new/check` scaffold 與靜態檢查。

**細節**:[`workflows-authoring.md`](workflows-authoring.md)(block catalog + how-to)、
[`workflows.md`](workflows.md)(完整規格)。

### user 自建：`workflow.json` 降階 DSL（#323，最難的一塊）

**為什麼不能像 skill 一樣放手讓使用者寫?** 因為 skill 被動、workflow 會**執行**,而且
orchestration 握有特權 capability。把**使用者寫的 Python 跑進 trusted API 不安全**。所以使用者
**不能寫 code**——他們得到的是一個**降階、非圖靈完備的 JSON DSL**,由一個 trusted 的**通用
interpreter** `run()` 讀它、把每個 step 的欄位當參數 dispatch 到上面那批**既有的** primitive。
沒有任何使用者 *code* 跑進 API。

一份完整的 `workflow.json` 長這樣(把上傳檔分流進 collection):

```jsonc
{
  "schema": 1,
  "id": "ingest-logs",
  "title": "File uploads into collections",
  "phases": [
    { "id": "classify", "title": "Classify" },
    { "id": "review",   "title": "Review" },
    { "id": "commit",   "title": "Commit" }
  ],
  "config": { "collections": ["logs", "specs"] },
  "steps": [
    { "type": "map", "over": "uploads/*", "as": "file", "phase": "classify", "do": [
      { "type": "agent",
        "prompt": "Read {file}. Pick a collection from {config.collections}; write a digest. Output JSON {collection, digest, source}.",
        "out": "plan/{file}.json",
        "tools": ["read_file", "ask_knowledge_base"],
        "check": { "choice_in": { "path": "plan/{file}.json", "key": "collection", "allowed": "{config.collections}" } },
        "retries": 2 } ] },
    { "type": "gate", "phase": "review", "title": "Approve filing these?", "summary_from": "plan/*.json", "allow": ["approve", "reject"] },
    { "type": "map", "over": "plan/*.json", "as": "p", "phase": "commit", "do": [
      { "type": "capability", "call": "ingest_to_collection", "collection": "{p.collection}", "path": "{p.source}" } ] }
  ]
}
```

DSL 的天花板(刻意收窄):

- `steps` 是有序清單;step `type` ∈ `agent` / `sandbox` / `gate` / `capability` / `map`
  (`map` 是**唯一**的迴圈,**one-level、不可巢狀**)。
- `{x}` / `{x.field}` 是**唯讀字串代入**(非任意運算式、**no eval**);當 `{x.field}` 的 `x`
  指向一個 `.json` 檔時,它會讀檔取欄位——這正是「agent 記下決定 → 資料 → 派給 capability」的
  decision/action routing。
- `check` 是宣告式的 gate builder(`file_nonempty` / `choice_in` / `collection_has`);branch 用
  資料 routing。**沒有** revise-loop / branch 基本元素 / 巢狀 map——那些留給 dev 的 `run.py`。
- **安全不變式**:「使用者 workflow 能做的,**恰好等於它的作者親手能做的**」。capability 在
  **captured user 的 authz scope** 下跑;使用者的 `sandbox` step 是 **compute-only**(不給
  run-scoped credential),所以副作用永遠只走受控的 capability primitive。authoring 不產生任何新
  權限。

**共創與生命週期**(照搬 skill 模型):一個 `author-workflow` meta-skill 引導 AI 起草 DSL;一個
`save_workflow` tool 在寫入前**驗證**(schema、phase 一致、`tools` ⊆ profile 上限、capability 在
允許清單、`check` 格式)並把無效的 DSL **退回原因讓 AI 修**。它存進
`<workspace>/.workflows/<id>.json`(FileStore、item-local、live 讀,同名 **shadow** 掉 package
workflow,**不是** specstar resource)。FE 的 **Workflows 面板**(`WorkflowsModal`,掛在
AgentPanel)每列一個 **Run** + 下載／匯入;使用者按 Run,既有的 Run 端點／orchestrator／journal／
gate 機制原樣把它跑起來。

**一個 interpreter 服務兩層**:一個 *package* workflow 可以是 `run.py`(trusted Python)**或**
`workflow.json`(被 interpret)。所以 **promote = 把 json 複製進 profile**(免 transpile,正如
skill promote 就是複製 `SKILL.md`),再補一行 `_profile.json` 條目。

**細節**:[`workflows.md`](workflows.md) §22、[`plan-issue-323.md`](plan-issue-323.md)。

### dev `run.py` ↔ user `workflow.json` 對照

| 面向 | dev `run.py` | user `workflow.json` |
|---|---|---|
| 形式 | trusted Python | 宣告式 JSON DSL（資料） |
| 圖靈完備 | 是 | **否**（刻意） |
| 控制流 | 任意 `for` / `if` / `await`、巢狀、revise-loop、branch | 只有 `map`（one-level）+ 資料 routing |
| 副作用 | 任意 capability + 自訂 `sandbox_node` | 受控 capability allowlist；`sandbox` 為 compute-only |
| 存放 | `apps/…/profiles/<p>/workflows/<id>/run.py`（repo） | `<workspace>/.workflows/<id>.json`（FileStore） |
| 建立方式 | 手寫 + `workflow new/check` scaffold | 跟 AI 共創 → `save_workflow` |
| 驗證 | `check` CLI + 開機 `exec` | `save_workflow` 存檔時 validator |
| 誰能建 | dev（commit 進 repo） | 能存取該 item 的任何 user |
| 執行者 | 它自己的 `run()` | trusted 通用 interpreter dispatch 到同一批 primitive |

底層是**同一批 step primitive、同一套 filesystem-journal + input-hash 執行模型**;只是 authoring
的**表面**天差地遠——一邊是任意 Python,一邊是受限資料。

---

## 升格：把 user 自建的東西變成內建

三條 user 路徑都能被 dev **promote** 成 profile 內建,出貨給該 app 的所有人:

- **skill** → dev 把資料夾 commit 進 `apps/<slug>/profiles/<profile>/.skill/<name>/`。v1 只帶
  `SKILL.md` 本體,不帶掛載的 `references/` / `scripts/`。
- **workflow** → dev 把 `workflow.json` 複製進 profile 的 `workflows/<id>/` 並補一行
  `_profile.json` 條目(免 transpile)。
- **skill 的 script** → 當它需要自訂依賴或值得驗證後重用,dev 把它**升格成 tool-package**
  (見上面 Tool 段)。

---

## 原始碼與細節文件（快速索引）

| 面向 | dev 入口 | user 入口 | 細節文件 |
|---|---|---|---|
| Tool | `tooling/packages.py::PACKAGES`、`scripts/prebuild_tools.py`、`tooling/{prebuild,registry}.py`、`sample-tools/` | —（無） | `subsystems/tooling-and-sandbox-host.md`、`plan-skills-and-tools.md` §B |
| Skill | `sample-skills/`、`apps/shared_skills.py::SHARED_SKILLS`、`apps/skills.py` | `sample-skills/author-skill/`、`agent/tools.py::save_skill_impl`、`SkillsModal.tsx` | `skills-authoring.md`、`plan-skills-and-tools.md` §A |
| Workflow | `apps/…/workflows/<id>/run.py`、`workspace_app.workflow`、`workflow new/check` | `sample-skills/author-workflow/`、`save_workflow`、`workflow/dsl.py`、`WorkflowsModal.tsx` | `workflows-authoring.md`、`workflows.md`（§22 = user DSL）、`plan-issue-323.md` |
