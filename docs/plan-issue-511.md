# Plan — #511 待審 inbox 真分頁:CardGen 提案抽成獨立 resource

## 需求 / 問題(RCA)

待審核 inbox(flat + grouped)目前是**假分頁**:offset/limit 只切一個「已全部載入記憶體」的清單,昂貴的載入每頁都重做 → flat ~12s、grouped ~60s。

- `_card_items`(`kb/review_inbox.py:334`)→ `CardGenRunStore.runs_by_status(statuses, collection_id=)`(`kb/card_gen_run.py:175`)`list_resources(query.build())` **無 limit/offset**,把該狀態的**所有** `CardGenRun` blob(連同每個 run 整包 `proposals`)全載入。
- `_question_items`(`review_inbox.py:366`)→ `questions_by_status`(`kb/doc_questions.py:126`)同樣 `list_resources` **全載入**所有 `DocQuestion`。
- `build_review_inbox`(`review_inbox.py:313 / 325`)把兩流 merge、Python 排序,**之後才** `merged[offset:offset+limit]` 切片。

**真因**:提案巢狀在 `CardGenRun.proposals`(list 欄位),**不是**可查詢/排序/分頁的 DB row。specstar 的 `order_by(...).offset(o).limit(n)` 真分頁在別處已在用(doc 列表 `api/kb_routes.py:1541` `rm.search_resources(ordering.offset(offset).limit(limit).build())`),但巢狀提案吃不到。

**#510**(空殼 `done` run → `empty` 狀態 + drain,draft PR)只是減少要載入的 run 數,是同一根因的**症狀補丁** → **廢棄**(draft PR 關閉),改做本重構。本重構落地後 0-proposal run = 0 列,inbox 天生看不到,`empty`/drain 不需要。

## 敲定設計(grill 結論)

1. **提案抽成獨立 resource `CardProposal`**(權威內容 + 審核 `decision`),不再巢狀。→ 三視圖全部走 specstar 原生 DB 分頁。
2. **`ClusterMember` 不動**:仍是 embedding + `cluster_key` + `state` 中樞,供 reconcile 的**單一 cosine query**(`assign_cluster_key` 掃所有 kind 找最近鄰、`grade_candidate` 掃 `kind=="card"`,`kb/reconcile.py:46-111`)。`ref_id` 指向 `CardProposal.id`(提案)/ `DocQuestion.id`(問題)/ `ContextCard.id`(既有卡)。**embedding 留在這張表**,否則跨 kind 的最近鄰單查就得改多表 union。
3. **flat 兩流分開分頁**:提案子清單 pages `CardProposal`、問題子清單 pages `DocQuestion`;**不**強求兩者混同一時間軸(避免跨兩表的單一 offset/limit,無現成解)。FE 待審區拆兩個子清單。
4. **grouped 仍是「一概念一列」**(⑤:同概念的提案+問題併一列),靠 `ClusterMember.cluster_key` 聚合分頁(見依賴)。
5. **#510 廢棄**:本分支自乾淨 master 開,不含 `empty`/drain;draft PR #510 關閉。

## 依賴:specstar 需加「分頁 / 排序的 aggregate」

`Sum/Min/Max/Avg` 聚合已 shipped(specstar #406)。唯一缺口:**`exp_aggregate_by` 的結果無法排序 + 分頁**。

> **specstar feature request**:`exp_aggregate_by(by, aggregates, query=None)` 目前一次回**所有** group、無序。請加 optional **`order_by`**(依某個具名 aggregate 或 group key、asc/desc)、**`offset`**、**`limit`**,並提供 **distinct-group 總數**(給 pager 的 total),全部下推到 store engine(`GROUP BY … ORDER BY … LIMIT/OFFSET`)。
>
> Use case:待審 inbox 的「依概念分組」要對 distinct `cluster_key` 依「最新成員時間」分頁,現在被迫把所有 group 撈進 Python。

**依賴標記**:P1–P3、P5 **不**依賴此功能(可先做);**只有 P4 的 grouped 分頁**依賴新版 `exp_aggregate_by`。順序:先提 specstar issue → P1–P3 平行推進不等它 → specstar 版本到位後做 P4。

## 資料模型

```python
class CardProposal(Struct):  # → resource "card-proposal"
    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    run_id: Annotated[str, Ref("card-gen-run", on_delete=OnDelete.cascade)]
    keys: list[str] = field(default_factory=list)
    title: str = ""
    body: str = ""
    confident: bool = True
    mode: str = "new"                    # new | update
    target_card_id: str | None = None
    provenance: list[Provenance] = field(default_factory=list)
    decision: str = "pending"            # pending | accepted | rejected | committed
    # active = decision ∈ {pending, accepted};terminal = {committed, rejected}
# add_model(CardProposal, indexed_fields=["collection_id", "run_id", "decision"])
# 排序鍵 = specstar meta created_time(doc 列表已證可 order_by;沿用)
```

- `CardProposal.id` 對齊 reconcile 既有 `ref_id` scheme(`prop:{run}:{pid}`)→ migration 後既有 ClusterMember 免重投影。
- `decision` indexed,讓「待審(active)/ 歷史(terminal)」查詢走 index。
- **cluster_key 不放 CardProposal**:grouped 需跨「提案+問題」兩 kind 分群,只有 `ClusterMember` 同時含兩者;故 grouped 聚合 over `ClusterMember`,CardProposal 不必揹 cluster_key(免 reconcile/merge-sweeper 雙寫)。

## 三視圖分頁(全部真 DB)

- **flat 提案**:`(QB["collection_id"]==cid) & (QB["decision"].in_(["pending","accepted"]))` `.order_by(created_time desc).offset(o).limit(n)`;total = 同 query 的 count。→ specstar 現成,零新功能。
- **flat 問題**:`DocQuestion` where `status=="open"`(+collection)`.order_by(created_time desc).offset.limit`;total = count。→ 順手把 `questions_by_status` 改原生分頁。
- **grouped**:`ClusterMember.exp_aggregate_by(by=cluster_key, {n:Count, latest:Max(created_time)}, query=(collection & state=="active" & kind∈{proposal,term_question}), order_by=latest desc, offset=o, limit=n)`(**新 specstar 功能**)→ 拿當頁 `(cluster_key, n, latest)` + distinct 總數 → `cluster_key IN (當頁)` 載成員 → 提案成員讀 `CardProposal`、問題成員讀 `DocQuestion`。actionable 過濾在 cluster 層(任一成員 collection 可寫即 actionable)。
- **history / suppressed 審計**:同款原生分頁(history = `decision∈{committed,rejected}`;suppressed = `ClusterMember.state=="suppressed"` 分頁)。

## 生命週期改道

- `_finalize`(`card_gen_coordinator.py`):每個 kept proposal `create` 一列 `CardProposal`(id=`prop:{run}:{pid}`);reconcile 投影 `ClusterMember`(`ref_id`=CardProposal.id)。**不再寫 `run.proposals`**。0 proposal → 0 列(無 `empty` 狀態需求)。
- 逐提案 CAS(取代 `CardGenRunStore` 對巢狀 list 的 read-modify-write):
  - `decide(proposal_id, decision)` → update `CardProposal.decision`(+同步該 `ClusterMember.state`:terminal 時 active→inactive,讓 grouped 聚合自動排除)。
  - `update_proposal(proposal_id, new_card)` → update `CardProposal` 內容。
  - `commit(run_id)` → query `run_id` 的 accepted `CardProposal` 列 → 寫 `ContextCard` → 標 committed(+member inactive)。
- **run 收尾**:不再需要 `committed/dismissed` run-status;「待審」= 「該 collection 還有 active `CardProposal`」的查詢。`CardGenRun.status` 簡化回純生成生命週期(`pending/running/done/error`);`_RUN_STATUS` 移除 review-terminal 條目。

## Migration

既有 `CardGenRun.proposals`(巢狀)一次性 backfill 成 `CardProposal` 列(`id`=`prop:{run}:{pid}`,保留 `decision`)。specstar migration step 或一次性程式;線上資料保留不丟。驗證後(P5)才 drop 巢狀欄 + `set_proposals`。

## Phases(flat integer;每 phase 走 /tdd + commit;FE 走 vitest)

1. **P1** 新 `CardProposal` resource + `_finalize` 改寫成寫 `CardProposal` 列 + 既有巢狀資料 backfill migration(`CardGenRun.proposals` 暫留唯讀 fallback)。
2. **P2** 逐提案 CAS 改道:`decide` / `update_proposal` / `commit` / run 收尾(改 count 查詢)全改成操作 `CardProposal` 列;`_RUN_STATUS` 簡化。
3. **P3** flat 真分頁:提案 pages `CardProposal`、問題 pages `DocQuestion`;`review_inbox` + route 兩區各自 offset/limit/total;FE 待審區拆兩子清單 + 各自 Pager。**（不依賴 specstar 新功能）**
4. **P4** grouped 真分頁:靠新版 `exp_aggregate_by`(order+offset+limit)分頁概念 + 載當頁成員;reconcile `ref_id`→CardProposal;history / suppressed 一併原生分頁。**（依賴 specstar 新功能）**
5. **P5** drop 巢狀 `CardGenRun.proposals` + `set_proposals` + fallback(migration 驗證後)。

## 狀態(P1–P4 DONE,PR #512)

- **P1–P3(backend)DONE**。P3 flat:`CardProposalStore.page_for_review` + `page_questions_by_status` 原生分頁;`kind=all` 用 **bounded-merge**(各載 top `offset+limit`、merge-sort、切片;跨兩表單一 offset 的正確且有界解法,推翻 grill 的「無現成解」前提)。故 **flat FE 不拆兩區**——單表 Pager + 後端 merge 已達效能目標。
- **P4 DONE**。實作時發現 plan 假設的兩個地基 #506/P2 從未建:
  1. **`ClusterMember.state` 從不同步**——decide/commit/answer 只改 CardProposal/DocQuestion,member 永遠停在建立時的 active,故「GROUP BY active member」會把已 resolve 的概念算進總數。P4 補上 de-join:新 `kb/cluster_member.set_member_state` 接進 `CardProposalStore._cas`/`replace_run_proposals`(proposal terminal→翻同 id member,id 就是 CardProposal id `prop:{run}:{pid}`)與 `doc_questions.answer_question`/`discard_question`(term q→`tq:{qid}`;description q 無 member=no-op)。
  2. **description DocQuestion 不投影成 member**——依 plan §「grouped 聚合限 kind∈{proposal,term_question}」,description q 只在 flat 視圖,grouped 不含(移除舊 load-all 路徑的 singleton fallback 副作用)。
  - grouped 原生分頁:`cluster_member.page_clusters`/`count_clusters` 用 `exp_aggregate_by(QB["cluster_key"],{n:Count,latest:Max(QB.created_time())},order_by="-latest",offset,limit)` + `exp_count_groups`,載當頁成員再 resolve;`q`-過濾走 bounded scan fallback `_grouped_scan`。suppressed 也原生分頁。
  - **specstar 0.11.15 已含 #412**;`Max(QB.created_time())` 可對 ResourceMeta 時間戳聚合,**不需**在 ClusterMember 加 indexed 時間欄。
  - **FE 零改動**:ReviewPage grouped tab 早在 #506 G2 就送 `limit/offset` + Pager + 讀 `total`,後端原生後自動生效。
- **剩**:P5(drop 巢狀欄)+ full 100% gate 終驗。

## 可調 / 決定

- **flat 提案 vs 問題**:分兩區各自分頁(grill 敲定),不混同一時間軸。
- **cluster_key**:只住 `ClusterMember`(免雙寫);grouped 聚合 over member。
- **embedding**:留 `ClusterMember`(保 reconcile 單一 cosine query)。
- **#510**:廢棄,draft PR 關閉。

## 驗證 DoD

- 單元:`CardProposal` CRUD + 逐提案 CAS(decide/update/commit/收尾 count);`review_inbox` 三視圖回**只當頁**列(不再全載入);grouped 聚合分頁(specstar 功能到位後)。
- FE(vitest):待審兩區各自 Pager;grouped Pager。
- 整合 / live:幾千筆真資料下 flat/grouped 皆**單頁載入量固定**(非隨總數線性)。
- migration:既有巢狀提案 backfill 後 inbox 顯示不變、決定狀態保留。
- 100% gate(full local suite)綠;ruff/ty 綠;FE vitest 綠。

相關:`docs/plan-issue-506.md`(閉環去重 + ClusterMember/reconcile 由來:P6 ClusterMember、P7 grouped 投影、P8 sweeper)。
