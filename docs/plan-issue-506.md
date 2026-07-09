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

### 生成注入既有知識(②③,Q1 augment · Q2)
digest 前 `KnowledgeRetriever.for_doc(cid, path, text) -> KnowledgeContext`,注入改寫後的 prompt:
- `relevant_cards`:P3 先 exact-key 命中卡 body;P4 embedding 落地後升級語意 top-K。
- `relevant_questions`:同理(exact-term → 語意)。
- `rag`:既有 `kb/retriever.py`(dense+BM25)拉 KB **其他 doc** 相關段落(DocChunk 有向量索引)。
- `wiki_toc`:該 collection 的 wiki `_paths`(頁清單/標題)當**廣度**訊號(gated `_wiki_enabled`);**不做全文向量檢索**。
- `known_keys`:全 collection 卡 key + open Q term 清單(輕讀 indexed `norm_keys`)。

prompt(`card_drafting.md`)新增 `{existing_cards}/{open_questions}/{rag}/{wiki_toc}/{known_keys}`,指令:只建/問**沒被涵蓋**的;接近既有卡有補充 → 提「更新那張卡(引 key)」;拿不準是否已涵蓋 → 傾向不重問。**一份 doc 只做一次檢索**。

### wiki 用 grep,分兩處(避開無向量索引)
- **P3 生成**:只給 `wiki_toc`(便宜廣度)。
- **P4 reconcile**:對**有限的 draft 候選**逐個 grep wiki 全文(重用 `api/search.search_text`),命中即「wiki 已解釋」→ 進分級自動丟(⑥)。per-candidate grep 只跑在收斂後的候選,不爆搜。

### reconcile 分級(⑤⑥,Q1 reconcile · Q3)
候選 embed → 指派 cluster_key → 每群算建議動作:
- 群內最近**既有卡** ≥ τ_high **或 wiki grep 命中** → `已解釋`→ **自動丟(suppressed,可審計)**。
- 近某既有卡(部分) → `更新卡 X`(帶 target_card_id),等人**一鍵確認**。
- 無既有涵蓋 → `新卡`,整群合成 review 一列。

### cluster 如何做(Q3/Q4)
每個新候選:
1. **exact-key 快路**:`norm=norm(term)`;查 `norm_key==norm` 命中 → `cluster_key=norm`(確定性、無 race)。
2. **語意路**:`vec=embed(norm/term + " " + title)`;specstar 原生 cosine 查最近成員;≥ τ → 併其 cluster_key;否則 `cluster_key=norm` 開新群。
- **race**:同字面 burst 靠 exact-key 確定性解決;不同字面並行 race 由**背景 union-find sweeper**(兩群 centroid ≥ τ 併,兼做 P6 backlog 回填)掃尾。
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
先上 P1 分頁 + `collection_id` index(現存幾千筆立即變快、flat);新提案開始 cluster;P6 migration job 分批回填既有 pending 的 cluster_key(重用 embedder)。

## Phases(flat)

- **P1** review-inbox 分頁 + `CardGenRun.collection_id` index + server filter；FE 分頁/虛擬化。**既有 backlog 立即變快**(獨立可先出)。
- **P2** `ReviewDrawer` 可編輯 keys(純 FE,後端就緒)。獨立可先出。
- **P3** 生成升級:`KnowledgeContext` + `KnowledgeRetriever`(cards exact-key / open Q / RAG / wiki_toc / known_keys)+ 改寫 `card_drafting.md` + `_process_one` 接線。
- **P4** reconcile 網 + `ClusterMember`:候選 embed → cluster_key(exact + 語意)+ 分級動作 + wiki grep 覆蓋;suppressed 可審計;P3 的 relevant_cards/questions 升級成語意。
- **P5** review-inbox 按 cluster 分群投影 + FE 一群一列一鍵套用整群 + suppressed filter。
- **P6** 背景 sweeper:union-find 併近群 + 分批回填既有 pending backlog。

## 可調預設(不再逐個確認)

- **τ**:config 超參,初值保守偏高(寧少併);exact-key 不吃 τ。top-K:cards≤15 / questions≤10 / rag≤5(可調)。
- **embed 內容**:`norm_key/term + title`(短規範字串),非整篇 body。
- **desc_Q**:注入 context 對三種產出都有益,但**只有 term-cards + term-questions 進 cluster**;desc_Q 維持 `(doc,norm(quote))`。
- **embedder / dim**:重用 `kb/embedder.py` + 既有 `EMBED_DIM`(換 embedder 要 reindex,沿用現況)。

## 驗證 DoD

- 單元:KnowledgeRetriever 產出 top-K + known_keys(空 collection / 截斷 / 相似度地板);drafter 注入內容進 prompt 且解析仍過;cluster 指派(exact 確定性 / 語意併群 / 過門檻開新群);reconcile 分級(自動丟 / update / 新)+ wiki grep 命中;review-inbox 分頁 + GROUP BY cluster;sweeper union-find 併群。
- 整合/live:真 embedder + 真 LLM 的 canned check(#feedback:LLM 功能要 live check);幾千筆 backlog 分頁效能。
- 100% gate(full local suite)綠;ruff/ty 綠;FE vitest(新 FE 走 TDD)。
