# Plan: LLM wiki — a second, parallel retrieval pipeline (#50)

> 概念來源:Karpathy 的「LLM wiki」(gist 442a6bf…)。不是把原文切 chunk 在
> query time 拼湊,而是讓 LLM 在 **ingest time** 把知識「編譯」成一組互連的
> markdown 頁(summary / entity / concept / index,含 `[[wikilink]]`、標矛盾),
> 之後保持更新;query time 讀這套已編好的 wiki 答題。「synthesis 的功在 ingest
> 做一次,不是每次 query 重算」。
>
> **本專案的定位(使用者明確約束):LLM wiki 是「第二套並行管線」,不取代現有
> chunk-RAG。每個 collection 各自選 `use_rag` / `use_wiki`(兩個獨立開關)。**

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | `Collection.use_rag/use_wiki` + `WikiPage` resource + `WikiFileStore`(per-page,FileStore protocol)+ 每 collection 灌入 bundled `WIKI.md` + 刪 collection 時 cascade 清理 | ✅ |
| **P2** | sandbox-free wiki AgentToolContext flavour + `search_wiki`(FileStore-backed grep)+ `read_new_source` 工具 + `wiki_maintainer` agent purpose(重用既有檔案工具) | ✅ |
| **P3** | Ingest hook — doc index 完成後(coalesced)觸發 maintainer 增量編輯。`WikiMaintenanceCoordinator`:per-collection 序列佇列(單程序無 lost-wakeup;跨 worker 靠 specstar CAS,屬未來硬化) | ✅ |
| **P4** | Wiki-reading agent(純 agentic 導覽)+ source-doc citations(option 2:`read_source` 在 reader context 註冊 passage 回 SourceDoc,沿用 `parse_citations`) | ✅ |
| **P5** | ~~Query 整合 — `WikiAwareRunner`(KB chat 專屬 engine):chunk-only 為純 passthrough(零風險),wiki-only 直接串流 reader,both = 兩 agent 各答 + 共用引用清單 renumber + merge agent 串流~~ **RETIRED by #537** —— 路由把「開 wiki」與「同時搜文件」焊死(wiki-only 需要 scope 內每個 collection 都關 `use_rag`)。改為 KB agent 用 `ask_wiki` 自行選擇來源;`wiki_reader` 保留(就是 `ask_wiki` 委派的對象),merge agent 退役(合併由本來就要綜合的那個 agent 做)。 | ⛔ |
| **P6** | ~~per-query「Search the wiki」勾選(`EnhancementsInput.wiki` → `ctx.wiki_query`)~~ **RETIRED by #537** —— 勾選框只能「加上」wiki,無法「只要」wiki。App composer 不再有 wiki 控制(交給 KB agent 判斷);KB chat 改為每個來源一個配額,0 = 該來源本回合關閉。 | ⛔ |
| **P7** | FE — collection 模式開關 + 唯讀 Wiki 瀏覽分頁([[wikilink]] 可導覽 + Rebuild)+ depth「Search the wiki」勾選 + 後端 wiki-browse 端點 | ✅ |
| **P8** | live 健康檢查(#51):`agent-wiki-reader`(search_wiki)/ `agent-wiki-maintainer`(write_file)兩個 canned check(`ToolCallCheck` 參數化) | ✅ |

每階段完成定義:`ruff check && ruff format --check && ty check` 清、`pytest` 綠、
FE `vitest`+`tsc`+`build`、commit、本表打勾。

### 0.1 · 後續處理(MVP 後,使用者 2026-06-07 拍板,皆已落地)

| 項目 | 內容 | 狀態 |
|---|---|---|
| **跨 worker CAS** | `WikiFileStore.read_with_etag`/`write_cas`(specstar v0.11.6 etag,in-place modify 也會 bump);`WorkspaceFiles.edit` duck-type 之 → etag-guarded read→write→retry,第二個 ingest worker 改同頁時 re-base 不覆寫。單程序仍靠 coordinator 序列化。 | ✅ |
| **admin 清空** | `DELETE /kb/collections/{id}/wiki`(`WikiFileStore.clear`)—— rebuild 永遠增量、不刪;此為「打掉重練」後端把手,無 FE 入口。 | ✅ |
| **RCA→KB wiki** | ~~composer「Search the wiki」勾選 → RCA turn body → `ask_knowledge_base` 經 `_run_subagent(wiki_query=…)` 改用 wiki-aware runner(`answer_question(wiki=True)` 設 `ctx.wiki_query`);infer_modules 維持 chunk-only。FE 依 `design_handoff_rca_3.0` 在 RCA composer 顯示同一 toggle。~~ **RETIRED by #537**(見 P5/P6)。 | ⛔ |
| **FE wiki 瀏覽** | 依 `design_handoff_rca_3.0/rca/views/wiki.jsx`:header(AI-maintained + Rebuild)、分組 tree(Index/Entities/Concepts)、prose + `[[wikilink]]`、**可點 Sources footer**(解析頁尾 `Sources:` → 開 source doc)、empty/building 狀態。 | ✅ |

---

## 1 · 鎖定的決策(grilling)

- **定位**:第二套並行管線,不取代 chunk-RAG。
- **per-collection 模式**:`Collection.use_rag: bool = True`、`use_wiki: bool = False`
  (兩個獨立開關;`use_rag` 預設開 → 現有 collection 全部向後相容)。
- **維護方式**:**增量編輯**(貼 Karpathy)。maintainer agent 讀新原文 +
  `list/read/write` 現有 wiki 頁,跨頁增量更新;**不**每次全量重建。爆發式多檔上傳
  **coalesce** 成一次維護。
- **Query 時 wiki 檢索**:**純 agentic 導覽**(讀 index → 跟 `[[wikilink]]` → 讀頁),
  **#537 更正**:導覽是主路,`search_wiki`(grep)只是 fallback —— Karpathy 原文是 index-first + 讀整頁;#506 一度只給 KB agent 一把 grep,等於把 fallback 當唯一手段。現在 KB agent 用 `ask_wiki` 委派給這個 reader。
  **無向量索引**。是 **Knowledge search depth 的 advanced 選項**(per-query 可勾可不勾)。
- **both 模式**:**兩個 agent 各答再合併** —— chunk-agent 一份答、wiki-agent 一份答,
  再一個 merge 步驟整併(去重/重編引用)成最終答案。
- **範圍**:本次一次做到底(含 query 整合 + FE)。

## 2 · 架構

```
                       ┌─ use_rag ─► 既有 chunk-RAG(DocChunk 向量 + BM25 → 答 + 引用)
collection ── query ──┤
                       └─ use_wiki ─► wiki-reading agent(導覽 wiki 頁 → 答 + 引用[指進 wiki])
                                       (兩者都開 → merge 步驟整併成一份答案)

                       ┌─ use_rag ─► 既有 chunk + embed → DocChunk
upload doc ── index ──┤
   (完成後)            └─ use_wiki ─► wiki_maintainer agent(讀新原文 + 增量編輯 wiki 頁)
```

### 2.1 儲存 — wiki = 每 collection 一個 FileStore 資料夾(Karpathy-faithful)

貼 Karpathy:wiki **就是一個資料夾的 .md 檔**,agent 用一般檔案操作維護/查詢。
我們已有這套基礎建設(FileStore + `read_file`/`write_file`/`edit_file`/`ls`),所以
**不另建 `WikiPage` resource / `IWikiStore` / 自訂工具** —— 重用現成檔案層。

- **`Collection`** 加:`use_rag: bool = True`、`use_wiki: bool = False`。
- **wiki workspace**:每個 collection 一個 FileStore workspace,id = `wiki:{collection_id}`
  (與 investigation id 不同命名空間)。裡面就是 markdown 檔:
  `index.md`、`WIKI.md`(schema)、`entities/*.md`、`concepts/*.md`,`[[wikilink]]` = 相對路徑。
- **backing = per-page,不用單 blob(效能,見 §5①)**:wiki 的 FileStore 用一個新的
  **`WikiFileStore`(實作 FileStore protocol,但一頁一個 `WikiPage` resource)**,**不**用
  `SpecstarFileStore`(那是「一 workspace 一 resource、所有檔內聯」→ 改一頁要讀寫整包 wiki +
  整包 revision,wiki 高頻 churn 會寫入放大)。`WikiPage` = `{collection_id, path,
  content: Binary}`(metadata 靠 specstar、不自定;content 進 blob);`collection_id` 建索引。
  agent 工具**完全不變**(還是 FileStore protocol 的 read/write/edit/ls)。
- **draft 寫入,不自動記 history(效能,見 §5①)**:`WikiFileStore` 的 `write`/`edit`
  改用 specstar **`modify()`(draft 原地改,不產生新 revision)**,而非 `update()`。
  → wiki 頁高頻 churn **不留 per-edit history**(machine 維護,逐筆歷史是 bloat/noise)。
  要粗顆粒歷史的 hook:maintainer run 結束 promote 成 stable(每個 ingest 批次一個 checkpoint)
  —— 預設不做,留可選/未來。
- **持久化(回應顧慮)**:`write_file`/`edit_file`/`ls`/`read_file` 全部**直接打 FileStore**
  (見 `agent/tools.py`:`_workspace(ctx)` → `fs.create/read/edit/ls`;只有 `exec` 會喚醒
  sandbox)。`WikiFileStore` 把每頁存成 specstar blob,持久、去重、**改一頁只動一頁、
  draft 原地改**。→ **wiki 寫入即落地持久層**,沒有 ephemeral sandbox 暫存、沒有 sync 競態。
- **生命週期(回應顧慮)**:wiki agent 的 context **`sandbox=None`、不掛 `SandboxSync`、
  不掛 `exec`**。因此**完全不依賴**「人點進 workspace 才啟用 sandbox / idle-kill」那套
  (那是 RCA investigation 的人發起 session 模型,不適用於 ingest/query 觸發的 wiki)。
  maintainer / reader 各開一個 FileStore-backed context、做完即走。
- **清理**:刪 collection 時清掉 `wiki:{collection_id}` 這個 FileStore workspace。
- **Raw sources(Karpathy layer 1)= 既有 `SourceDoc`,不搬家**:上傳檔的 content(Binary
  blob)+ 抽取 `text` 仍存 SourceDoc(chunk-RAG 也吃這個)。wiki agent **只讀不改**,
  透過 `read_source(path)` / `list_sources()` 存取(讀 `SourceDoc.text`)。
- **頁的 source provenance**:maintainer 寫/改一頁時,必須在頁尾 `Sources:` 記下這頁綜合自
  哪些 source(path/id),讓 reader 循得回原文做引用(option 2 的基礎)。
- **Schema 層**:bundled 預設灌進每個新 wiki workspace 根目錄的 `WIKI.md`(= CLAUDE.md
  角色:wiki 結構/慣例/工作流程,含「每頁要記 `Sources:`」「維護 grep-friendly `log.md`」)。
  它就是一個檔,日後 per-collection 可被 agent 或人改寫。

### 2.2 Wiki agent 的工具 = 現成檔案工具 + FileStore-backed grep(sandbox-free)

maintainer / reader 都重用 `agent/tools.py` 既有檔案工具(`read_file`/`write_file`/
`edit_file`/`ls`),context 指向 `wiki:{collection_id}` workspace、`sandbox=None`。
→ 全部純檔案操作、直接 FileStore、持久、無 sandbox,小模型不用學新工具。

- **`search_wiki(pattern)`(grep)**:Karpathy 原文明確用 grep(`grep "^## [" log.md | tail -5`
  掃結構化 log)。我們給一個 **FileStore-backed 的 grep 工具**:重用 `api/search.py`
  的 `compile_query`/`search_text`/`path_selected`,直接掃 wiki workspace 的檔
  (同 `_search_files` 的純 FileStore 路徑,**不經 sandbox**)。→ 有 grep、又維持
  sandbox-free。**不違背「無向量索引」的決策** —— grep 是文字工具,非 embedding。
- maintainer 另需一個唯讀的 `read_new_source()` 把這次要消化的原文文字餵進來
  (走 context 欄位,不入 FileStore)。
- 仍**不掛 `exec`**(那才會喚 sandbox)—— grep 由 in-process `search_wiki` 取代。

### 2.3 Ingest:maintainer agent(增量)

- 新 agent purpose **`wiki_maintainer`**(catalog preset,如 `infer_modules`)。
- context:`filestore` + wiki workspace id(`wiki:{cid}`,讀寫)+ collection SourceDoc
  (原文唯讀)+ `wiki_new_source` 指這次觸發的 doc + `sandbox=None`。工具:現成
  `ls`/`read_file`/`write_file`/`edit_file` + `search_wiki`(grep)+
  `read_source(path)`/`list_sources()`(原文唯讀)+ `read_new_source()`(這次那份的捷徑)。
- system prompt = wiki workspace 根目錄的 `WIKI.md`(schema/慣例)+ 增量維護指示
  (讀新原文 → `search_wiki` 找相關既有頁 → 寫/更新其 summary 頁、**頁尾記 `Sources:`** →
  更新 `index.md` / append grep-friendly `log.md` 一行 → 動相關 entity/concept 頁 → 標矛盾)。
- **觸發 + coalesce**:`Ingestor.index(doc_id)` 完成且該 collection `use_wiki` → 排程一次
  maintainer run。**per-collection 去抖**:同 collection 已有 run 在跑就標 dirty,跑完若
  dirty 再跑一次(爆發式上傳合併成一次)。off-loop(`asyncio.to_thread`)。
- **MVP 增量最小可行**:讀新原文 → 寫該原文 summary 頁 + 更新 `index.md`;再逐步長出
  entity/concept 跨頁維護(差別在 `WIKI.md` 指示與測試深度)。

### 2.4 Query:wiki-reading agent(純 agentic 導覽)

- 新 agent purpose **`wiki_reader`**。工具:現成 `ls`/`read_file` + `search_wiki`(grep)
  (導覽 wiki 頁)+ **`read_source(path)` / `list_sources()`**(讀底層原文 ground)。
  context 指向 `wiki:{cid}`(wiki 讀)+ 該 collection 的 SourceDoc(原文唯讀)、`sandbox=None`。
  **無向量檢索**(grep 是文字工具,非 embedding)。
- 流程:`search_wiki`/讀 `index.md` → 跟 `[[wikilink]]` → 讀 wiki 頁**定位**答案在哪 →
  循頁尾 provenance `read_source` 讀底層原文**佐證** → 寫答案 + `[n]` 引用。
- **引用 = 指回原文 `SourceDoc`(option 2,可稽核)**:reader 讀過的 source 累積成
  可引用單位(同 kb_search 的 passage registry 角色),`parse_citations` **原封不動**:
  `document_id = SourceDoc id`、`filename = 原文名`。**不需 `is_wiki` 旗標** —— wiki 引用
  與 chunk-RAG 引用同型、同 FE 卡片、點開同一份 source doc。
  代價:wiki 是合成內容,span 對不回原文精確位置 → 引用偏 doc 級(snippet = reader 讀到
  的相關段),比 chunk 引用粗,換來「指回真來源」的可稽核性(使用者已選此 trade-off)。

### 2.5 both 模式:兩 agent 各答 + merge

當 collection `use_rag` 且 `use_wiki` 且 query 勾了 wiki:
1. 並行跑 chunk-agent(既有 `answer_question`)與 wiki-agent → 各得(答案, 引用)。
2. **merge 步驟**:一個 merge agent/prompt 收兩份答案 + 兩組引用 → 整併成一份連貫
   答案,引用去重 + 重編號(chunk 引用與 wiki 引用並存於同一 `[n]` 序)。
3. 回最終答案 + 合併引用。
單開一條時不進 merge(直接回該條的答案)。

### 2.6 Query depth advanced 選項

`EnhancementSettings` / KbEnhancementPicker(現已收進 ModelEffortPicker 的 depth 區)
加一個 **`wiki`** advanced 開關(per-query bool):collection `use_wiki` 開時,depth 區
出現「Search the wiki」可勾;勾了才走 wiki 路徑。預設值由 operator 設(同 expand/hyde/rerank
的 default/max 慣例)。turn body 多帶這個 flag(沿用 `EnhancementsInput` 機制)。

### 2.7 FE — source docs 與 wiki pages 都看得到(使用者要求)

- **Collection 建立/編輯**:`use_rag` / `use_wiki` 兩個開關。
- **Source docs**(layer 1):**既有** documents 表格 + `KbDocViewer` 照舊,不變。
- **Wiki 瀏覽(唯讀)**(layer 2):collection 開一個「Wiki」分頁,顯示 `wiki:{collection_id}`
  的 .md 樹(index + entities + concepts)。**wiki 是 LLM 擁有/維護的層,FE 一律唯讀** ——
  使用者只看不改:
  - 導覽用唯讀 tree(由 `ls` 列出;重用 `FileTree` 需開 **read-only 模式**,關掉新增/
    刪除/改名/編輯的 context-menu),內容用**唯讀 markdown viewer**(如 `KbDocBody`/
    `KbDocViewer` 的 render 路徑,**不是** workspace 的可編輯 Monaco editor)。
  - `[[wikilink]]` 可點導覽、頁尾 `Sources:` 可點回 source doc。
  - FE → wiki 的**唯一寫入路徑 = 「Rebuild wiki」按鈕**(觸發 maintainer full pass),
    **沒有**直接編輯檔案的入口。維護中狀態顯示。
  - (人工編輯 wiki 頁留未來;MVP 唯讀。)
  → 使用者**同時看得到** source docs(documents 分頁,可如常操作)與 wiki pages(Wiki 分頁,唯讀)。
- **Depth picker**:depth 區多一個「Search the wiki」勾選(僅 collection use_wiki 時出現)。
- **引用卡**:wiki-reader 的引用一律指 source doc(option 2)→ 沿用現有引用卡、點開
  source doc(**無** `is_wiki` 特例)。

## 3 · 推定項(照 codebase 慣例,未另行確認)

- agent 一律重用既有 `AgentRunner`(maintainer / reader 都是新的 `AgentToolContext`
  flavour + 各自工具集,不另寫 runner);purpose 走 catalog preset(`wiki-maintainer`、
  `wiki-reader`)+ 可在 config 指定 LLM(預設沿用 kb LLM endpoint)。
- 所有 LLM call streaming(per memory `always-stream-llm`);maintainer/reader/merge 都
  累積 chunk。
- 端點回 pydantic models;新增介面(若有)用 `abc.ABC` `I<Name>` 分檔(per memory)。
- wiki 儲存 = 既有 FileStore seam(`SpecstarFileStore`,workspace id = `wiki:{cid}`),
  **不另建 store** —— 重用 `read_file`/`write_file`/`edit_file`/`ls` 的 FileStore 路徑。
- coalesce/維護是 off-loop(`asyncio.to_thread`),不卡 event loop。
- citations 沿用現有 `Citation` + `parse_citations`(wiki-reader 引用指回 SourceDoc,
  與 chunk-RAG 同型,**不需新欄位**)。
- live canned check(#51 DoD):新增 `wiki-maintainer`、`wiki-reader` 兩個 capability probe。

## 4 · 不在範圍(本次)

- wiki 頁的向量索引(明確選了純 agentic 導覽)。
- per-collection 自訂 schema 的完整 UI(MVP 用 bundled 預設;`_schema.md` 覆寫留口)。
- 「好答案 file 回 wiki」的自動 compounding(Karpathy 有提;本次 query 不自動寫回,
  留未來;maintainer 工具已具備寫頁能力,之後接上即可)。
- wiki 頁的版本/diff/revert:預設**不留 per-edit history**(draft `modify()`,§2.1);要粗
  顆粒 checkpoint 用「maintainer run 結束 promote stable」的 hook,UI 留未來。

## 5 · 效能瓶頸與對策

- **① FileStore 寫入放大 + revision 爆量(結構性,最嚴重)**:`SpecstarFileStore` 一
  workspace 一 resource、所有檔內聯 → 改一頁要讀寫整包 wiki **+ 每筆 update 一個整包
  revision**。wiki 無界成長 + 每次上傳高頻 churn → 越大越慢、revision 爆。**兩半都對策**:
  (a) **`WikiFileStore`(per-page resource)** → 改一頁只 O(一頁)、`ls` 走 indexed query;
  (b) **draft `modify()` 原地改** → per-edit 不留 history。agent 工具不變。(§2.1)
- **② maintainer agent 成本**:每次上傳 = 一個多步 agent run(多 LLM/tool call)。**對策**:
  per-collection 待處理 source **佇列** + coalesce(爆發合一次)+ off-loop 背景跑(不擋使用者)。
  首次建大 wiki 會久 → FE 顯示維護中。
- **③ query 延遲**:wiki-reader = 多輪 agentic 導覽,比 chunk-RAG 慢;both = chunk+wiki+merge
  三個 agent。**對策**:wiki 路徑 **per-query opt-in**(depth 勾才走,平時不付);both 兩 agent
  **並行** + merge;導覽**設步數/頁數上限**。明說 wiki/both 本來就比純 RAG 慢。
- **④ grep 掃全 wiki**:`search_wiki` O(wiki 大小)/次。**對策**:targeted prefix;per-page
  store 讓 `ls` 便宜;要更快才加輕量索引(本次不做)。
- **小模型導覽能力**:agentic 導覽要 LLM 會用 `search_wiki`(grep)+ 讀 index/跟連結/收斂;
  grep 大幅降低「只靠讀 index 會漏頁」的風險。仍是 #51 canned check 要守的能力點。
- **both merge 矛盾**:兩份答案可能衝突;merge prompt 要求標明分歧而非硬合。
