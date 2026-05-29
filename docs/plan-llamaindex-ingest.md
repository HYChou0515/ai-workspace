# RCA 3.0 — Plan: LlamaIndex 化 ingest pipeline + chat → knowledge

> 這份計畫把 KB 的 ingest 從手刻 chunker/parser 換成 LlamaIndex `IngestionPipeline`,
> 同時新增「從 RCA chat 抽 domain insight 進 KB」的第二條 source。經 `/grill-me`
> 走完決策樹後定案。這是可勾選的追蹤文件 — 每完成一階段就把對應的 `- [ ]`
> 打勾並 commit;全部勾完代表這批做完。
>
> 規格細節進 [contract.md](contract.md) / [architecture.md](architecture.md);
> 這裡記「要做什麼、為什麼、順序、進度」。

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | 引入 LlamaIndex,doc ingest 改走 `IngestionPipeline` + multi-format Reader + 結構感知 Splitter | ⬜ |
| **P2** | Chat → knowledge:把 RCA 對話抽成 insight,進入 KB 另一個 collection | ⬜ |
| **P3**（未來) | Code QA:tree-sitter `CodeSplitter` + 跨檔 reference + 程式碼專用 embedder | ⏸ 預留 |

每階段完成定義:`uv run ruff check && ruff format --check && ty check` 全清、後端
`coverage report` **100%**、FE `pnpm typecheck`+`vitest`+`build` 綠、commit 完成、本表打勾。

---

## 1 · 範圍

### 為什麼換

兩個正在/即將發生的需求把現有 ingest 推到極限:

1. **多格式 ingest**(現在):使用者要上傳 PDF / DOCX / HTML / image,我們現在只接 text/markdown
   + 壓縮檔。手刻 parser 不現實 — LlamaIndex 的 Reader 生態已經把 99% 的格式包好
2. **Chat → knowledge**(現在):RCA agent 跟使用者的對話含大量 domain 知識
   (root cause 確認、procedure、lesson learned),這些散在 conversation 紀錄裡
   沒被 KB 用到。需要 ingest 階段的 LLM-driven extraction(insight 抽取)
3. **Code QA**(P3):同一個 web app 另一條應用場景。CodeSplitter + tree-sitter +
   跨檔 reference,**這次不做但架構必須留位置**

對應到 LlamaIndex 的價值:
- **Reader 生態**:`PDFReader` / `UnstructuredReader` / `BeautifulSoupReader` /
  `DocxReader` / 之後的 `SimpleDirectoryReader`(P3 code walk)
- **結構感知 splitter**:`MarkdownNodeParser`(heading 階層)、`SentenceSplitter`
  (預設)、之後的 `CodeSplitter`
- **`IngestionPipeline` + metadata extractors**:`SummaryExtractor` /
  `QuestionAnsweredExtractor` 是 LLM 驅動的 transformation,專門給 P2 的
  insight extraction 當基座

### 什麼不換

- **儲存層** — `SourceDoc` + `DocChunk` + specstar Vector index 完全不動。
  LlamaIndex Node → 我們 `Chunk` 的 adapter 邊界在 ingest 出口
- **API 層** — `/kb/*` endpoints、ingestor 的對外簽名都不動。LI 是 ingest 的
  **內部實作細節**
- **Embedder** — 保留 `LitellmEmbedder`。LI 的 embedder layer 跟我們的對齊
  (text in / vector out + asymmetric prefix),沒有新能力。**只有要 CLIP 多模態
  時才換**,目前不需要
- **Retriever** — 保留現有 hybrid(dense + BM25 → RRF → MMR → parent-doc merge
  → 可選 multi-query / HyDE / rerank)。LI 標準 retriever 對我們的混合場景
  反而不如自家
- **`kb/links.py`** — markdown 跨檔 reference rewriting,LI 不會自動做,留我們的

### Grill-me 決策摘要

| 問 | 答 | 為什麼 |
|---|---|---|
| LI 在我們堆疊裡的角色? | **C** — `IngestionPipeline` 當 orchestrator;Reader + Splitter + Extractor 都走 LI | P2 多 source、多 transformation chain、共享 embedder + cache 正是 IngestionPipeline 設計中心 |
| 我們的 Embedder 換 LI 的嗎? | **不換** | LI embedder 是個 wrapper,我們 LiteLLM 已經是個 wrapper;沒有新能力。多模態真要時再換 |
| Chunker 用 LI 的嗎? | **用** | LI 的 `MarkdownNodeParser`/`CodeSplitter`/`SentenceSplitter` 比我們 `FixedTokenChunker` 強,且是 P2/P3 的必要基礎建設 |
| Retriever 用 LI 的嗎? | **不用** | 我們 hybrid 對小語料 + 結構化 metadata 比 LI 標準 retriever 強;rewrite 成本大 |
| Chat insight 怎麼存? | **當普通 markdown SourceDoc 進專屬 collection** | 不改 SourceDoc schema;insight extractor 出 `(path, markdown_bytes)` 餵既有 ingest path |
| Insight extraction 何時跑? | **investigation close hook + 手動 promote 按鈕** | 即時跑每個 turn 太貴;close 是天然觸發點 |
| Cache backend? | **P1: `SimpleCache`(in-memory)**,P2 起評估換 file-backed | dev 機跑 Ollama,沒必要拉 Redis |
| Per-source dispatch 怎麼做? | **多條 `IngestionPipeline`,一個 source 一條** | 比一條 pipeline 內 if-else 乾淨;cache + embedder 是物件級共享,不複製 |
| 程式碼跨檔 reference 怎麼處理(P3)? | 預設**路線 1**(softlink prepend);路線 2(CodeRef resource)如 query 品質不夠再升 | LI 沒有自動處理;tree-sitter parse import 是我們的事 |
| LI 版本怎麼鎖? | `llama-index-core>=0.10,<0.11`,follow 一個 minor cycle | 0.9→0.10 已 breaking change 過;鎖 minor 範圍降風險 |
| 舊資料要重 index 嗎? | **不用** | 既有 DocChunk 用同一個 embedder embed;chunker 雖換但既有 chunks 還能被搜到。新 doc 從新 pipeline 進 |

---

## 2 · P1 — Doc ingest 走 IngestionPipeline

### 目標

把現有 `Ingestor.index()`(`magic-sniff → text-decode → FixedTokenChunker → embed`)
換成 LlamaIndex `IngestionPipeline`。**對外行為等價**:同樣的 collection 上傳同樣的
bytes,還是進同樣的 SourceDoc + DocChunk。新增能力:PDF / DOCX / HTML / 結構感知
markdown 切割。

### 設計

**新檔 `kb/li_pipeline.py`** — 集中 LI 物件構造:

```python
def build_doc_pipeline(embedder: Embedder, cache: IngestionCache) -> IngestionPipeline:
    return IngestionPipeline(
        transformations=[
            DispatchSplitter(),       # 我們自寫,按 mime/ext 選 splitter
            EmbedderAdapter(embedder), # 我們的 Embedder Protocol → LI TransformComponent
        ],
        cache=cache,
    )
```

`DispatchSplitter` dispatch 表(可在 settings 擴):

| 條件 | Splitter | 備註 |
|---|---|---|
| `mime == "text/markdown"` 或副檔名 `.md` | `MarkdownNodeParser` | heading breadcrumb prepend 到 node.text |
| `mime == "text/html"` 或 `.html`/`.htm` | `HTMLNodeParser` | tag-aware |
| `mime in {"text/plain", …}` | `SentenceSplitter` | token 數從 `kb_chunk_max_tokens` 帶過去 |
| 其他可被 Reader 解到 text 的(PDF/DOCX) | `SentenceSplitter` | Reader 先解成 text,再走 sentence |
| 未知 | `SentenceSplitter` fallback | 不丟錯,不像 v1 直接 skip |

**Reader dispatch** 在 `_store_file` 上游(`store()` 內):

| 條件 | Reader | 備註 |
|---|---|---|
| `magic` 偵測 archive(zip/tar/gz) | 既有 `_extract` 邏輯 | LI archive 支援差,留我們的 |
| `.pdf` | `PDFReader` | `llama-index-readers-file` |
| `.docx` | `DocxReader` | 同上 |
| `.html` / `.htm` | `BeautifulSoupReader` | 同上 |
| `.md` / `.txt` | 直接 decode | 不需要 Reader |
| `image/*`(P1 暫不做) | — | P3 起評估 CLIP 路線 |
| 其他 | skip + log | 跟現在一樣 |

**Embedder 不變** — `LitellmEmbedder` 包成 `EmbedderAdapter(TransformComponent)` 直接餵
進 pipeline:

```python
class EmbedderAdapter(TransformComponent):
    def __init__(self, embedder: Embedder): self._e = embedder
    def __call__(self, nodes, **kw):
        vecs = self._e.embed_documents([n.text for n in nodes])
        for n, v in zip(nodes, vecs):
            n.embedding = v
        return nodes
```

**Ingestor 內部換 wire,對外簽名不變**:

```python
def _index(self, collection_id: str, doc_id: str, data: bytes) -> None:
    docs = self._reader_for(filename).load_data(...)  # 多格式 → list[Document]
    nodes = self._pipeline.run(documents=docs)         # splitter + embedder
    self._write_chunks(doc_id, nodes)                  # Node → DocChunk → specstar
```

### `Chunker` Protocol 怎麼辦

**棄用**,但**不刪**(向後相容、tests 還用 `FixedTokenChunker`)。Production wire 從
`get_chunker(settings)` 改注入 `IngestionPipeline`。`HashEmbedder` 同樣保留(offline
tests)。

### Phase 1 scope

- [ ] 加 deps:`llama-index-core>=0.10,<0.11` + `llama-index-readers-file`
  + `pypdf` / `python-docx` / `beautifulsoup4`(各個 Reader 的依賴)
- [ ] `kb/li_pipeline.py` — `build_doc_pipeline`、`DispatchSplitter`、
  `EmbedderAdapter`、`ReaderFor(mime/ext)`
- [ ] `Ingestor._index` 內部換成 `pipeline.run(documents=...)`,Node → DocChunk
  adapter
- [ ] `Ingestor.store` 內部接 multi-format 偵測 → Reader → `Document`(沒 Reader
  的 `text/*` 走既有 decode 路徑)
- [ ] `factories.get_doc_pipeline(settings)` + `__main__.py` 注入
- [ ] tests:
  - `test_li_pipeline.py` — `DispatchSplitter` 對 md / html / txt / unknown
    dispatch 正確;`EmbedderAdapter` 用 `HashEmbedder` 跑通
  - `test_ingest_pdf.py`(新檔)— 上傳一個 minimal PDF,SourceDoc 進 + DocChunk
    embed 出來(用 fixture PDF,真跑 PDFReader)
  - `test_ingest_md_heading_breadcrumb.py` — 上傳一個 H1/H2/H3 markdown,確認
    chunks 的 `text` 帶 heading prefix(這是 LI MarkdownNodeParser 的價值)
  - 既有 `test_ingest.py` 全綠(回歸:同樣 .md 上傳產生等價或更好的 chunks)
- [ ] 100% coverage + ty check 過

### 邊界(P1 不做)

- ❌ 不換 `LitellmEmbedder` 為 LI embedder(理由見決策表)
- ❌ 不啟用 `TitleExtractor` / `SummaryExtractor` 等 LLM-driven extractor(留 P2)
- ❌ 不換 Retriever
- ❌ Image / code 不 ingest(P3)
- ❌ 不做 IngestionCache 持久化(SimpleCache in-memory 夠;LI cache 對我們效益
  小,只是「重 ingest 同 doc 同 transformation 的 short circuit」,我們 `_store_file`
  已經在 doc 層 short-circuit 一次了)

---

## 3 · P2 — Chat → knowledge insight extraction

### 目標

把一場 RCA chat 的 conversation,在 investigation 結案時(或使用者手動 promote
時),用 LLM 抽出結構化 insight,寫進 KB 一個專屬 collection,變成跨投查可被
retrieve 的知識。

### 為什麼這個 path 需要 LI

P1 用 LI 是「現成 Reader/Splitter 比手刻好」。P2 用 LI 是因為 `IngestionPipeline`
+ `BaseExtractor` 給了 LLM-driven transformation 的標準骨架,**我們不用自寫**:
LLM call 排程、prompt template、metadata 寫回 node、cache key 等等。

但**核心邏輯**(什麼算 insight、prompt 怎麼寫、結構化 output 怎麼解析)**是我們的事**,
LI 沒幫到。

### 觸發點

| 觸發 | 動作 | 為什麼 |
|---|---|---|
| Investigation 進入 `resolved` / `abandoned` 時的 close hook | 自動跑一次 insight extraction | RCA 結案是天然「這場 chat 的知識定型了」訊號 |
| 使用者在 chat 頁按「promote to KB」按鈕(新 UI) | 同上,手動觸發 | 結案前已經有共識的 insight,使用者要立刻沉澱進 KB |
| 不啟用:每個 turn 跑 | — | 每個 turn 一個 LLM call 太貴,且 mid-turn 的 hypothesis 還沒驗證,進 KB 是噪音 |

### Insight 形狀

一場 chat → 多個 insight。每個 insight 是個 markdown 文件,**進專屬的 collection
`"Investigations Knowledge"`(name 可改)**,path = `{investigation_id}/{insight_id}.md`。

範例:

```markdown
---
source_investigation: inv-2026-abc123
source_title: "Reflow zone-3 drift on MX-7 board"
kind: root_cause
extracted_at: 2026-05-29T08:30:00Z
---

# Root cause: thermal profile drift caused by zone-3 heater failure

## Evidence
- AOI flagged void density >12% on lots 25-W14 to 25-W17 (4 lots)
- Zone-3 thermocouple log showed peak temp 245°C → 232°C drop on 25-W14
- Heater element replaced 2 weeks earlier (work order WO-8819)

## Recommended actions
- Add zone temp delta alert to SPC (threshold: >5°C)
- Check WO-8819 install records — possible thermal cycle calibration miss
```

`kind` 可能值:`root_cause` / `procedure` / `lesson_learned` / `false_hypothesis`
(後三個比較少,但仍有價值;`false_hypothesis` 特別:跨投查防再踩雷)。

### Pipeline 設計

```python
chat_pipeline = IngestionPipeline(
    transformations=[
        InsightExtractor(llm=...),       # 我們寫,call LLM 把 conversation 抽成 insight markdown
        ChatInsightSplitter(),           # MarkdownNodeParser 子類,加保護(短文件不切)
        EmbedderAdapter(embedder),
    ],
    cache=shared_cache,
)
```

- `InsightExtractor` 繼承 `BaseExtractor`(LI 介面),`__call__(nodes)` 收一場 chat
  (Conversation → 一個大 Document 餵進去)、call LLM、按 prompt response 拆出 N 個
  insight Document
- `ChatInsightSplitter`:單一 insight 通常 <500 token,不切;>500 才退到
  MarkdownNodeParser
- 跟 doc pipeline **共用同一個 embedder + cache** — 同 IngestionPipeline 物件構造
  時共享

### Insight extractor prompt(初版)

放在 `kb/prompts/insight_extraction.txt`,follow 既有 KB prompts 風格。要點:

- 輸入:整場 conversation(user + assistant + tool calls,但 tool call args 摘要化)
- 輸出:嚴格 JSON `{"insights": [{"kind": ..., "title": ..., "markdown": ...}, ...]}`
- 規則:
  - 只抽**有結論的東西**,not hypotheses-in-flight
  - 每個 insight 自含上下文(因為將來在不同 investigation retrieve 時沒有原 chat)
  - 若 chat 沒結論(中途棄置、純閒聊)→ `[]`,不硬擠
- LLM:`settings.kb_llm_model`(qwen3:14b 級夠用;不額外加 model)

### Collection 怎麼建

`"Investigations Knowledge"` collection **在 server 啟動時 ensure**(`Settings`
給 collection name + description,migration 在 `__main__.py` 跑一次
`get_or_create`)。不暴露給使用者「我可以刪除這個 collection」的 UI(server 管的)。

### Storage 是否要變

**不變**。SourceDoc(`collection_id` + `path` + content bytes)+ DocChunk(text +
vec)就夠承載 insight markdown。`InsightExtractor` 的 LLM call 結果存哪?**不存,
重 ingest 重抽**(insight 是 derivative state,跟 chunks 一樣 — 鏡像 chat,chat
變了就重抽)。如果未來要 audit「這個 insight 是哪次 LLM call 哪版 prompt 抽的」,
再加 metadata。

### API

兩個新 endpoint:

| Method | Path | 功能 |
|---|---|---|
| `POST /investigations/{id}/promote-to-kb` | 手動觸發 insight extraction | 200 ok + 寫入的 SourceDoc ids |
| `GET /investigations/{id}/insights` | 列出已抽出的 insight(從 KB collection filter source_investigation == id) | 給 FE 顯示「這場 chat 已沉澱 N 個 insight」 |

Investigation status 變 `resolved` / `abandoned` 時 server 內部自動 call promote
邏輯(背景 task,不阻塞 status change request)。

### Phase 2 scope

- [ ] `kb/insight_extractor.py` — `InsightExtractor(BaseExtractor)`,呼 LLM,
  解析 JSON,output list[Document]
- [ ] `kb/prompts/insight_extraction.txt` — prompt template
- [ ] `kb/li_pipeline.py:build_chat_pipeline(embedder, llm, cache)` — 跟 doc
  pipeline 並列、共享 embedder/cache
- [ ] `Ingestor.ingest_chat(investigation_id)` — 讀 conversation、跑 chat
  pipeline、Node → DocChunk + SourceDoc
- [ ] 啟動時 ensure `"Investigations Knowledge"` collection 存在
- [ ] Investigation close hook(`PATCH /investigations/{id}` 改 status)觸發背景
  `ingest_chat`
- [ ] `POST /investigations/{id}/promote-to-kb` + `GET /investigations/{id}/insights`
- [ ] FE 在 chat header 加「Promote to KB」按鈕(InvestigationShell 右上,跟
  Export 並列);chat resolved 時顯示「N insights saved to KB」
- [ ] tests:
  - `test_insight_extractor.py` — 用 `ScriptedLlm` 給定假 LLM response,
    extractor 正確解析 + dedup
  - `test_ingest_chat.py` — end-to-end 一場 fake conversation 進、N 個 SourceDoc 出
  - `test_promote_to_kb_endpoint.py`
  - `test_investigation_close_hook.py` — close → 背景 task 跑 → insights 出現
  - FE:`PromoteToKbButton.test.tsx`、InvestigationShell 整合測
- [ ] 100% coverage,等等

### 邊界(P2 不做)

- ❌ Insight 之間的 dedup(同 root cause 從多 investigation 抽出)— 暫時各自一份,
  靠 retrieval 階段的 MMR 防重。Dedup 是 LLM 任務,留 P3 或之後評估
- ❌ Insight versioning(同一場 chat 第二次 promote)— 第二次 promote 直接覆蓋
  既有(SourceDoc id deterministic from chat id + insight idx)
- ❌ Insight 編輯 UI — 自動抽完使用者只能看,不能改;改是後期增強

---

## 4 · P3 — Code QA(預留架構)

**這次不做**,但 P1/P2 的決策必須**不擋路**:

| 之後要做 | 現在不能踩雷 |
|---|---|
| `CodeSplitter`(tree-sitter)當第三條 pipeline | `DispatchSplitter` 必須是「可加 splitter type」的設計,不能 hard-code 只有 md/html/sentence |
| Code-specialized embedder(`nomic-embed-code` 等) | `Embedder` Protocol 已經是 swappable;`Settings.kb_embed_model` 已經分開。注意 dim 可能不一樣 → 若引入,**整個 KB 要 re-index**(因為 DocChunk Vector field 的 dim 固定),這是個一次性遷移 |
| 跨檔 reference(import / call graph) | 不依賴 LI;走我們自家路線 1(softlink)或 2(CodeRef resource)。**`kb/links.py` 的 pattern 直接複製**,不要綁進 LI |
| 跑 watcher 對 source tree 增量 re-index | LI cache 已能在 transformation 層 short-circuit;只缺 file watcher,跟 P1/P2 無關 |

P3 預估再開一份 plan(`plan-code-qa.md`)。

---

## 5 · Open questions(P1/P2 落地時要回答)

1. **`MarkdownNodeParser` 的 heading breadcrumb 要不要 prepend 到 `node.text`?**
   - 我傾向:**要**(embedding 帶結構),`start/end` 還是指原文字 span(citation
     highlight 不變)。但要驗證 LI 的這個 node 在 `node.text` 裡有沒有把
     breadcrumb 自動含進去,還是只放 `metadata["header_path"]`(要我們手動 prepend)
2. **PDF Reader 用哪個?**`PDFReader`(`pypdf` 包,輕)還是 `UnstructuredReader`
   (重得多,但對 table / 圖表/ layout 處理好得多)。預設 `PDFReader`,複雜文件
   出問題再升 `UnstructuredReader`
3. **Insight extraction LLM 的 cost guard** — 結案時 chat 可能很長(50+ messages)。
   要不要 truncate 或分段抽?還是 trust LLM context 容量?
4. **Investigation close hook 是 sync 還是 async?** 我傾向 async(背景 task,不
   阻塞 status change response),但要決定:fail 了怎麼 retry?顯示給誰?
5. **`promote-to-kb` 按鈕的權限** — 任何看得到 investigation 的人都能 promote?
   還是只有 owner / member?
6. **LI version pin**:具體釘 `0.10.x` 哪個 patch — 等 P1 開工時看當下最新 stable

---

## 6 · 風險

| 風險 | 影響 | 對策 |
|---|---|---|
| LI 0.10 → 0.11 又 breaking change | P1 之後升級要花時間 | 鎖 `<0.11`,coverage 100% 守住 regression;adapter 集中在 `kb/li_pipeline.py`,blast radius 可控 |
| LI Reader 對某些檔案解析爛(PDF table 變亂碼等) | 使用者上傳出問題 | P1 結束跑一輪 manual smoke test:幾種 PDF / docx / md 真的 ingest 看 chunks 對不對 |
| `InsightExtractor` LLM call 卡死或 hallucination 嚴重 | P2 體驗差 | timeout + retry;prompt 嚴格 JSON schema + Pydantic 驗;`kind` 是 enum;LLM 回不出來就 `[]`,不硬擠 |
| Chat insight 量爆炸(每場 chat 抽 10+ insight) | KB 飽和、retrieval 噪音 | 初版限制每場 chat ≤5 insight(prompt 內寫 "the most important up to 5");觀察 |
| 既有 DocChunk 跟新 chunks 共存(舊用 FixedTokenChunker 切,新用 LI splitter) | 同 doc 不同 chunk 風格 → retrieval 順序變動 | 接受;新 doc 重 ingest 才用新 splitter。批次 re-index 既有 doc 是後期動作,需要 dim 不變 → 可安全跑 |

---

## 7 · Out of scope(整個 plan 都不做)

- ❌ Multi-modal embedding(CLIP)— 跟我們 text-first KB 不一致,P3 再評估
- ❌ Knowledge Graph index(`PropertyGraphIndex`)— ROI 不明,RCA domain 不夠
  entity-heavy
- ❌ LlamaCloud / LlamaParse(雲端 parsing 服務)— 我們 air-gap 可能性大,不引入
  雲端依賴
- ❌ 整個換成 LI 的 Retriever — 已決策
