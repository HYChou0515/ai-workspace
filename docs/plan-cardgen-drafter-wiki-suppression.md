# Plan — Drafter 用 wiki 自我壓卡（#506/#577 follow-up）

## 問題（已確認）

大 collection（~1000 docs + ~800 wiki pages）跑 card generation，**卡片提案 ≈ 0**。使用者在「已自動略過」分頁看到 5 筆、理由全是「已在 wiki 說明」。

**釐清**：在現在的 master，**卡片提案不可能**被標成 `wiki` 理由（`reconcile.py` 只對「術語提問」設 `wiki_hit`）。所以那 5 筆一定是**術語提問**（被 wiki 壓是 #577 後的正確行為）。使用者分不出來，是因為 suppressed 分頁**沒顯示 `kind`**（觀測黑洞）。真正的問題是：**卡片幾乎沒被生成**。

## 根因（實查接線，非推測）

**真兇 = agentic drafter 的自我壓抑（`api/card_drafter_agent.py`）。**

- 啟用鏈：`app.py:947` — `card_drafter_llm is not None` ⇒ `wire_agentic_card_drafter` 無條件把 drafter 換成 `AgentCardDrafter`。使用者能看到 card-gen 產出（問題/suppressed），代表 `kb.card_drafter` 有設 ⇒ **跑的就是 agentic drafter**（否則是 `NullCardDrafter`，連 0 卡都不抽）。
- drafter 的 `ask_knowledge_base` spec：`default_drafter_ask_kb_spec()`（`card_drafter_agent.py:89-94`）= **`kb_search_max=3, wiki_search_max=3, glossary=True`（寫死，非 config）**，scope 被 `drafter_context_builder` 釘成 `[collection_id]`。docstring 自述「consults ALL of RAG + wiki + glossary before drafting」。
- prompt `kb/prompts/card_drafting_agentic.md` 第 3 步：**「The knowledge base already explains it well → it is already known; leave it out. Draft no card...」**。
- **結構性必然**：drafter 抽的詞來自這 collection 的 doc，又有 800 wiki pages ⇒ 幾乎每個詞的 `ask_knowledge_base` 都會從 wiki/RAG 撈到解釋 ⇒ 判「已知」⇒ **不出卡**。這正是 `reconcile.py:280-286` 註解記載、#537/#577 已在 **reconcile** 階段修掉的自我壓抑 bug（「every key drafted off a page is by construction present in the corpus being greped」）。

**#577 無辜且已生效**：它把「卡片不拿 wiki grade」寫死在 `reconcile_proposals`（無 config gate），只要 reconcile 有跑就生效。但 #577 的範圍**只涵蓋 reconcile**——drafter 是另一條它從沒碰的 code path，且 drafter 的 wiki 預算**寫死、config 關不掉**。

## 修法原則

把 #577 的原則落到 drafter：**「wiki/來源語料已解釋」只能壓「問題」，絕不能壓「卡片」。** 卡片對卡片的去重交給 reconcile（已在做、#577 對）。一個概念出現在 wiki/文件裡，恰恰是它**該有卡**的理由，不是省略的理由。

### 為什麼「只改 prompt」不夠

prompt 是給 LLM 的軟指令，且 drafter 用**同一次 KB consultation** 同時決定「出不出卡」和「問不問」，wiki 命中會同時壓掉兩者。要可靠，得**從結構把 wiki/RAG 移出「卡片判斷」**，不能只靠 prompt 措辭。

### 關鍵洞察：reconcile 已是唯一去重權威

- `reconcile_proposals`：卡片 vs 既有卡片（near-card ≥ suppress_tau），**不碰 wiki**（#577）。
- `reconcile_term_questions`：問題 vs wiki + 既有卡片（`_wiki_mentions` → 壓已解釋的問題，G1）。

所以 drafter **根本不需要**自己查 KB 去重——它只要**從 doc 忠實抽取**（doc 定義得出的→卡片；doc 提到但沒定義的→問題），去重與 wiki-壓問題全部由 reconcile 收尾。drafter 的 KB 自我 consultation 是**冗餘且有害**的。

## Phases（flat integer；逐 phase /tdd + commit）

### P1 — 觀測：讓「卡片死在哪」看得見（先做，讓修可驗證）
- **FE**：`SuppressedAuditList.tsx` 每列顯示 `kind`（卡片提案／術語問題／描述問題，i18n）；near-card 時顯示「撞到哪張既有卡」（後端已有 `SuppressedItemOut.kind`，near-card 的 target card 需補一欄）。suppressed 分頁上方顯示分類計數（X 卡片、Y 問題）。
- **後端可觀測**：`card_gen_coordinator._finalize` 已記 `n_units/n_raw_drafts/n_proposals`（`:539`）——把這三個數**經 route 曝到 FE**（每個 collection 的「上次生成摘要」），使用者不必翻 log 就能看「抽了幾張草稿→留幾張」。A4 的「has text but digested to 0 cards」WARNING 計數一併曝。
- **驗收（先紅）**：suppressed 列渲染出 kind 標籤；一個 near-card 項顯示目標卡名；生成摘要顯示 raw_drafts/proposals 數。**這塊讓使用者能親眼核對 P2 修完卡片數從 0 變多。**

### P2 — drafter 不再用 wiki/RAG 壓卡（核心修）
- **spec（結構）**：drafter 抽卡的判斷不吃來源語料。最小改動 = `default_drafter_ask_kb_spec()` → `kb_search_max=0, wiki_search_max=0`（保 `glossary=True`）——drafter 的 `ask_knowledge_base` 只剩 glossary（既有卡片 exact-key 查詢），「已知」= 「已經有卡」，不再是「wiki/別的文件提過」。
- **prompt**：`card_drafting_agentic.md` 第 3 步拆開——
  - 卡片：**只要 doc 給得出定義就出卡**；明講「卡片和 wiki 頁是不同東西，值得做卡的往往正是 wiki 解釋過的概念」；不因 wiki/其他文件提過而跳過。
  - 問題：維持「已解釋就別問」（但實際壓抑交給 reconcile，drafter 可放手提，reconcile 收）。
- **驗收（先紅）**：
  - 單元：`default_drafter_ask_kb_spec()` 的 `wiki_search_max==0`（鎖住 wiki 不進卡片路徑）；`AskKbSpec.allowed_tools()` 在此 spec 下**不含 `ask_wiki`**。
  - 管線：一個 scripted drafter 對「wiki 已解釋的詞」仍吐出 card → reconcile 不壓（#577 已保證）→ 落地成 CardProposal。證明 wiki-covered 的詞現在會變卡。
  - ⚠️ **真正的行為證明需 live canned check**（真 LLM + wiki-heavy collection → 卡片數 0→多），列為 DoD（LLM feature，見下）。

### P3 —（視 P1 觀測結果）覆蓋補洞
- 若 P1 摘要顯示 `n_units << 選取數`，代表「still-indexing skip」（`_process_one:426-440`）也在吃 doc。屆時再決定要不要補（例如生成前擋住未 ready、或 ready 後補跑 hook，比照 #530）。**先不預設要做——用 P1 的數字決定。**

## 驗證 / DoD
- 單元 + 管線測試（P1/P2 的先紅測試）。
- **Live canned check（LLM feature DoD，需 user 的 Ollama/1M 環境）**：一個有 wiki 的 collection，生成前 vs 後，卡片提案數應明顯上升，且「wiki 解釋過但無卡」的詞出現在提案裡。比照 `scripts/check_cardgen_closed_loop_506.py` 加一個 case。
- 全套 100% gate 交 CI。

## 風險 / 取捨
- **審核量暴增**：drafter 放手抽 ⇒ 1000 docs 可能吐大量卡片提案。這正是使用者要的（有卡可審），且 review-inbox 的 cluster 分群 + 分頁（#506 P7/G2）本就為此設計。但值得在 P1 觀測到實際量後確認 UI 撐得住。
- **glossary-only 去重夠不夠**：drafter 只用 glossary（既有卡）去重，跨 doc 的同詞重複由 `merge_drafts`（norm_key）+ reconcile near-card 收。若同義不同字面的重複變多，reconcile 的語意 near-card 會接住；τ=0.92 偏嚴，必要時 P3 再調（走 `kb.cluster` config，不硬編）。

## 驗證（逐條，實作後回填）

**P1 — 觀測**
- ✅ **P1a suppressed 分頁顯示 kind + 計數**：`SuppressedAuditList.tsx` 每列一個 `suppressed-kind-{kind}` 標籤（卡片提案／術語問題，i18n），上方 `suppressed-summary` 顯示「X 卡片、Y 問題」。測試 `SuppressedAuditList.test.tsx`「labels each row's KIND」＋「summarises the counts by kind」。→ 使用者現在一眼分得出「5 筆 reason=wiki」是**問題**不是卡片。
- ✅ **P1b 後端 funnel 持久化 + 曝到 FE**：`CardGenRun` 加 `n_units/n_raw_drafts/n_proposals`，`_finalize` 經 `finish(...)` 寫入（同一次 CAS）。`GET /kb/context-card-gen/{job_id}` 帶三數；新 `GET /kb/collections/{cid}/context-card-gen/latest` 回**最近一次已完成 run**（含 kept=0，走 `latest_finalized`，`(collection_id,status)` 皆有索引）。待審核分頁顯示「最近一次生成：讀 N 來源 → 抽 D 草稿 → 留 K 提案」。測試：coordinator `test_finalize_persists_the_funnel_counts_on_the_run`／`test_latest_funnel_reports_the_most_recent_finalized_run`；route `test_status_exposes_the_finalize_funnel_counts`／`test_latest_run_funnel_route_reports_the_collections_last_run`；FE `CollectionReviewTab.test.tsx`「shows the last run's drafted→kept funnel summary」＋「shows no funnel summary before…」。
- ✅ **near-card 顯示撞到哪張既有卡**：`ClusterMember` 加 `target_label`（near-card 壓抑時從 `existing` 用 `grade.target_card_id` 反查既有卡標題），經 `MEMBER_FIELDS` 投影 → `SuppressedItem` → `SuppressedItemOut` → FE。`SuppressedAuditList` 對 near-card 列改顯示「已有相近卡片「<卡名>」」。測試：reconcile `test_reconciler_suppresses_a_proposal_that_duplicates_a_card`（斷言 `target_label=="TAGX"`）、route `test_review_inbox_suppressed_lists_dropped_candidates`（`s["target_label"]=="Reflow Zone 3"`）、FE「names the existing card a near-card row duplicated」。backfill 只投影新 active member、不覆寫 suppressed，故 target_label 不會被抹掉。

**P2 — drafter 不再用 wiki/RAG 壓卡（核心修）**
- ✅ **spec 結構**：`default_drafter_ask_kb_spec()` → `kb_search_max=0, wiki_search_max=0, glossary=True`；`allowed_tools()==["lookup_glossary"]`。測試 `test_drafter_spec_grants_no_wiki_or_corpus_search_so_it_cannot_self_suppress_cards`。
- ✅ **接線沒被覆寫**：抓到 `wire_agentic_card_drafter` 原本 `replace(default_spec, kb_search_max=max_searches or 3)` **把 RAG 又打開**（死旋鈕）——改為傳 default spec 原封不動，並移除已無用的 wiki consultant（ask_wiki 不再被授予）。回歸守門 `test_wire_passes_the_glossary_only_spec_and_never_re_enables_corpus_or_wiki`（spy builder：`max_searches=3` 也不得洩進 drafter spec）。
- ✅ **prompt**：`card_drafting_agentic.md` 拆開——卡片「只要 doc 定義就出、別處提過**不是省略理由**」，移除舊「leave it out」；問題維持「別問已解釋的」但實際壓抑交給 reconcile。測試 `test_agentic_drafter_prompt_does_not_suppress_a_card_for_being_covered_elsewhere`。
- ✅ **reconcile 仍是唯一去重權威（#577 未動）**：卡片對卡片 near-card 壓抑不變；問題才吃 wiki_hit。
- ✅ **行為 DoD（live canned check）已備成可跑腳本**：`scripts/check_cardgen_drafter_glossary_only_577.py` — 用 config 解析的**真 LLM** 跑**與 app 相同的 `wire_agentic_card_drafter` 接線**（fresh in-memory spec、不碰真 collection），種一張既有卡當 glossary，對一份定義多個術語的文件跑 drafter，檢查：抽到 ≥1 卡（不再全壓）、定義但無卡的詞（SP-7/MSL）被抽、已有卡的「Reflow Zone 3」被略過。單元測試證**結構**（spec=glossary-only、接線不再打開 corpus/wiki），此腳本證**真模型在真 loop 下真的出卡**。⚠️ 需 user 有可達的 tool-calling model 才跑得動（我無 Ollama 無法代跑，但已驗 import/ty/ruff 乾淨）。跨 corpus+wiki 的 before/after（提案 0→多）仍是 `check_cardgen_closed_loop_506.py` 記的 app 內流程（看待審核 funnel）。

**P3 — 覆蓋補洞（已完成，非延後）**
- ✅ **still-indexing skip 歸因**：重讀 P3 後確認「ready 後補跑」本就存在（auto_digest hook #377/#530）＋ modal 已提示手動重生成，缺的只是**可見性**。補：`CardGenRun.skipped_indexing`（`mark_skipped_indexing` 同時記 done 供 gate + 記 skipped 供歸因），funnel 加 `n_skipped_indexing`，route + 待審核分頁顯示「（另有 S 份仍在索引，完成後再生成）」。→ n_units 偏低現在會標明是「還沒 ready」而非 drafter 失敗。測試 coordinator `test_the_funnel_attributes_the_coverage_gap_to_still_indexing_docs`、FE「attributes a coverage gap to still-indexing sources」。原 plan 的「生成前擋住未 ready」刻意**不做**（混合選取應照樣處理 ready 的那些，整批擋住是錯的）。

## Non-goals
- 不改 reconcile 的卡片去重（#577 對、別動）。
- 不把「wiki 出現」做成**主動加卡**的正面訊號（使用者第二句的強版）——先把「不再壓卡」做對、用觀測確認，強版另議。
- 不碰 wiki 對「問題」的抑制（#577/G1 對）。
