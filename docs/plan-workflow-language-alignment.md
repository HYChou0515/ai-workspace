# Workflow 可靠性計畫 — node contract、verify、authoring（維持 JSON DSL）

> **追蹤 issue：** #499。
>
> **狀態：** 設計 / 計畫（以終為始，經一整場 `/grill-me` 鎖定）。本文件是 *目標* 也是
> *驗收標準*：當可觀察行為符合這裡的規則時就算「完成」。被否決的替代方案就地記錄（§5），
> 免得重新爭論。它延伸並在若干點上 **修正** [`workflows.md`](workflows.md)（#100）、
> [`workflow-reference-model.md`](workflow-reference-model.md)（#428）、
> [`workflows-authoring.md`](workflows-authoring.md)。
>
> **一句話：** workflow 的真正痛是 **不可靠**——node 邊界未定型、未驗證，runner 在該停時
> 往下繼續。修法**不是**換語言（維持 JSON DSL），而是 **(1) 把 node 邊界升級成 typed +
> verified 契約**、**(2) 把 authoring 拆成「通用目的層 skill + 機器衍生的細節」讓 AI 產得出
> 合格 json 且知道邊界**。

---

## 0. 問題陳述

### 0.1 核心：node 邊界未定型、未驗證

觀察到的實際行為：**「叫它 output 某個檔案，跑起來檔案內容竟然是 AI 的回覆」**，而且
**「runner 總是無腦往下繼續」**。在程式碼裡就是兩個洞：

- `src/workspace_app/workflow/steps.py:161` — `agent_write_step` 把模型**整段回覆原文**直接
  `wf.write(out, text)`（含「Sure, here's…」、``` 圍欄、口語前言）。
- `steps.py:183` — 預設 gate `check or file_nonempty(out)`，**只檢查非空**、不驗形狀。
- `engine.py:110` — **`check=None` → `CheckResult(True)` 永遠通過**。所以 `sandbox_node` 沒給
  check 時，**連 exit code 非零（指令失敗）都算成功**繼續往下。

引擎其實**會停**（`engine.py:121`：check 真的失敗就 `raise StepFailed`、頂層 abort）。所以
「無腦往下繼續」的成因**不是引擎缺 halt，是 check 在該失敗時放水誤判成功**。→ 修法是**把每種
node 的預設契約變嚴、禁止「無 verify」的 node**，讓「沒驗證就算過」在構造上不存在。

> **一條 node 邊界目前的 contract 只有「有一個非空的檔」。** 不保證格式、不保證乾淨、不保證
> 符合下游期待。producer 吐什麼、consumer 就吞什麼——這是「node 串接」問題的根。

### 0.2 為何維持 JSON（記錄被否決的替代方案）

grill 過程認真評估過「把 user 層從宣告式 JSON 換成 sandboxed 命令式 script（對齊 Claude
Code）」。**否決**，理由：

1. **痛不在語言、在可靠性。** 上面的 bug 跟 authoring 語言無關，換語言不會修好它；而 verify
   修法（§2）跟語言無關，維持 JSON 照樣拿到。
2. **JSON 安全又已建好。** 它是純資料、no eval、攻擊面極小；`dsl.py` 雖大但已運作。
3. **命令式的代價太高且動到核心。** 未受信任使用者不能寫 `run.py`（RCE）；要跑命令式就得在
   sandbox 裡跑，而 `wf`（`WorkflowHandle`）是個 god-object——`handle.py:26-29` 直接 import
   `EntityOrigin/EntityWriteSink/FileStore/nonidempotent` 並自實作全部 capability，把它搬進
   sandbox 等於**逼 sandbox 裝下整個後端**（毀隔離、違反 §5.2）。要正確做就得把 `wf` 剖成
   capability API + 瘦 client，還得把 `agent` 這種**有狀態、串流進 chat、帶 tool** 的 turn 升成
   capability——而那會把 §10 的「串流 + 人類零成本接手」硬塞進 capability 邊界。**投報比不划算。**

**結論：維持 JSON DSL；力氣放在 verify（§2）與 authoring（§3）。** authoring 好不好寫的關鍵不是
「宣告 vs 命令式」，是「AI 有沒有一份不會 drift 的 grammar + 邊界說明」（§3）。

---

## 1. 決策總結（grill 鎖定）

| # | 決策 |
|---|---|
| D1 | 主因＝可靠性/verify（非 authoring 語言）；維持 JSON。 |
| D2 | **每種 node 都有嚴格預設 gate；「無 verify」直接禁掉**（封 `engine.py:110`）。 |
| D3 | 「正確」驗到 **L1 格式 + L2 契約（確定性、到滿）**；**L3 語意交 `human_gate`**，絕不強制 AI-judge。 |
| D4 | 契約 **producer 單一宣告、生產當下 verify**；typed edge = typed output；v1 不做雙端相容檢查。 |
| D5 | 一個 `agent` node **只有一種產出**：`outputs`（結構化決策）**XOR** `out`+`kind`（prose artifact）；兩者皆無＝schema error。 |
| D6 | **不對 LLM 輸出做 sanitize**（不剝前言/圍欄）；verify 抓到污染就 fail → **retry-with-feedback 逼模型產乾淨的**（源頭修，不事後 munge）。 |
| D7 | authoring：**(a) skill 收斂成通用目的層**（概念/golden rule，不 drift）＋ **(b) 機器衍生所有會 drift 的細節**（grammar from schema、per-app capability/tool 邊界注入）。 |
| D8 | **DROP Tier 2**（命令式 script、sandboxed interpreter、`wf` 拆解、agent-as-capability）。 |

---

## 2. Workstream 1 — Verify / node-contract（落在 JSON DSL）

### 2.1 一個 node 只有一種產出（D5）

`agent` step 必為以下**其一**，否則 `validate_def` 靜態報錯（封「無 verify」＝D2）：

- **通道 D — 結構化決策**（短）：宣告 `outputs`（schema）。模型透過結構化產出、**tool-call 層
  驗證**、不符 retry、耗盡 fail。下游用 `{steps.<name>.<field>}` 讀 typed 欄位。因 args 短，本地
  模型也穩（避 #107 長 tool-arg 雷）。
- **通道 P — prose artifact**（長）：宣告 `out` + `kind`。模型把內文當回覆產出、step 寫檔（維持
  「不走長 tool-arg」以避 #107）。

**禁 both**（v1 一 node 一種產出，最可靠、語意最乾淨）；**禁 neither**（無 verify）。

### 2.2 每種 node 的預設 gate（D2）

| node | 現況預設 | 新預設契約 |
|---|---|---|
| `agent`（通道 D） | — | schema 驗證即 gate（不符 retry，耗盡 fail-loud） |
| `agent`（通道 P） | `file_nonempty`（放水） | **`artifact_valid(out, kind)`** |
| `sandbox` | `check=None` → **永遠過** | **預設 `exit_code == 0`**（指令失敗就 fail） |
| 任何 node **完全沒 verify** | 靜默永遠過（`engine.py:110`） | **schema error——不可表達** |

### 2.3 三層「正確」與各自手段（D3）

| 層 | 驗什麼 | 手段 |
|---|---|---|
| **L1 格式** | 是合法 `<kind>`、非 trivial | `artifact_valid(kind)`，確定性。結構化 kind（json/csv/yaml）＝**parse 成該格式**，污染的「Sure, here's: {…}」parse 不過 → fail。 |
| **L2 契約** | 有下游要的結構（必要 heading、能 parse 成宣告 schema、最少段落） | 作者宣告 **`requires`**（producer 端），確定性。**prose 的真正強度來自這裡，強烈鼓勵宣告。** |
| **L3 語意** | 內容真的對/好 | **`human_gate`（人當 judge，produce→review→commit）**；只在無人在迴圈且作者願擔風險時 opt-in LLM-judge，**永不強制**。 |

**殘酷事實（寫死，防日後有人加「每 node AI 自動驗語意」）：** 不能用不可靠模型可靠地驗另一個
不可靠模型的語意——尤其兩邊同一顆本地模型（§6 自己就警告過）。L1/L2 抓「格式爛/污染/形狀不符」，
抓不到「格式漂亮但內容錯」；後者是 `human_gate` 的職責。

### 2.4 不 sanitize，改 retry-to-clean（D6）

**絕不**在寫檔前剝前言/圍欄（不偷改輸出、不冒 false-positive 剪掉正文的風險）。改為：verify
（L1 parse-for-structured-kind / L2 `requires`）**抓到污染就 fail**，把原因回灌 prompt（例如
「你的輸出夾了對話前言，只輸出 artifact 本身」）→ **模型重產乾淨的**。污染在**源頭**被修，不是
事後被 munge。搭配 prompt 慣例（「只輸出 artifact、無前言」）。

### 2.5 producer-declared、生產當下 verify（D4）

契約宣告在**產出的 step**（`kind`/`requires`），verify 在**生產當下**跑——失敗就 fail-loud 在
**出問題的那個 node**（比「下游讀檔失敗」好 debug）。consumer 消費一個「已驗證的 typed 值/
artifact」本身就是 typed edge，不需另立一條 edge 宣告。v1 **不**做雙端相容檢查（YAGNI + 保持
AI 好寫）；若 consumer 需要更嚴，收緊 producer 的宣告。

---

## 3. Workstream 2 — Authoring（維持 JSON，讓 AI 產得出合格 json 且知道邊界）

痛的真相：`sample-skills/author-workflow/SKILL.md`（109 行）**已 drift**——只教 5 種 step
（漏 `switch`）、傳值漏 `{steps.<name>.<field>}`、漏 `outputs`/`over` 變體/fan-in/`revise`/
`reads`、capability 只列 2 個（實際 7 個）。**手寫 prose 一定會再 drift。** 故分工：

### 3.1 (a) skill 收斂成通用目的層（D7）

`author-workflow` SKILL.md **只留概念與 golden rule**：workflow 是什麼、何時做 workflow vs
skill、decision/action split、produce→review→commit、一個 job、keep user in the loop、hand-off。
**刪掉會 drift 的 step 列舉/欄位/capability 清單**——那些交給 (b)。

### 3.2 (b) 機器衍生所有會 drift 的細節（D7）

1. **grammar 參考從 schema 自動生。** 從 `dsl.py` 的 Struct schema 衍生「如何產合格 json」的完整
   參考，涵蓋現行**全部**（6 種 step、`outputs`、`{steps.x.field}`、`over` glob/list/range、
   fan-in `.outputs`、`switch`、`gate revise`、`reads`）——**drift-proof**（改 DSL 自動同步）。
2. **per-app 邊界注入。** authoring 時把該 app 的 **capability allow-list + tool ceiling** 注入
   AI context，明說「這 app 開了/關了/完全沒有哪些」——重用 [#322] ceiling ＋ [#480] 三態揭露
   pattern。**不 hardcode 在 skill**（違反「App is a template」）。
3. **`save_workflow` 的 validate→bounce 是 validity 硬保證。** guidance 只加速首發命中；
   `validate_def` + `save_workflow` 才保證「產不出不合格 json」。§2 讓這個 validator 更嚴（連
   verify 不足都擋）。

---

## 4. Phasing（flat integer；每 phase 走 `/tdd`，每完成一 phase commit 一次）

| Phase | 內容 | 依 |
|---|---|---|
| **P1** | 通道 P：`agent` 的 `out` 預設 gate 從 `file_nonempty` 換 **`artifact_valid(out, kind)`**；`kind ∈ {markdown,json,csv,yaml,code,text}`；**不 sanitize**，verify fail → retry-with-feedback。← 治「檔案 = AI 回覆」 | — |
| **P2** | 封漏洞：拿掉 `engine.py:110` 的 `check=None→always-ok`；`sandbox` 預設 `exit_code==0`；`validate_def` 強制 agent＝`outputs` **XOR** `out`+`kind`（禁 both、禁 neither）。← 治「runner 無腦往下繼續」 | P1 |
| **P3** | producer-declared **L2 `requires`** 契約（必要 heading／parse 成 schema／最少段落），生產當下 verify | P1,P2 |
| **P4** | `author-workflow` SKILL.md → **通用目的層**：刪 drift-prone 列舉，只留概念/golden rule | — |
| **P5** | **機器衍生 grammar 參考** from `dsl.py` schema，涵蓋現行全部語法（drift-proof） | P4 |
| **P6** | **per-app capability/tool 邊界注入** authoring context（重用 #322 ceiling + #480 三態揭露） | P4 |

（Verify＝WS1＝P1–P3；Authoring＝WS2＝P4–P6，可平行。）

---

## 5. 明確 DROP（被否決，記錄免重議）

- **Tier 2 全部**：命令式 script、sandboxed interpreter（受限 Python / 嵌 JS）、`wf` god-object
  拆成 capability API + 瘦 client、`agent`-as-capability。理由見 §0.2——維持 JSON 更省更安全，
  且 verify（真痛）與語言無關。
- **對 LLM 輸出 sanitize**：改 retry-to-clean（§2.4）。
- **雙端（consumer/producer）契約相容檢查**：producer 單一宣告即可（§2.5）。
- **每 node AI 自動驗語意（L3）**：交 `human_gate`（§2.3）。

---

## 6. 非目標 / 延後

- 不改 `human_gate` / capability / journal / steer 的**語意**。
- 不動 release 版號、不碰 `config.yaml`。
- 節點層級視覺化拖拉 authoring（維持 phase 層級觀察）。
- per-element 真平行 sub-handle 屬引擎能力（#429 P5），與本計畫正交。

---

## 附錄 A — 現況痛點 → 修正後對照

| 現況 | 修正後 |
|---|---|
| `agent_write_step` 把原始回覆倒進檔案 | 不 sanitize；verify 抓到污染 → retry，模型自產乾淨的 |
| 預設 gate `file_nonempty`（非空就過） | 預設 `artifact_valid(out, kind)`（驗格式）＋ producer 宣告 `requires`（驗契約） |
| `check=None` → 永遠過（含 sandbox 指令失敗） | 封洞：sandbox 預設 `exit_code==0`；無 verify = schema error |
| node 邊界 = 「有個非空檔」 | 通道 D = schema-typed 值；通道 P = 驗證過的 artifact（producer 宣告、生產當下驗） |
| 一 node 混 `out`+`outputs` | 一 node 一種產出（D XOR P） |
| 語意正確沒人管 / 想用 AI 自動驗 | 語意 = `human_gate`；L1+L2 確定性到滿 |
| author-workflow SKILL.md 手寫且已 drift | (a) 通用目的層 skill + (b) 機器衍生 grammar + per-app 邊界注入 + validate→bounce |
| 想換命令式對齊 Claude Code | 否決；維持 JSON（痛在可靠性非語言，且命令式要拆 `wf`/agent-as-capability，代價過高） |
