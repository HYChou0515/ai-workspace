# Workflow 表達語言對齊計畫 — node contract、verify、與 Claude-Code-shaped 詞彙

> **追蹤 issue：** #499。
>
> **狀態：** 設計 / 計畫草案（以終為始）。本文件是 *目標* 也是 *驗收標準*：當實作的
> 可觀察行為符合這裡的規則時就算「完成」。它延伸並在若干點上 **修正**
> [`workflows.md`](workflows.md)（#100 spec）、[`workflow-reference-model.md`](workflow-reference-model.md)（#428）、
> [`workflows-authoring.md`](workflows-authoring.md)。尚未鎖定的決策集中在 §9，留給一場 `/grill-me`。
>
> **一句話：** 目前 workflow 有 **三種表達語言**（dev `run.py` 命令式、user `workflow.json`
> 宣告式、以及我們想對齊的 Claude Code 命令式），而 node 之間的 contract **未定型、未驗證**。
> 本計畫把三者收斂成 **一種 Claude-Code-shaped 的命令式詞彙**，並把 node 邊界升級成
> **typed + verified 的契約**。

---

## 0. 問題陳述（為什麼「幾乎難以使用」）

### 0.1 三種語言

| | 層 | 語言型態 | 誰寫 | 安全邊界 |
|---|---|---|---|---|
| **A** | `run.py`（dev） | 命令式 Python：`for`/`if`/`await` + step 函式庫 | 工程師 | trusted 後端 |
| **B** | `workflow.json`（#323/#428） | 宣告式 JSON DSL：steps list + `{steps.x.field}` 字串代入 | 非工程師 + AI | interpreter dispatch |
| **C** | Claude Code Workflow | 命令式 JS：`agent()`/`pipeline()`/`parallel()` + host 控制流 | AI（模型自己寫 code） | sandboxed interpreter |

`workflows.md` §3 早已否決宣告式 DSL，理由正是「控制流硬塞進宣告式 → 難以定義」。§22 為了
「讓非工程師 author」把它重開（#323）。結果就是 §3 預言的坑：**B 難用不是 bug，是這條路本身。**
而 A 其實已經很接近 C——都是命令式、控制流即 host language。**對齊方向該往 A/C 收斂，不是 B。**

量化證據：`dsl.py` 是 **1417 行**，整個 workflow 子系統最大的單一檔案（比 855 行的 `orchestrator.py`
還大）。它的一大半（`{steps.<name>.<field>}` 引用層、`switch`、`map` 單層限制、`gate revise` 的
forward-ref 特例與五條靜態約束、fan-in `.outputs` 對齊、`key_by` 撞值報錯…）**存在的唯一理由，是在
JSON 裡重造「變數 / if / for / 回邊」。** 這些在命令式碼裡直接消失。

### 0.2 更尖銳的問題：node 邊界未定型、未驗證（本計畫的核心）

觀察到的實際行為：**「我叫它 output 某個檔案，跑起來檔案內容竟然是 AI 的回覆。」**

在程式碼裡就是這兩行（`src/workspace_app/workflow/steps.py`）：

```python
# agent_write_step — 把模型「整段回覆原文」直接寫進檔案
result = {"out": out, "bytes": len(text)}
if out:
    await wf.write(out, text)          # steps.py:161 — text = 原始 turn 回覆，未清理
...
check=check or file_nonempty(out),     # steps.py:183 — 預設 gate 只檢查「非空」
```

於是模型回「Sure! Here's the report:\n\n# Report …」時，檔案**字面上**就以「Sure! Here's the
report:」開頭；``` 圍欄、口語結尾、拒答訊息也一併落地。而 `file_nonempty` 只要非空就過。下游 node
`glob` 到這個檔、讀進去，拿到的是**被污染的文字**——node A → node B 的邊界毫無型別、毫無語意保證。

這是「node 串接」問題的根：

> **一條 node 邊界目前的 contract 只有「有一個非空的檔」。** 不保證格式、不保證是乾淨的
> artifact、不保證符合下游期待。producer 吐什麼、consumer 就吞什麼。

Claude Code 對這件事的答案正是 `agent(prompt, {schema})`：模型被**強制**透過一個
StructuredOutput tool call 產出，結果在 **tool-call 層做 schema 驗證**（不是從 prose 硬 parse）、
不符就自動 retry、回傳一個 **typed 物件**。node 邊界變成「一個驗證過的 typed 值」，而不是
「一個希望它乾淨的檔」。**驗證即 verify。**

---

## 1. 對齊目標

1. **一種表達語言。** dev 與 user 兩層共用同一套 Claude-Code-shaped 命令式詞彙；差別只在
   **執行 context**（trusted process vs 受限 interpreter），不在語言。
2. **typed + verified 的 node 邊界。** agent node 的產出走 **結構化通道**、在邊界被驗證；
   一條 A→B 的邊是「producer 的輸出對 consumer 的期待做過驗證」，不是「有個非空檔」。
3. **保留我們比 Claude Code 強的東西**：`human_gate`、capability（decision/action + 冪等 +
   非冪等去重）、always-on 且可手改檔的 filesystem-journal resume、steer。這些是本平台
   （durable / headless / human-in-the-loop）的真正價值，不因語言對齊而動。

### 1.1 一個你的技術限制必須內建進設計（不可忽略）

Claude Code 的 `schema` 能無腦用，是因為它面對 **frontier hosted 模型**，structured-output /
tool-calling 很穩。**我們的 stack 含本地模型**（本地 Qwen via Ollama、#107 已記錄長 tool-arg
不可靠），這正是 `agent_write_step` 當初「內容 = plain-text 回覆、不走 tool 參數」的**刻意**理由。

結論：**不能**把「長 artifact 內文」也塞進 schema tool call。設計必須支援**兩條輸出通道**（§3），
verify 分別對兩條做。這是本計畫與 Claude Code 最大的一處**刻意分歧**，理由是模型能力差異。

---

## 2. Claude Code 做對了什麼（對照我們的痛點）

| C 的設計動作 | 我們對應的痛點 |
|---|---|
| 一種語言，控制流即 host（`for`/`if`/`while`） | `switch`、`map` 單層、`revise` 回邊 = 在 JSON 重造控制流 → 全砍 |
| `agent(prompt,{schema})` 回傳**驗證過的 typed 物件** | `outputs`+`{steps.x.field}`+寫檔讀檔 = 重造「變數」 → 全砍 |
| `pipeline`（per-item 獨立 staging，無 barrier） vs `parallel`（barrier） | 我們兩層都缺 pipeline；`wf.map` 把 batch+barrier 混在一起 |
| `phase()`/`log()` 敘述 + `meta.phases` 靜態骨架 | 你 §12 已這樣做 → 戳破「非宣告式就畫不出圖」的顧慮 |
| sandboxed interpreter：拔 `Date.now`/`Math.random`、只給注入的 primitive | 安全 + determinism **不靠宣告化**就能拿到（§8） |
| `budget`（token 縮放）、`workflow()` 巢狀、`resumeFromRunId`（prefix cache） | 我們缺 budget/巢狀；resume 我們更強（可手改檔） |

**核心洞察：** 你走宣告式想換三樣東西——靜態畫得出圖、step 身分可重現、安全——但三樣都能用
「受限 interpreter 跑命令式」拿到（§8 詳述），這正是 C 的做法。**宣告式 DSL 買不到任何受限
interpreter 買不到的東西，卻付出整份 reference-model 的複雜度 + 你親身體會的『難用』。**

---

## 3. Node 輸出的兩條通道與 verify 模型（本計畫的心臟）

一個 agent node 只會產出兩種東西，各走一條通道、各有自己的 verify：

### 3.1 通道 D — 結構化決策資料（短、走 schema tool call）

「這個檔屬於哪個 collection」「分數多少」「類型是 latency/errors/other」這類**短**欄位。

- 走 `schema`：模型透過 StructuredOutput 產出、**tool-call 層驗證**、不符自動 retry、回傳 typed 物件。
- 因為 **args 短**，本地模型也穩（不觸 #107 的長 tool-arg 雷）。
- 這條通道**取代** `outputs` + `{steps.x.field}` 整套引用層：下游直接拿變數（命令式）或
  `{steps.x.field}`（DSL 若保留）讀 typed 值，不再寫 json 檔再 glob 回來。

```python
# 對齊後（dev 層示意）
plan = await agent(wf, f"Read {f}. Pick a collection from {allowed}, write a digest.",
                   phase="classify", schema=PLAN_SCHEMA, tools=["read_file"], retries=2)
# plan 是 {collection, digest, source} 的驗證過物件 —— 直接用，不落檔、不字串代入
```

### 3.2 通道 P — Prose artifact 內文（長、走 plain-text 回覆 → 檔案）

`report.md`、摘要文件這類**長**內文，維持「模型把內文當回覆產出、step 寫檔」以避長 tool-arg 不可靠。
**但 verify 必須遠強於 `file_nonempty`**，且寫檔前要**清理**。分三層（延續 §6「deterministic
primary、LLM-judge auxiliary」）：

1. **寫檔前 sanitize（必做、便宜、高價值）。** 落檔前自動剝掉常見污染：開頭的口語前言
   （`^(Sure|Certainly|Here('|')s|I('|')ve|Below is)\b…`）、包住全文的 ``` 圍欄、結尾寒暄。
   讓 artifact **就算模型加了廢話也乾淨**。這一步直接消滅「檔案內容是 AI 回覆」的觀察現象。
2. **結構驗證（deterministic gate，取代 file_nonempty 當預設）。** 依宣告的 artifact 型別驗：
   `markdown`（可解析、含要求的 heading/section、長度非 trivial）、`json`/`csv`/`yaml`（能 parse
   成宣告 shape）、`code`（語法可 parse）。**預設 gate 從 `file_nonempty` 換成 `artifact_valid(out, kind)`。**
3. **LLM-judge（escape hatch，只給語意）。** 「這個檔是否**只**含被要求的 artifact、無對話包裝？
   是否滿足 `<intent>`？」保留給機械驗不到的語意目標——照 §6，這是輔助不是主力。

### 3.3 typed edge — 把「A→B 的邊」本身變成契約

node 串接的根治：**consumer 宣告它要什麼，engine 在 producer 產出後、consumer 執行前，於邊界驗證。**

- 結構化資料（通道 D）：consumer 消費 `plan.collection` 時，`plan` 的 schema 靜態 + 執行期都保證
  欄位在。**邊界不需額外驗**——schema 就是契約。
- prose artifact（通道 P）：consumer 若宣告 `expects="markdown"`（或一個 `reads` 的期待型別），
  engine 對 producer 的 `out` 跑 `artifact_valid`；不符 → producer 的 gate fail → 帶原因 retry。
- **原則：未通過 verify 的 artifact 絕不流進下游 node。** verify 是**邊上的 gate**，不是事後。

> 對照現況：現在邊界 contract = 「有個非空檔」。對齊後 = 「通道 D 是 schema-typed 值；通道 P 是
> 清理過且對宣告型別驗證過的 artifact」。這就是你要的「比較好的 verify」。

---

## 4. Tier 1 — 語言對齊（低風險，先做；不吃 interpreter 風險）

把 A、B 兩層的詞彙 reshape 到 C 的形狀。這是**純命名 + 少數新原語 + verify 升級**，不動安全模型。
它直接解掉你「難用」最大來源（傳值層）＋「node 邊界弱」最大來源（file_nonempty）。

### 4.1 統一詞彙表 v1（最終 signature 目標）

以 dev（Python）層為準；user 層在 Tier 2 用同一組詞彙（見 §5）。

| 概念 | Claude Code | **對齊後（本平台）** | 取代現況 |
|---|---|---|---|
| agent turn（結構化） | `agent(p,{schema})`→obj | `await agent(wf, prompt, *, phase, schema, tools, reads, retries, cache, name, key)` → 驗證過 dict | `agent_step`+`outputs`+`{steps.x.f}` |
| agent turn（prose 檔） | （用 schema 一欄） | `await agent_write(wf, prompt, *, phase, out, kind="markdown", sanitize=True, verify=None, tools, reads, retries)` → 清理後寫檔 | `agent_write_step`+`file_nonempty` |
| deterministic node | （JS 碼本身） | `await sandbox(wf, run, *, phase, schema=None, check=None, reads, cache)` | `sandbox_node`（更名對齊） |
| fan-out（獨立 staging） | `pipeline(items,s1,s2)` | `await pipeline(wf, items, stage1, stage2, ...)` → list（per-item skip → None） | **新增**（現無） |
| fan-out（barrier） | `parallel(thunks)` | `await parallel(wf, [thunk,...])` → list（skip+collect → None） | `asyncio.gather`/部分 `wf.map` |
| 進度 | `phase()`/`log()` | `wf.phase(title)` / `wf.log(msg)` | `phase=` kwarg（保留）＋新增 `log` |
| 分支 | `if` | `if`（host） | DSL `switch` → 命令式 `if` |
| 迴圈 | `for`/`pipeline` | `for`/`pipeline`（host） | DSL `map` → `for`/`pipeline` |
| 巢狀 workflow | `workflow(name,args)` | `await run_workflow(wf, ref, args)` | **新增** |
| 預算縮放 | `budget` | `wf.budget.total/spent()/remaining()` | **新增** |
| human gate | （無） | `await human_gate(wf, *, phase, title, summary, allow)` → Decision | 不變（保留） |
| 有界回饋 loop | （無） | `human_gate` 回傳 `revise` + host `while` | DSL `revise` 五約束 → host 迴圈 |
| capability | （無） | `wf.ingest_to_collection / upsert_context_card / create_entity / …` | 不變（保留） |
| resume/cache | `resumeFromRunId` | filesystem-journal + input-hash（always-on、可手改檔） | 不變（比 C 強） |

重點變化：

- **`agent` 回傳 schema 驗證過的資料** → 通道 D。這一步同時砍掉 `outputs`/`{steps.x.field}` 傳值層。
- **`agent_write` 預設 `sanitize=True` + `verify=artifact_valid(kind)`** → 通道 P。落檔前清理、
  gate 從 `file_nonempty` 換成型別驗證。**這是「better verify」的落點。**
- **新增 `pipeline`**：batch 的正確預設（per-item 獨立走完 stages，wall-clock = 最慢單鏈）。
  `wf.map` 的 skip+collect 語意被 `pipeline`/`parallel` 的 per-item→None 自然涵蓋。

### 4.2 Tier 1 的 mandatory-gate 語意（延續 §5.1）

「每個 agent node 都必須有 gate」維持不變，但 gate 的來源對齊後更自然：**`schema`（通道 D）或
`verify`（通道 P）本身就是 gate**；兩者皆無時才需顯式 `check`。「無驗證的 agent node」仍是 schema error。

---

## 5. Tier 2 — 結構收斂（把 user 層從宣告式 JSON 換成受限命令式 script）

語言一致後才做得起來的大動作：**`workflow.json` → 一支跑在 sandboxed interpreter 裡的命令式 script**，
詞彙與 §4 完全相同。差別只在執行 context。這一步讓 `dsl.py` 裡「重造變數/if/for/回邊」的機制整批消失。

對齊後的 user workflow（示意，取代現行 61 行 file-uploads.json）：

```js
export const meta = { id: "file-uploads", title: "...", config: { collections: ["notes"] },
                      phases: [{id:"classify"},{id:"review"},{id:"commit"}] }

const files = await glob(inputs.files)                       // 通道皆為注入 primitive
const plans = await pipeline(files, f =>
  agent(`Read ${f}. Pick a collection from ${config.collections}, write a digest.`,
        { phase: "classify", schema: PLAN_SCHEMA, tools: ["read_file"], retries: 2 }))

const d = await human_gate({ phase: "review", title: "File these?", summary: plans,
                             allow: ["approve", "reject"] })
if (d.choice === "reject") return { status: "rejected" }

for (const p of plans)                                       // 迴圈 = host for
  await ingest_to_collection(p.collection, p.source, { phase: "commit" })
```

- **安全 = 能力注入。** script 只拿得到注入的 primitive（`agent`/`pipeline`/`human_gate`/
  capability…），碰不到 credential、fs、網路——與現行 interpreter「dispatch 到 primitive」**同一個
  安全模型**，只是控制流變成真的碼。
- **determinism = 拔掉不確定來源。** interpreter 移除 `Date.now`/`Math.random`（如 C），step 身分
  由作者提供的 `name`/`key`（如你 `run.py` 已信任 dev）+ args-hash 決定 → journal skip 不變。
- **靜態驗證 → 受限 runtime + 輕量 AST lint。** 現行存檔時的靜態驗證（`tools ⊆ ceiling`、capability
  允許清單）改為：runtime 邊界（沒注入就呼叫不到）+ 存檔時 AST lint（禁 import、禁被拔的 global）。
  比信任 JSON schema 更硬。

**§5 消滅的機制（全部因為有了 host language）：** `{steps.<name>.<field>}` 引用層、`switch`、
`map` 單層/不准巢狀、`gate revise` forward-ref 特例與五條靜態約束、fan-in `.outputs` 對齊、
`key_by` 撞值報錯、`on` 穩定性約束、default 窮盡檢查……即 `workflow-reference-model.md` 幾乎整份。

---

## 6. 保留（我們比 Claude Code 強、不動）

- `human_gate` / `awaiting_human` / decisions endpoint — durable 人閘。
- capability（decision/action 拆分 + 冪等 + #435 非冪等去重）— 可靠副作用只走這條。
- filesystem-journal + input-hash，**always-on 且可手改檔**的 resume/rewind（§9）。
- steer（#288）— 活躍窗外的自由文字 overlay。

這些在對齊後**不需要改語法**——它們是注入的 primitive，命令式碼直接呼叫。

---

## 7. Phasing（flat integer phases）

> 每個 phase 走 `/tdd`（red-green-refactor），每完成一 phase commit 一次。Tier 1（P1–P5）**不吃**
> interpreter 風險，可先全做完；Tier 2（P7+）**gated on** P6 的可行性 spike 結論。

| Phase | 內容 | 相依 |
|---|---|---|
| **P1** | **通道 P verify 升級**：`agent_write` 加 `sanitize`（剝前言/圍欄）+ `artifact_valid(kind)` 取代 `file_nonempty` 當預設 gate；`kind ∈ {markdown,json,csv,yaml,code,text}`。直接治「檔案內容是 AI 回覆」。 | — |
| **P2** | **通道 D**：`agent(..., schema=)` 回傳 tool-call 層驗證過的 typed 物件（不符 retry）；本地模型走短 args。 | — |
| **P3** | **`pipeline` / `parallel` 原語**：per-item 獨立 staging + barrier 兩式；`wf.map` 以 `pipeline` 重寫（skip+collect → per-item None）。 | — |
| **P4** | **`typed edge`**：consumer 宣告 `expects`/期待型別 → engine 在邊界對 producer `out` 跑 `artifact_valid`；不符 → producer gate fail + retry。 | P1,P2 |
| **P5** | **詞彙對齊 + 缺項**：`phase`/`log`/`budget`/`run_workflow`；dev 層 rename（保留舊名 alias 一版）。改寫 topic-hub 範例 workflow 到新詞彙。 | P2,P3 |
| **P6** | **受限 interpreter 可行性 spike**（決策，非產出）：Python 受限直譯 vs 嵌 JS/Lua VM，比 sandbox 強度、DX、與 dev 層語言一致性。輸出一份選型建議 + PoC。 | P5 |
| **P7** | **Tier 2：user script 引擎**：依 P6 選型實作 sandboxed interpreter，注入 §4 primitive；AST lint；`author-workflow` 改產 script、`save_workflow` 改驗 script。 | P6 |
| **P8** | **退役宣告式 DSL**：`dsl.py` 收斂（移除引用層/switch/map 限制/revise 五約束）；改寫唯一的 file-uploads.json → script；`schema` 維持內部契約。 | P7 |

---

## 8. 三個「宣告式想換的東西」為何受限 interpreter 都給得到

| 你想要 | 宣告式 DSL 的給法 | 受限 interpreter 的給法（= Claude Code） |
|---|---|---|
| 靜態畫得出圖 | 整個 body 宣告化 | `meta.phases` 靜態骨架 + `phase()` live（你 §12 已這樣） |
| step 身分可重現 | `map over` 強制迭代集合來自穩定引用 | 拔 `Date.now`/`random` + 作者給穩定 `name`/`key`（你 `run.py` 已這樣信 dev） |
| 安全（不碰 credential） | interpreter dispatch 到 capability | script 只能呼叫注入的 primitive（同一安全模型） |

---

## 9. 未鎖定決策（留給 `/grill-me`）

1. **受限 interpreter 選型（P6 的核心）。** (a) 受限 Python（`RestrictedPython` 一類；與 dev 層
   **同一語言**是最大誘因，但沙箱逃逸史需嚴審）；(b) 嵌小型 JS/Lua VM（沙箱乾淨，但引入**第二語言**、
   與 dev `run.py` 分裂）。**取捨：語言一致 vs 沙箱強度。**
2. **prose 通道要不要乾脆也用 schema 一欄？** 若本地模型長 tool-arg 之後夠穩（#107 有進展），通道 P
   可併入通道 D（`agent(schema={report:"str"})` 再寫 `result.report`），輸出通道就**只剩一條**。
   現階段先維持兩條（§1.1）；此決策 gated on #107。
3. **`agent_write` sanitize 的積極度。** 只剝「明顯」前言/圍欄（保守、低誤傷）還是更積極（可能剪掉
   正文首段）？預設保守，`kind` 特定規則可調。
4. **typed edge 的宣告位置。** 期待型別宣告在 **consumer**（`expects=`）還是 **producer**（`out` 的
   `kind=`）？或兩者都有時取交集驗證？
5. **Tier 2 要不要做，還是 Tier 1 就夠。** 若 Tier 1 已把「難用」與「node 邊界弱」解掉大半，Tier 2
   的 ROI（砍 `dsl.py` 複雜度）是否值得 interpreter 的工程與安全成本？**這是最上層的 go/no-go。**

---

## 10. 非目標 / 延後

- 不改 `human_gate` / capability / journal / steer 的**語意**（只改它們被呼叫的**語言**）。
- 不動 release 版號、不碰 `config.yaml`。
- 節點層級的視覺化拖拉 authoring（維持 phase 層級觀察）。
- 對外 webhook、真 SSO authz（延續 `workflows.md` §21 非目標）。
- per-element 真平行 sub-handle 屬**引擎能力**（#429 P5），與本「語言」計畫正交。

---

## 附錄 A — 現況痛點 → 對齊後對照（一頁速查）

| 現況 | 對齊後 |
|---|---|
| `agent_write_step` 把原始回覆倒進檔案 | `agent_write` 落檔前 sanitize，只寫乾淨 artifact |
| 預設 gate `file_nonempty`（非空就過） | 預設 gate `artifact_valid(out, kind)`（驗形狀） |
| 結構化決策走 `outputs` + 寫 json + `{steps.x.f}` 讀回 | `agent(schema=)` 回傳 typed 物件，直接當變數用 |
| node 邊界 contract = 「有個非空檔」 | 通道 D = schema-typed 值；通道 P = 驗證過的 artifact |
| `switch` / `map` 單層 / `revise` 五約束 | host `if` / `for`/`pipeline` / `while` |
| 三種表達語言（run.py / json DSL / 想對齊 CC） | 一種命令式詞彙，跨 dev 與 user 兩層 |
| `dsl.py` 1417 行重造控制流 | 受限 interpreter + 注入 primitive |
