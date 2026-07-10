# Plan — #506 doc question / context card suggestion:效能 + 品質 + 閉環去重

## 需求(使用者六點)

1. **前端顯示慢**(待審幾千筆,review 頁卡)。
2. **提問/推薦品質不佳**。
3. **出題/推薦要能看 wiki + glossary + RAG**,避免一直問同一題/類似題。
4. **人要能改 card 的 keys**(現在審核抽屜只能改 title/description)。
5. **重複的待審推薦要先看舊的並「合併」再發問**。
6. **同 key 已有 card,要先看舊的決定增修/放棄**(已有解釋就別再推)。

## 現況 RCA(grill 盤點,file:line)

- **doc question 與 card suggestion 共用同一個 per-doc digest**:`card_gen_coordinator._process_one`(`kb/card_gen_coordinator.py:390`)→ `LlmCardDrafter.digest(doc_path, doc_text)`(`kb/card_drafter.py:53`),prompt `kb/prompts/card_drafting.md`,一次吐 `{cards, term_questions, description_questions}`。②③改的是這**一個共用生成器**。
- **鏈是開環的**:生成器只看單 doc 內文(`drafting_prompt` 只 `{path}`/`{document}`,`card_drafter.py:35`),**看不到既有知識**;「別重複」邏輯全塞在 finalize 當**事後 exact `norm_key`**:`classify_against_existing`(`kb/card_gen.py:294`)、`open_or_merge_term_question`(`kb/doc_questions.py:73`)。換字面/wiki 已解釋 仍照問。
- **④ 後端已支援改 keys**:`edit_context_card`(`api/context_card_routes.py:33-73`)收 `{keys,title,body}` 並重算 norm_keys;`ContextCardsTab`(`web/src/pages/kb/ContextCardsTab.tsx:256-285`)已能改。只有 `ReviewDrawer`(`web/src/pages/kb/ReviewDrawer.tsx:122-129`)把 keys 顯示唯讀。
- **⑤ 無跨 run 去重**:`merge_drafts`(`card_gen.py:254`)只在單 run 內;`_finalize`(`card_gen_coordinator.py:438`)只比 committed cards,不看其他 pending run。burst auto-digest(`index_coordinator.py:613` 每 doc 一 run)→ 同新詞 N 個獨立待審。
- **⑥ exact-key 已有分級**:`classify_against_existing` new/update/skip;缺近似/語意 + wiki 覆蓋。
- **① review-inbox 全撈無上限**:`build_review_inbox`(`kb/review_inbox.py:103`)每 collection × 每 done run × 每 proposal + 每 open Q,每列帶整包 body;FE(`ReviewTable.tsx`)全載入純前端 filter,無分頁/虛擬化;`CardGenRun.collection_id` 未 index(`resources/__init__.py:433`,只 index `status`)。
- **wiki = Karpathy prose,無向量索引**:`search_wiki`(`agent/tools.py:339`)= 子字串 grep over `WikiFileStore`(`kb/wiki/store.py`);wiki 是 per-collection opt-in(`_wiki_enabled`)。**不能靠向量 retriever 拉 wiki 段落。**

## 敲定設計(grill Q1–Q6,把開環變閉環)

### 生成升級:把 `ask_knowledge_base` 擴充成可設定能力,drafter 用配好的它(②③,Q1 augment · Q2)

one-shot `LlmCardDrafter` → **agentic drafter**(agent loop,重用 `run_subagent` 橋)。**不直接配 leaf 工具**,改給一個**配好 spec 的 `ask_knowledge_base`**:保住 context isolation(#270,吵雜檢索留丟棄式子 agent、drafter 視窗乾淨),又能控 budget/prompt/scope。

**把 `ask_knowledge_base` 從寫死變可設定 —— spec + builder(變異全是「資料」非「演算法」,不用 GoF class factory):**

- **① `WikiSearchBudget`**(對稱 `KbSearchBudget`,`agent/context.py:28`):新型別 + `AgentToolContext.wiki_search_budget` 欄;`search_wiki_impl`(`agent/tools.py:339`)加 budget gate(比照 `kb_search` 在 `tools.py:557-564`)。**這一項就治好「wiki 搜尋無上限」的洞。**
- **② `AskKbSpec`**(frozen,factory 輸入,budget-only、**無 `wiki_mode`**):
  ```python
  @dataclass(frozen=True)
  class AskKbSpec:
      kb_search_max:   int | None = 3     # 0=不授、N=上限、None=無限
      wiki_search_max: int | None = 3     # 同款;0=off、N=auto+上限、None=無限
      glossary:        bool = True        # lookup_glossary（便宜、不設 budget）
      prompt:          str | None = None  # 覆寫子 agent 指令;「強制查 wiki」寫這
      scope:           list[str] | None = None   # collection_ids;None=沿用呼叫端
      sub_agent_purpose: str = "kb_chat"
  ```
- **③ builder(factory 本體)** `build_ask_kb_context(spec, base) -> AgentToolContext`:授 `kb_search`(KB agent 必授)+ `search_wiki`(iff `wiki_search_max != 0`)+ `lookup_glossary`(iff glossary);`collection_ids = spec.scope or base`;`system_prompt = spec.prompt or preset`;塞 `KbSearchBudget`/`WikiSearchBudget`。
- **④ factory function(可選薄層)** `make_ask_knowledge_base(spec)` 回一個 closure over spec 的 impl;或單一 impl 讀 `ctx.ask_kb_spec`。**不是**為每種呼叫者生 tool 子類。
- **⑤ wiki 從「路由」改「工具」(唯一行為改動)**:現況 wiki 是 turn 前置路由到重的整頁 reader(`WikiAwareRunner`,`orchestrator.py:163`);改成把 `search_wiki`(grep,輕,回命中行)當 budgeted 工具授給子 agent、由 agent 自己決定要不要搜 —— 即現況 FE toggle ON 的 **auto** 語意,但補上 budget。重的整頁 reader 保留當正交深讀變體,本 issue 不動。

**drafter 的 spec**:`AskKbSpec(kb_search_max=3, wiki_search_max=3, scope=[cid], prompt="出卡前先查既有卡片/wiki/RAG,回報哪些已涵蓋、哪些是新的")`。cid 由 `_process_one` 手上的 `CardGenSources(spec, collection_id)` 貫穿(`card_gen_coordinator.py:365`);**無 cid → 退回 open-loop(不注入、只看內文)**,不拿全域當範圍(別的 collection 的解釋不算數、又貴)。便宜靜態種子(可選):prompt 可塞 `known_keys`(全 collection 卡 key + open Q term)當廣度,讓 drafter 問得準。

**FE + route(與 kb_search 同款,取代 toggle)**:request body 加 `max_wiki_searches`(鏡射 `max_kb_searches`),移除 `enhancements.wiki: bool`;route 用 `WikiSearchBudget(max_calls=resolve_max_searches(body.max_wiki_searches, default, ceiling))`(比照 `kb_chat_routes.py:647-654`);FE composer 的 wiki on/off toggle → 數量選擇器(沿用 kb-search 那顆的樣式)。

prompt(`card_drafting.md`)改 agentic:先查清「已被涵蓋 / 已問過」再決定;只建/問**沒被涵蓋**的;接近既有卡有補充 → 提「更新那張卡(引 key)」;拿不準 → 傾向不重問。

### wiki 讀取:靠 budgeted `search_wiki` 工具(agent loop 內),非向量
- **生成(P5)**:agent 用 `search_wiki`(grep,受 `wiki_search_budget` 約束)自己搜 wiki;scope=[cid](`WikiFileStore` 以 collection id 為 key)。
- **reconcile(P6)**:對**有限的 draft 候選**逐個 grep wiki 全文(重用 `api/search.search_text`)當「已解釋」的確定性判定 → 進分級自動丟(⑥)。這層是無 LLM 的安全網,不吃預算。

### reconcile 分級(⑤⑥,Q1 reconcile · Q3)
候選 embed → 指派 cluster_key → 每群算建議動作:
- 群內最近**既有卡** ≥ τ_high **或 wiki grep 命中** → `已解釋`→ **自動丟(suppressed,可審計)**。
- 近某既有卡(部分) → `更新卡 X`(帶 target_card_id),等人**一鍵確認**。
- 無既有涵蓋 → `新卡`,整群合成 review 一列。

### cluster 如何做(Q3/Q4)
每個新候選:
1. **exact-key 快路**:`norm=norm(term)`;查 `norm_key==norm` 命中 → `cluster_key=norm`(確定性、無 race)。
2. **語意路**:`vec=embed(norm/term + " " + title)`;specstar 原生 cosine 查最近成員;≥ τ → 併其 cluster_key;否則 `cluster_key=norm` 開新群。
- **race**:同字面 burst 靠 exact-key 確定性解決;不同字面並行 race 由**背景 union-find sweeper**(兩群 centroid ≥ τ 併,兼做 P8 backlog 回填)掃尾。
- **範圍**:term-cards + term-questions 一起分群(同概念一次解決);**description-questions 不進語意群**(維持既有 `(doc,norm(quote))` 去重)。

### 新 resource(存向量 + 群)
```python
class ClusterMember(Struct):  # → resource "cluster-member"
    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    kind: str            # "proposal" | "term_question" | "card"
    ref_id: str          # 來源列 id
    run_id: str = ""     # kind="proposal" 的 CardGenRun id
    norm_key: str = ""   # norm(term/key)
    cluster_key: str = ""
    state: str = "active"  # active | inactive(去 join 化,inbox 讀不必回查來源狀態)
    embedding: Annotated[list[float] | None, Vector(dim=EMBED_DIM, distance="cosine")] = None
# add_model(ClusterMember, indexed_fields=["collection_id","cluster_key","state","norm_key"])
```
理由:候選住三個異質位置(ProposedCard 巢狀在 CardGenRun.proposals、無法掛 indexed Vector),正規化成一張表 → 單一向量查詢 + 單一 cluster_key 供 GROUP BY。重用 `kb/embedder.py` + EMBED_DIM。

### review-inbox 分頁 + 分群投影(①⑤,Q4/Q5)
`WHERE collection_id=… AND state="active" AND kind∈{proposal,term_question} GROUP BY cluster_key` 分頁;一群一列(展開看成員、一鍵套用整群);server filter collection/type/status;FE 虛擬化/分頁;suppressed 一個 filter 可審計。

### 可編輯 keys(④)
`ReviewDrawer` 加 keys 編輯器(重用 `ContextCardsTab` term chips),送 `{keys,title,body}` 到既有 proposal update route(`api/card_gen_routes.py:137`)/`context_card_routes`。

### 既有 backlog(分階段,Q6)
先上 P1 分頁 + `collection_id` index(現存幾千筆立即變快、flat);新提案開始 cluster;P8 sweeper 分批回填既有 pending 的 cluster_key(重用 embedder)。

## Phases(flat,依「擴充 ask_knowledge_base → FE budget → drafter 接它 → reconcile/cluster」重排)

- **P1**(**後端 DONE**,commit `cc4886bd`+`83096901`)review-inbox server 分頁(`limit`/`offset`/`total`/`total_actionable`)+ server filter(`kind`/`q`/`actionable`,`q` 鏡射 FE 欄位、跨全集非僅當前頁)+ `CardGenRun.collection_id` index(配 no-op `Schema("v2")` 讓既有 rows 可 `POST /card-gen-run/migrate/execute` backfill;未 backfill 前全域 inbox 仍看得到全部、只 per-collection tab 少算舊 row)。**FE 分頁/虛擬化併入 P7**(P7 本就要把 review UI 重做成 cluster 分群投影,避免改兩次;後端向後相容,現況 FE 照常運作)。
- **P2**(**DONE**,commit `b31b3a09`)`ReviewDrawer` 可編輯 keys(chips 帶 `×` + 「新增詞彙」input,重用 ContextCardsTab 模式;`edited` 納入 keys、save 送 keys;唯讀時 keys 顯示但不可編)。純 FE:後端 `update_proposal` 已持久化整張 card(proposal 無 norm_keys,commit 時才 derive)。vitest 6 tests、typecheck 綠(④)。
- **P3**(**DONE**,commit `5d8b2672`)**擴充 `ask_knowledge_base` factory(backbone,非破壞)**:`WikiSearchBudget`(對稱 `KbSearchBudget`)+ `AgentToolContext.wiki_search_budget` + `search_wiki` budget gate(exhausted→sentinel、cap0→disabled、grep 後 used+1+footer;預設 unlimited→wiki maintainer/reader 不變)+ `AskKbSpec`/`build_ask_kb_context`(`agent/ask_kb.py`;`allowed_tools()` 授工具、builder 塞 budgets/scope)。`make_ask_knowledge_base` 延到 P5(有 consumer 才建)。舊 `WikiAwareRunner` 路由不動。22 unit 綠、ask_kb.py 100% cov、ruff/ty 綠。
- **P4** **FE + route**:`max_wiki_searches` 數量選擇器取代 wiki toggle(與 kb_search 同款)+ route 接 `WikiSearchBudget`;移除 `enhancements.wiki`。(只需 P3)
- **P5** **生成升級**:drafter agentic、用配好的 `ask_knowledge_base`(scope=[cid] / budgets / prompt)+ 改寫 `card_drafting.md` + `_process_one` 接線(cid 貫穿 + 無-cid fallback;②③)。(只需 P3)
- **P6** **reconcile 網 + `ClusterMember`**:候選 embed → cluster_key(exact + 語意)+ 分級動作(自動丟/update/新)+ wiki grep 覆蓋判定;suppressed 可審計(⑤⑥)。
- **P7** review-inbox 按 cluster 分群投影 + FE 一群一列一鍵套用整群 + suppressed filter(①⑤)。
- **P8** 背景 sweeper:union-find 併近群 + 分批回填既有 pending backlog。

## 可調預設(不再逐個確認)

- **budget 初值**:drafter `kb_search_max=3` / `wiki_search_max=3`;`lookup_glossary` 無限。FE 各自 clamp 到 `[0, ceiling]`(operator 預設)。
- **τ**:config 超參,初值保守偏高(寧少併);exact-key 不吃 τ。reconcile 最近鄰 top-K 可調。
- **embed 內容**:`norm_key/term + title`(短規範字串),非整篇 body。
- **desc_Q**:注入 context 對三種產出都有益,但**只有 term-cards + term-questions 進 cluster**;desc_Q 維持 `(doc,norm(quote))`。
- **embedder / dim**:重用 `kb/embedder.py` + 既有 `EMBED_DIM`(換 embedder 要 reindex,沿用現況)。

## 驗證 DoD

- **單元(擴充 ask_knowledge_base)**:`WikiSearchBudget` gate(`search_wiki` 受上限、耗盡即停,對稱 `kb_search`);`build_ask_kb_context` 依 spec 授對工具/塞對 budget/scope/prompt(`wiki_search_max=0` → 不授 `search_wiki`;`glossary=False` → 不授);`make_ask_knowledge_base` closure over spec。
- **單元(drafter/reconcile)**:drafter 用配好的 `ask_knowledge_base` 且結構化輸出解析仍過、cid 貫穿、無-cid 退回 open-loop;cluster 指派(exact 確定性 / 語意併群 / 過門檻開新群);reconcile 分級(自動丟 / update / 新)+ wiki grep 命中;review-inbox 分頁 + GROUP BY cluster;sweeper union-find 併群。
- **FE(vitest)**:`max_wiki_searches` 數量選擇器(取代 wiki toggle);`ReviewDrawer` keys 編輯。
- **整合/live**:真 embedder + 真 LLM 的 canned check(#feedback:LLM 功能要 live check);live 驗 drafter 真的會搜 wiki/kb 且**受 budget 約束**;幾千筆 backlog 分頁效能。
- 100% gate(full local suite)綠;ruff/ty 綠;FE vitest(新 FE 走 TDD)。
