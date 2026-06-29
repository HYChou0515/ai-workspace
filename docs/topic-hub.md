# Topic Hub — 手冊

> **狀態:** **Topic Hub** App 的規範性設計 spec(對應 issue 待定)。
> 這份文件同時是*目標*與*驗收標準*:當實作的可觀察行為符合這裡的規則時,就算「完成」。
> 刻意在 plan 之前先寫(「以終為始」)。所有決策都在一次 `/grill-me` 過程中鎖定;
> 被否決的替代方案就地記錄,免得日後重新爭論。
>
> Topic Hub 建立在、且大多由四個既有元件組成:
> **#89 Apps**(`docs/adding-an-app.md`)、**#100 Workflows**(`docs/workflows.md`)、
> **#106 Context Cards**(`docs/plan-context-cards.md`),以及 **#43 collab** 聊天
>(已在 `master`)。真正*新增*的平台工作很小,並在 §2 明確點出。

**Topic Hub** 是一個工作空間,用來進行一條持續、**跨 collection**、**會隨時間累積知識**
的探究路線。你把材料丟進來、針對它聊天(獨自或與他人),並執行 workflow 把材料蒸餾成
持久的 **memory** 檔案,把你的文件歸檔到正確的 KB **collection** —— 全都在同一個你會
跨 session 回來繼續的 item 裡。

grilling 中鎖定的關鍵框架:

- **Topic Hub 是一個 App**(`apps/topic-hub/`),不是新的第一級「Topic」資源。它倚賴整個
  #89 App 平台(launcher、item 清單、建立流程、檔案工作空間、agent、profile、members、
  mentions)與 #100 workflow 平台,並加上少數幾個小而**通用**的平台磚塊(§2)。
- **item 是容器;*topic* 是它的內容。** 一個 Hub 容納了*關於*某個主題的多個 chat、
  memory 檔案與 collection 參照 —— Hub 本身不是「一個 topic」。
- **Memory 即檔案。Collection 集合即一個檔案。Chat 即 workflow**(就 UI 而言)。
  **群組聊天從 #43 繼承**。這些都不需要重新發明。

---

## 1. 心智模型

- **一個 App,每個 Hub 一個 item。** `apps/topic-hub/` 是一個正常的 #89 App。每個 item 就是
  一個 Hub:一個檔案工作空間 + 一個 agent +(現在)**多個 chat** + 一個 **collection 集合
  (一個工作空間檔案)** + **memory 檔案**。
- **chat 即 workflow ——「一個引擎的兩種模式」。** 每個 chat 都是*同一個*
  `ChatTurnEngine`(workflows.md §1/§10)的一次 run。有些 chat 是 **orchestration 驅動**
  (由某個 workflow 的 `run()` 驅動 turn);有些是 **人類驅動**(自由聊天)。UI 完全相同;
  「開新 chat」就是在 **[free chat]** 或 **[某個 workflow 類型]** 之間挑選。
- **所有持久的東西都是檔案。** Memory(`memory/` + `MEMORY.md`)、collection 集合
  (`collections.json`)、workflow artifact、glossary 填寫稿 —— 全都是工作空間檔案,
  由 workflow 維護,並可由人類 + agent 編輯。一個小型索引檔(`MEMORY.md`)是永遠在 context
  中的核心(§6)。
- **檢索分三層,由便宜到貴**(§11):
  1. **memory** —— 永遠注入(`MEMORY.md`)+ 較深的檔案按需讀取;
  2. **context cards** —— 確定性、快速(`lookup_glossary` tool + #106 route 注入);
     涵蓋到的詞彙不需 RAG 就能回答;
  3. **docs / wiki** —— `ask_knowledge_base`(笨重;現在是*罕見*路徑)。

為什麼長這樣:使用者的 KB stack 在它的 prod vLLM 上無法關閉 reasoning,所以
`kb_search`/`ask_knowledge_base` 很慢(這正是原本 #106 的痛點)。把常見情況推到確定性的
memory + cards,就讓緩慢的 agentic 檢索變成例外,而不是每個 turn 都要跑。

---

## 2. 哪些是繼承的、哪些是新平台

**免費繼承**(沒有新工作 —— Topic Hub 只是*用*它):

| 來自 | 內容 |
| --- | --- |
| #89 Apps | launcher card、item 清單、建立流程、**檔案工作空間 + IDE + 檔案 tool**、agent + **per-profile 系統提示**、profile(seed 檔案 / skills / 提示)、`members` + `topics`(Tier-2)、3 層 agent-config 解析、`function.*` 開關 |
| #100 Workflows | `run()` orchestration、agent + 確定性節點、**`human_gate`**、**filesystem-journal + input-hash** 執行模型、`WorkflowRun`、capability(`ingest_to_collection`)、run-scoped 憑證、Run/Poll/Stream/Decide API、phase 圖 |
| #106 Context Cards | `ContextCard`(`norm_keys`)、`lookup`/`match`/`cards_for_collections`/`card_context_block`、route 層注入慣用法 |
| #43 collab(在 `master`) | **多寫者群組聊天**(App-item `send_message` 無 owner gate、`author` 蓋章、**廣播給線上觀看者**、`/stream`)、`mention` + 通知 |

**新平台磚塊** —— 小而**通用**(任何 App 都可用):

- **(I) 每個 item 多 chat** —— §3。*平台層級*(所有 App,含 RCA)。
- **(II) 每個 profile 多 workflow** —— §4。
- **Collection 集合作為工作空間檔案**(而非資源欄位)+ 一個 `resolve_collection` tool 供
  使用者按需管理它 —— §5。
- **確定性 context 注入**(`agent.context_files`)—— §6。
- **`lookup_glossary`** —— 一個只查 context card 的檢索 tool —— §7。
- **`create_context_card`** —— 一個 workflow capability(decision/action)—— §8。

**Topic Hub**(§9–§13)就是*組合*這些磚塊的那個 App。

> **Slug 註記。** 選定的 slug 是 **`topic-hub`**(帶連字號)。目前的 App 平台把
> `slug == dir == 可 import 的 package` 綁在一起(`apps.<slug>.model`,依 `adding-an-app.md`
> 不能有連字號)。要支援帶連字號的 slug,意味著**以檔案路徑載入該 App 的 `model.py`** ——
> 這正是 workflow `run.py` 與帶連字號的 profile 目錄*早已*採用的載入方式
>(`workflow/discovery.py`)—— 而不是 `import_module`。這是一個一個函式就能搞定的平台微調,
> 在此記為一項決策。

---

## 3. 多 chat(平台層級)

今天一個 item 有**剛好一個** `Conversation`(`_conversation_for` 以 `item_id` get-or-create),
而一個 workflow run 驅動那唯一的 conversation(workflows.md §1/§10)。Topic Hub 需要每個 item
**多個並行 chat**。

- **資料模型。** `Conversation` 變成基本單位,並取得 `id`、`title` 與一個選用的 `run_id`;
  **每個 item 多個**。一個 **workflow chat** = 一個 `Conversation` 加上一個 `WorkflowRun`
  (workflows.md §13)在**驅動**它;一個 **free chat** = 一個**沒有** run 的 `Conversation`。
  這就是把「一個 conversation 的兩種模式」(workflows.md §1/§10)具體化:run 是 conversation
  上的一層*疊加*,而非另一個實體。
  - *被否決:「一切都是 `WorkflowRun`」(free chat = 退化的 run)。* 一個 `WorkflowRun` 帶有
    phase/manifest/`pending_decision` 等對 free chat 毫無意義的欄位 —— 硬把它們留空只是雜訊。
    以 Conversation 為基底讓 free chat 保持簡單。
- **範圍:平台層級。** 多 chat 對**每個 App**(含 RCA)都啟用,不是 per-App 的 opt-in。
  - *被否決:per-App opt-in 開關。* 使用者選擇全域以求一致;反正下面的向後相容預設 chat
    會讓非多 chat 的 App 維持原樣。
- **向後相容:隱含的預設 chat + 加法式端點。** 每個 item 都保有一個隱含的**預設 chat**;
  既有的 item 層級端點(`/messages`、`/stream`、cancel、undo)在**沒帶** `chat_id` 時解析到它,
  既有資料就是那個預設 chat。新的 **chat-scoped** 端點(`/items/{id}/chats`、
  `/items/{id}/chats/{chat_id}/...`)為多 chat 介面而新增。
  - *被否決:全面 chat-scoped 重構 + 資料 migration。* 加法式作法 churn 少得多;客戶端與
    已儲存的 conversation 都可原封不動繼續運作。
- **Launch 即開一個 chat。** 「執行一個 workflow」會**建立一個 workflow-chat** 並回傳其
  `chat_id`。workflows.md §14 的「每個 item 最多**一個**進行中的 run」被**解除**:現在 run 是
  **每個 chat 一個**,且多個可平行進行(§3.1)。

### 3.1 並行

- **允許平行 run** —— 一個 Hub 內可有多個 chat(free 或 workflow)同時進行。
- **一個 Hub 的所有 chat 共用一個持久 `FileStore`**(事實來源)。一個 thread 裡的 chat
  (例如一個輔助的 free chat)編輯著某個暫停中的 workflow chat 正在等待的同一個檔案 ——
  這正是重點。
- **Last-write-wins,且具原子性。** Sandbox 寫回 `FileStore` 走 specstar 的內容定址 `write`
  (新 blob → 原子式 file-id 交換),所以整檔覆寫是原子的 —— **不會撕裂寫入(torn write)**,
  即使在並行下也是。無 live sandbox 的直接編輯路徑*更強*(etag 守護的 CAS:
  read→write→retry,在持續競爭下回報衝突 —— `tests/files/test_facade_cas.py`)。
- **無特別的跨 chat 並行控制。** 兩個 run 在同一瞬間猛打*同一個*檔案,在正常使用下不該發生;
  若真發生,就讓它失敗。持久紀錄因為寫回是原子的而保持一致。
- **Step 命名空間保持互斥。** 每個 workflow 的 journal 住在它**自己的資料夾**
  `/.workflow/<workflow_id>/`(#136),其內的平行寫入落在各自的 `step_<name>/<key>` 命名空間
  (workflows.md §9);只有**刻意共享**的檔案(例如 `memory/`、`collections.json`、一個 glossary
  填寫檔)會重疊,而那裡 last-write-wins 正是*預期*行為。作者要記住這點;平台不會去管制它。

---

## 4. 每個 profile 多 workflow

workflows.md §2 定下「一個 profile 有 **0 或 1** 個 workflow」並把「workflow == profile」綁死。
Topic Hub 需要一個 item(由一個 profile seed 而來)提供**好幾種** workflow 類型(例如 `→memory`、
`→collections`、`→consolidate`)。

- **`_profile.json` 帶一個 list。** 原本單一的 `workflow` 區塊變成
  `"workflows": [ { "id": "...", "title": "...", "phases": [...], "input_json": "..." }, ... ]`。
  每個 workflow 在 **`profiles/<name>/workflows/<id>/run.py`** 有自己的 orchestration。
  Discovery(`workflow/discovery.py`)以檔案路徑逐一處理它們(它本來就用檔案路徑 exec `run.py`,
  所以改動只是改成迭代一個目錄)。
- **一個 profile,一個行為套件,N 個 workflow。** 一個 profile 的所有 workflow 共用該 profile 的
  **tool 上限**、**seed 檔案** 與**提示** 資產(把 workflows.md §2 的「一個完整行為套件」修訂為
  允許 N 個 workflow;§18 的「profile = 不可變的行為版本」延伸為「一個提供 N 個 workflow 的版本」)。
- **「新 chat」挑選器**列出 **[free chat]** + seed profile 的 N 個 workflow。
- *被否決:App 層級的 workflow 目錄*(`apps/<slug>/workflows/`,任何 item 跑任何 workflow)。
  抽象上更乾淨,但它把 workflow 從 seed profile 解耦,並把 per-workflow 的 tool 上限打散;
  使用者要的是「在**一個 profile 裡**多種 workflow 類型」,而 profile 層級是較小、同形狀的改動。

---

## 5. Collection 集合 —— 一個工作空間檔案

Hub 的 collection 集合是一個**工作空間檔案**(`collections.json`,一個 `[{id, name}, …]` 的 list),
**而非** item 資源上的一個欄位。這讓 Hub 裡的一切都是檔案形狀(像 memory),並讓 `WorkItem` 保持輕薄;
它隨時可被**collection 挑選器**(§5.2)、agent(§5.1)、或 —— 作為逃生口 —— 在 Monaco IDE 直接
編輯原始檔案 來變更。

- **在 turn/run 時讀取,不從資源讀。** Workflow 用 `wf.read_json("collections.json")` 讀它;
  App 的 turn-context-builder 讀它來填 `collection_ids` 供檢索使用(`lookup_glossary`、
  `ask_knowledge_base`)。`→collections` workflow 的 `allowed` 集合**就是**這個檔案(取代
  WF §20 的 `wf.config["collections"]`)。
- **一個集合,兩種角色**(已鎖定):既是 Hub 各 chat 的**讀取範圍**,也是 `→collections` workflow
  歸檔進去的**寫入候選**(`check.choice_in(..., allowed=<from file>)`)。
- *被否決:一個 Tier-3 資源欄位。* 使用者為了一切皆檔案的一致性把它移到檔案系統。
  **接受的取捨:** 我們失去透過 specstar *索引/查詢*「哪些 Hub 參照了 collection X」的能力 ——
  v1 不需要。
- *被否決:profile 固定(workflows.md §20)* 以及*兩個分開的讀/寫集合* —— 一個可變檔案,兩種角色。

### 5.1 `resolve_collection` —— 按使用者需求管理集合

使用者以對話方式變更集合(「加上 equipment-log collection」)。他們給的是一個 **id 或一個 name**;
agent 需要 canonical 的 `{id, name}` 配對來記錄。

- **新 tool `resolve_collection(ref)`**:給定一個 id **或** 一個 name,回傳 canonical 的
  `{id, name}` —— 或在歧義時回傳候選 list / 在落空時回傳可用的 collection —— 透過查 collection
  registry。**它只負責解析;不負責寫入。**
- **agent 自己用它的檔案 tool 寫 `collections.json`**(`write_file` / `edit_file`),附加或移除
  解析出的 `{id, name}` 項目。這是互動式聊天,所以一個單純的檔案寫入就好 —— 不需要 decision/action
  節點(那個模式是給 *workflow* 副作用用的,§8)。
- *被否決:一個既解析**又**寫檔的 tool。* 把寫入保持為一個普通的檔案編輯,符合「agent 自己維護
  那個檔案」,也與 memory 的編輯方式一致。

### 5.2 Collection 挑選器(#142)

在 Monaco 裡手動編輯 `collections.json` 是 power-user 路徑,不是日常路徑 —— 所以 Hub 的 chat
頂部欄帶一個**collection 集合按鈕**,點開一個挑選器 modal。FE-only;後端(那個檔案、
`resolve_collection`、turn-time 讀取)不變。

- **按鈕狀態(可發現性)。** 空選擇 → 一個 accent 樣式的 **「選擇知識集」** 提示(一個沒有
  collection 的 Hub 沒東西能讓 agent 檢索);非空 → 一個安靜的 **「知識集 (N)」** 徽章。它是
  item 層級的(集合由每個 chat + agent 共享),所以它坐在 shell 欄上,而非某個 chat 內。
- **Modal = 對 live collection 清單的一個 checklist**(`GET /kb/collections`),帶一個搜尋框 +
  一條 **全選 / 清除** bar;每一列顯示 collection 的 icon、name 與 doc 數,並依 `collections.json`
  預先勾選。Checklist 主體是共享的 `CollectionsChecklist` 元件(#271)—— 與 KB chat 的 collection
  modal 渲染的是同一個 —— 所以全選與搜尋在兩處行為一致。全選 / 清除作用於**目前篩選後**的列
  (所以「搜尋,再全選符合的」可行)。
- **顯示與寫回都用 LIVE name**,所以被改名的 collection 會自我修復,檔案也為每個 turn 的 context
  注入(§6)保持新鮮。
- **持久化 = last-write-wins**(已鎖定的「有爆炸就給它爆」):modal 在開啟時重新讀取檔案,並在
  明確 Save 時覆寫整個檔案(無 merge,2-space JSON)並 invalidate 挑選器的讀取與任何開著的 Monaco
  tab。它**絕不在開啟時寫入**。
- **健壯性。** 檔案不存在 → 空選擇(無警告);整檔解析失敗 → 一個警告橫幅(檔案可能正被手動編輯
  到一半)但 Save 仍可覆寫它;格式不良的項目以後端 `collection_ids_from_json` 的方式容忍(丟棄 +
  計數)。一個 **orphan id**(其 collection 已被刪除)在它自己的區域被呈現,可一鍵移除,並在 save
  時**逐字保留**直到使用者移除 —— 絕不自動丟棄。

---

## 6. 確定性 context 注入(`agent.context_files`)

Hub 經整理的 memory 核心(與當前的 collection 集合)必須**每個 turn** 可靠地擺在 agent 面前
(本地小模型不會可靠地記得去 `read_file` 它們)。#106 已經做了這件事的一個*特定*版本 —— 它在
`engine.stream` 之前把匹配到的 context card 前置到 turn 內容。我們把那個慣用法**一般化進 config**。

- **新 config 欄位** `agent.context_files`(在 `app.json` / profile manifest 中):一個工作空間
  檔案的 list,其**即時內容**會在**每個 turn** 前置到交給 agent 的內容前面,包在一個帶標籤的區塊裡。
- **靜態指示**(「你的 memory 在下方;請視為最新內容;更深的細節在 `memory/`,按需讀取」)住在
  `prompts/system.md` —— 純文字,沒有新機制。
- **每個 turn、即時、絕不持久化。** 該區塊在 LLM-call 時從*乾淨*的 history + 檔案的*當前*內容
  **重新即時推導**,且**不**儲存到 conversation。因此:
  - 只有**最新**的 turn 會帶區塊 —— 不累積、沒有 N 份副本;
  - agent 永遠看到**當前**的 memory / collection 集合(兩者都會在 session 中變動);
  - 它是 `(檔案內容, turn)` 的純函式 → **冪等且 replay 安全**(#51 replay 會精確重現 LLM 看到的東西)。
  - *被否決:「儲存區塊,下個 turn 把它剝掉」。* 那會變動已儲存的 history 並破壞 resume/replay;
    「絕不持久化 + 重新推導」給出相同結果卻沒有那些風險。
- *被否決:pull-only*(小模型忘記去讀);*inject-everything*(無界限的 memory 撐爆 context);
  *prompt-template 內插 `{{file:…}}`*(一個新的 per-turn 提示組裝機制;§106 的前置法已被驗證且更簡單
  —— 留作 v1 的擺放位置,日後若想要更高的指示 altitude 再提供系統提示擺位)。

---

## 7. `lookup_glossary` —— 一個只查 context card 的 tool

一個給常見情況用的新**輕量 agent tool**:確定性地把一個詞彙比對 Hub 各 collection 的 **context cards**。

- **行為。** 給定一個詞彙(或自由文字),透過既有的 #106 primitives
  (`cards_for_collections` + `lookup` exact-key / `match` text-scan)回傳 Hub 各 collection
  (從 `collections.json` 讀取,§5)的匹配 `ContextCard`。**無 LLM、無 embedding、無 retriever、
  無 agentic loop。**
- **Context 需求極小** —— 只需 Hub 的 `collection_ids`(從檔案)+ 對 `ContextCard` 的 spec 存取。
  它**不**需要 `AgentToolContext` 裡的 `Retriever`(不像 `kb_search`),所以它*不是*那個被否決的
  「把 `kb_search` 硬塞進 App context」的 hack(見下方 §13 rationale)。
- **補足 #106 route 注入。** Route 注入掃描*使用者訊息的開頭*;`lookup_glossary` 讓 agent 在
  **工作中途**(在它正讀的檔案裡、在檢索到的 doc 裡)碰到的詞彙也能查並決定是否定義它。
- *被否決:把 `kb_search` 接進 App turn context。* `kb_search_impl` 斷言一個 App run 從不設的
  `retriever`,而 RCA 刻意改用 `ask_knowledge_base`。加一個小巧、不需 retriever 的 card tool,
  比把 KB retriever 回頭裝到 App context 上要乾淨。

---

## 8. `create_context_card` —— 一個 workflow capability

`→collections` workflow 以把人類填好的 glossary 轉成 context card 作結。依 workflows.md 的
**decision/action** 原則(§4/§8),agent **決定** card 內容*作為資料*;一個**確定性節點** commit 它。

- 一個新的 HTTP **capability**(像 `ingest_to_collection`,workflows.md §8):一個 sandbox 確定性
  節點用 run-scoped 憑證呼叫它,在 Hub 集合裡的某個 collection 上**建立一個 `ContextCard`**
  (重用 #106 的 author action)。
- 紀錄一個 `step_<name>/<key>` 收據,使其在 re-run 下可 checkpoint / 冪等;要求 collection 必須存在。

---

## 9. Topic Hub App

`apps/topic-hub/` 組合上述磚塊:

- **`app.json`**
  - `function.workspace: true`(檔案 IDE + 檔案 tool —— memory + 上傳檔 + collection 集合檔住這裡)、
    `function.sandbox: true`(workflow 確定性節點在 sandbox 裡跑並呼叫 capability)、
    `function.terminal` 選用。
  - `agent.tools`(上限):檔案 tool + **`lookup_glossary`** + **`resolve_collection`** +
    **`ask_knowledge_base`**(+ 各 workflow 節點需要的 data tool)。
  - `agent.context_files: ["MEMORY.md", "collections.json"]`(§6)—— memory 核心 + 當前的
    collection 集合,每個 turn 擺在 agent 面前。
  - `item.noun`:「Topic Hub」。
  - `members`/`topics` 的 layout/labels(collection 集合是一個檔案,不是欄位)。
- **`model.py`** —— `WorkItemBase` 子類別:重新宣告 `members`/`topics`。(collection 集合是一個
  工作空間檔案,**不是** model 欄位 —— §5。)`INDEXED_FIELDS` 只給日後新增的任何真正 Tier-3 純量用。
- **`prompts/system.md`** —— 「你的 memory 與當前 collection 每個 turn 都會提供;請視為最新內容。
  更深的 memory 在 `memory/` 底下 —— 按需讀取。用 `lookup_glossary` 查未知詞彙。要變更 collection,
  用 `resolve_collection` 再寫 `collections.json`。文件/wiki 內容,用 `ask_knowledge_base`。」
- **`profiles/default/`** —— seed `MEMORY.md`、一個 `memory/` 目錄,與一個初始的 `collections.json`
  (`[]`);宣告 N 個 workflow(§12);附帶任何 prompt/skill 資產。

---

## 10. Memory 模型

- **Memory 即檔案。** 一個 `MEMORY.md` 索引(自動注入,§6)+ `memory/` 底下較深的筆記。由 workflow
  建立與維護(§12);可由 agent(檔案 tool)與人類(IDE)自由編輯。
- **結構是慣例,不是 schema。** 提案中的 memory *類型*(Fact / Hypothesis / Insight / Decision /
  Goal / Summary)與 *confidence* 以**檔案組織 + 檔案內標註**表達(例如 `memory/decisions.md`、
  一個 `(unconfirmed)` 標籤)—— 由 `→memory` workflow 的輸出格式決定,**而非**一個 typed 的 specstar
  資源。
  - *被否決:一個第一級 typed `Memory` 資源*,帶 confidence + 一個 extraction→review 生命週期 +
    第二套檢索系統。對 v1 來說機制太多;一個 workflow 產出什麼是 App 實作(workflows.md §19)。

---

## 11. 檢索分層

一個 Hub chat 從三個來源回答,由便宜到貴:

1. **Memory(永遠)。** `MEMORY.md` 每個 turn 注入(§6);較深的 `memory/*.md` 按需讀取。
2. **Context cards(確定性、快)。** `lookup_glossary`(agent,工作中途)+ #106 route 注入
   (對使用者訊息的開頭掃描)。涵蓋到的詞彙以**無 RAG** 回答。
3. **Docs / wiki(笨重、罕見)。** 對 Hub 的 collection 集合(從 `collections.json`,§5)做
   `ask_knowledge_base` —— 唯一會跑緩慢 agentic KB 檢索的路徑,現在是例外。

- **深度檢索用 `ask_knowledge_base`,不是 `kb_search`** —— 它是 App 開箱即用的(不需 retriever
  接線),而且因為第 1–2 層吸收了常見情況而很少被打到。`kb_search` / `ask_knowledge_base` 本身不變。

---

## 12. 範例 workflow(預設 profile)

*僅供示意 —— 一個 workflow 做什麼是 App 實作(workflows.md §19)。*

- **`→memory`** —— 把上傳的材料消化成 memory 檔案。Agent 節點(讀取 + 摘要)寫 `memory/*.md` +
  刷新 `MEMORY.md`。Produce-then-write。
- **`→collections`** —— 標準的 **produce → review → commit**,「review」的內容住在**檔案**裡。
  寫作由 agent 做(它**草擬** glossary);人類**審閱**一份草稿,而非填空(#133):
  1. **classify**(agent,逐檔):從 Hub 集合(`collections.json`,§5)挑一個 collection + 寫一份
     摘要 + —— 趁它還開著那個檔案 —— **為每個未知詞彙草擬一份 markdown 定義**,給每個一個顯示用
     `title`、讀者可能搜尋的表面形式(縮寫 / 全名 / 中英別名)作為其 `keys`,以及一個確定/不確定旗標
     → `plan/r<round>/<f>.json`(gate:collection ∈ allowed 集合 + `terms` 形狀)。提示說明 keys 是
     以 EXACT 正規化成員資格比對(#182),所以 agent 把每個別名各列為一個 key,而不是把詞彙收斂成
     一種形式或寫成一整句。
  2. **cards**(確定性):把草稿(以正規化 key 去重、別名取聯集)組裝成 `context-card.todo.md` 裡的
     提案 card —— 每個詞彙一個 `<!-- card -->` 區塊,帶 `title` / `collection` / `keys` metadata 行,
     接著一段**自由 markdown 內文**(#183),所以一段內文可以用自己的 `##` 標題而不切開區塊(一份確定
     的草稿成為內文;不確定的成為一行 `⚠️`)。在它旁邊,寫一份**唯讀的「before」快照**
     `.readonly/context-card.current.md`:對每個提案 card,寫出 commit 時 upsert 會**覆寫**的既有 card
     (它真正的 keys/title/body),或在 card 是新的時寫一個空區塊(#205)。如此兩個檔案逐區塊 diff,
     一次靜默的 key 收窄就會可見,而非被藏起來。
  3. **`human_gate`**(`approve` / `reject` / `revise`):gate 摘要指向 **查看變更**,它開啟一個
     VSCode 風格的 diff —— 左 = `.readonly/context-card.current.md`(唯讀),右 = `context-card.todo.md`
     (可編輯)—— 所以一次覆寫絕不會被盲簽。人類在**diff 裡**審閱/編輯提案 card(或開另一個 chat 找
     LLM 幫忙 —— 共享 FileStore,§3.1)。**Approve** commit 檔案裡的內容(含他們的編輯);**revise** +
     一段註記會重跑整個 produce step 來重新產生草稿(覆寫),再次 gate;**reject** 結束 run,交給互動式
     接手。
  4. **commit**(確定性、冪等):對 docs 做 `ingest_to_collection` + 對每個*已填*區塊做
     `upsert_context_card`(§8,#111)—— `collection` 直接從區塊讀(所以人類改 title 不會誤導路由),
     未知的 collection 會大聲拒絕。一個仍只是一行 `⚠️` 的區塊被跳過(未解決),所以重新 classify 一個
     詞彙會更新它的 card,而非產生重複。
- **`→consolidate`** —— 讀取當前 memory + 近期 chat,**重寫** memory 檔案(去重 / 合併 / 摘要 / 丟棄
  過時)。自我參照;`memory/` 上 last-write-wins。**透過 Run 觸發**(由人類或外部排程器打 Run 端點)
  —— **沒有平台排程器**(workflows.md §14 早已把週期性委派給呼叫者)。有了多 workflow(§4),這只是
  另一個 workflow 類型,不是特殊機制。

---

## 13. 群組聊天與可見性

- **群組聊天是繼承的**(#43,已在 `master`):任何已認證的使用者都能送進一個 Hub 的 chat,訊息被
  `author` 蓋章、廣播給線上觀看者(`/stream`),`mention` + 通知也運作。**沒有新工作。**
- **v1 可見性 = 平台預設:** 所有內部已認證使用者皆可存取;既有的分享/mention 機制適用。**無 per-item
  ACL**、**無 private/team/org 範圍** —— 延後到真正的 SSO/authz 落地為止(提案的 open Q4)。

---

## 14. 平台與 App 的界線

- **平台磚塊(通用、可重用):** 多 chat(§3)、每個 profile 多 workflow(§4)、collection 集合工作
  空間檔案 + `resolve_collection` tool(§5)、`agent.context_files` 確定性注入(§6)、`lookup_glossary`
  tool(§7)、`create_context_card` capability(§8),以及帶連字號 slug 的檔案路徑 App loader(§2 註記)。
- **Topic Hub App 組合它們**(§9–§13):它的 `app.json` / `model.py` / `system.md`、三個範例 workflow、
  memory + collection 集合檔案慣例,以及檢索分層。

---

## 15. 分階段與非目標

**v1(Topic Hub 所需):**
§2 的各平台磚塊(多 chat,含預設 chat 向後相容 + 平行 run;每個 profile 多 workflow;collection 集合
檔案 + `resolve_collection`;`context_files` 注入;`lookup_glossary`;`create_context_card`;帶連字號
slug 的 loader);`apps/topic-hub/` App(§9);memory-as-files(§10);三個範例 workflow(§12);群組聊天
原樣繼承(§13)。

**延後 / 非目標:**

- **Per-item ACL / 可見性範圍**(private / team / org)—— 等 SSO/authz。
- **一個平台排程器**做週期性 consolidation —— 週期性是呼叫者的工作(workflows.md §14);
  `→consolidate` 是 Run 觸發。
- **一個 typed `Memory` 資源**(類型/confidence 作為 schema、extraction→review pipeline、一套獨立的
  Memory-Retrieval 系統)—— memory 即檔案;結構是慣例。
- **知識圖譜 memory**(實體 / 關係 / 證據)—— 提案的 open Q5;非 v1。
- **kb_search 延遲 / 在 prod vLLM 上關閉 reasoning** —— 一個獨立、早已延後的議題;Topic Hub *緩解*它
  (第 1–2 層)但不修它。
- **Steer-and-resume** 的 run 中插話 —— 繼承 workflows.md 的延後。
