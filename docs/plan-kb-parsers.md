# Plan: KB pluggable parser framework + bundled parsers (#39)

> KB ingest used to be MIME-allowlist gated (text + a tiny set of binary readers).
> Issue #39 turns it into a **pluggable parser framework** where bundled parsers
> (PDF / HTML / DOCX / JSON / CSV / Excel / image-VLM / slides-VLM) sit alongside
> custom in-house parsers operators register via `kb.parsers`.
> Decisions captured below come from the `/grill-me` walks on 2026-06-05/06.
> Per CLAUDE.md workflow: every parser shipped through `/tdd`.

---

## 0 · 進度總覽

| 階段 | 內容 | 狀態 |
|---|---|---|
| **P1** | Framework — `IParser` ABC + `IParserInput` lazy adapter + `ParserRegistry` | ✅ `8096c63` |
| **P2** | PDF / HTML / DOCX wrappers — bundled `LlamaIndexReader`-backed `IParser`s | ✅ `97ea784` |
| **P3** | `get_parser_registry(settings)` factory + `kb.parsers: list[str]` config | ✅ `034185e` |
| **P4** | `IParser.parse(on_progress=…)` + `ParserRegistry.all_matching` | ✅ `a4c0662` |
| **P5** | Schema — `SourceDoc.status_detail` + `DocChunk.parser_id` | ✅ `756bfd8` |
| **P6** | Ingestor rewrite — store-all + all-matching parsers + parser_id + status_detail | ✅ `83f3a62` |
| **P7** | JSON parser + `DispatchSplitter` JSON branch (`JSONNodeParser`) | ✅ `58d5637` |
| **P8** | CSV/TSV (`PagedCSVReader`) + Excel (pandas paged) parsers | ✅ `ae22da6` |
| **P9** | `kb.vlm_llm` + `kb-vlm` preset + `kb.parsers_disabled` + IVlm/VlmDescriber | ✅ `ec44eac` + `91ee005` |
| **P10/P11** | `VlmImageParser` + `PdfParser` v2 (selective per-page VLM) + `PptxParser` (soffice) | ✅ `fa30f3e` |
| **P12** | FE — `status_detail` beside the doc-table status chip | ✅ `17fde92` |

每階段完成定義:`uv run ruff check && uv run ruff format --check && uv run ty check` 全清、
`uv run pytest` 全綠,commit 完成,本表打勾。

---

## 1 · 鎖定的設計(grilling)

### Q1-Q5 框架

- **Q1 — 機制**:`IParser` ABC + `ParserRegistry`(`feedback_abc_over_protocol`)
- **Q2 — 輸出**:`Iterator[Document] | list[Document]`(LlamaIndex Document)
- **Q3 — 輸入**:`IParserInput` lazy 適配器,`as_bytes` / `as_path` / `as_stream`
- **Q4 — matches**:每個 parser 自帶 `matches(*, filename, mime, source) -> bool`,
  可選 peek 進 `source` 內容。mime 來自 libmagic,**extension 是可靠訊號**
  (JSON/CSV 常被 sniff 成 text/plain;xlsx/pptx 常只看得到 zip container)
- **Q5 — VLM LLM**:`kb.vlm_llm: {preset: kb-vlm}`(parallel to `kb.retrieval_llm`);
  bundled `kb-vlm` preset = `ollama_chat/qwen2.5vl:7b`

### Q6-Q7 註冊

- **Q6**:既有 reader 遷成 `IParser`,`reader_for` 刪除
- **Q7**:Bundled parsers 在 `factories.get_parser_registry(settings)` 寫死;
  custom 走 `kb.parsers: ["my.pkg.MyParser", ...]` dotted path,註冊在 HEAD

### Q8-Q10 ingest model

- **Q8a — store 範圍**:**全都存**(pipeline mode)。未知類型也存,未來 plugin
  parser 可以 backfill。Legacy chunker mode (`pipeline=None`) 仍 text-only
- **Q8b — 多 parser**:所有 `matches(...)=True` 的 parser 都跑,**各產一個 chunk
  packet**。Dispatch 為 **parsers-first**:inline text/code packet(`parser_id=""`)
  只是無 parser 認領時的 fallback
- **Q8c — chunk 歸屬**:`DocChunk.parser_id: str` = parser class name
- **Q9a — splitter 還跑**:parser 出 Document → `DispatchSplitter` 再切
- **Q9b — no-match 行為**:`status=ready, chunks=0`,SourceDoc 保留
- **Q10 — parser exception**:`status=error`,`status_detail` 帶錯誤摘要(240 字截斷)

### Q11-Q16 操作面

- **Q11 — 進度回報**:`SourceDoc.status_detail` + `IParser.parse(on_progress=...)`;
  FE doc 表在 status chip 旁直接顯示(1.5s polling 帶動)
- **Q12 — model 換 / soffice 補裝**:不自動 reindex,operator 手動
- **Q15 — sandbox tool packages**:不共享,完全分開的概念
- **Q16 — VLM 測試**:Fake `IVlm` 注入(`tests/kb/parsers/test_vision.py`)

### Chunking 哲學(user 拍板)

**「原檔永遠保存 + chunk 以 specstar Ref 連回 SourceDoc,其餘粒度選擇都是可調
hyperparameter。」** Parser 出 whole-file(或 whole-page / whole-row 等自然單位)
Document,粒度歸 splitter 管(`DispatchSplitter`:md → MarkdownNodeParser +
breadcrumb;code → tree-sitter CodeSplitter;json → JSONNodeParser;其他 →
SentenceSplitter 256/32)。詳見 memory `chunking-hyperparams-not-design`。

---

## 2 · 各 parser 落地形狀(research-corrected)

研究結論(LlamaIndex docs / TabRAG / ColPali / 多篇實戰文,2026-06):

| Parser | 形狀 | 根據 |
|---|---|---|
| `JsonParser` | `.json` 整檔一 Document、`.jsonl` 一行一 Document;**`DispatchSplitter` JSON 分支走 `JSONNodeParser`**(array → 一 element 一 node;leaf 帶祖先 key path)。壞 JSON raise → status=error | SentenceSplitter 會切斷 record、拆散 key-value |
| `CsvParser` | `PagedCSVReader`:**一列一 Document,`col: value` 行**(欄名跟著每列);`.tsv` 同 reader `delimiter="\t"` | 裸 `v1, v2` 列是反模式 — embedding 失去欄位語意;token 窗切碎列 |
| `ExcelParser` | pandas(openpyxl)自製 paged 形狀,`sheet_name=None` 全 sheet,多 sheet 時補 `sheet: <name>` 行 | 同上;`PandasExcelReader` 丟 header(反模式)且無 Paged 版 |
| `VlmImageParser` | png/jpg/webp → 一張圖一次 VLM call → 一個 Markdown Document。**無 VLM → matches False**(存而不 index) | VLM-to-text 是 doc-heavy RAG 共識;CLIP 弱(60% vs 95%);gif/svg 不收 |
| `PdfParser` v2 | **逐頁 Document + 選擇性 VLM**:pypdf 抽文字層;文字稀疏(<50 字)或頁帶圖 → pypdfium2 轉 200DPI PNG → VLM。無 VLM → 純文字層。對應 user 的兩種 PDF(paper / slide export) | pypdf 是文字型 PDF 共識地板;選擇性 VLM 省 call;pypdfium2 = pip wheel、BSD/Apache(避開 PyMuPDF AGPL) |
| `PptxParser` | soffice headless 轉 PDF → 走 `pdf_pages_to_documents` 同一條路(轉出的 PDF 保留逐字文字層 + 可 rasterize)→ 一 slide 一 Document。**soffice 沒裝 → RuntimeError**(status=error + 清楚訊息) | 純 Python 沒有 pptx rasteriser;混合式(文字層逐字 + VLM 只管視覺)是 SOTA |

VLM 層:`IVlm` ABC(streaming-only,`collect` drain `stream`)+ `LitellmVlm`
(multimodal content parts、base64 data URI)+ `VlmDescriber`(分層 prompt:
逐字 OCR → 結構描述 → 表格轉 markdown,`kb/prompts/vlm_describe.md`)。

---

## 3 · Docling 升級路(預留的接點)

最終會需要 Docling(layout model、table structure、掃描檔)。接法已備好:

1. 寫 `DoclingParser(IParser)` — Docling 原生輸出 markdown,丟回 pipeline 後
   `DispatchSplitter` 自動走 markdown 分支(heading breadcrumb 白賺)
2. config 一行替換(**`kb.parsers_disabled` 就是為此而生** — all-matching 語意下
   custom parser 不會 shadow bundled,要顯式關):

   ```yaml
   kb:
     parsers: ["my.pkg.DoclingParser"]
     parsers_disabled: ["PdfParser"]   # 或加 DocxParser / PptxParser
   ```

3. 不用動任何框架程式

---

## 4 · 未來選項(這輪不做)

- **Splitter 升級**(hyperparameter 級,動機出現再開):
  `SentenceWindowNodeParser`(句子級檢索精度)、`HierarchicalNodeParser` +
  AutoMergingRetriever(長文件)、`SemanticSplitterNodeParser`(語意斷點,切時要
  embedding)、`MarkdownElementNodeParser`(文件內嵌表格)
- **ColPali / 多向量 page embedding**:VLM-to-text 的升級路,需要 multi-vector
  store(Qdrant/Vespa),等 vector 層換代再議
- **`parser_id` 的 FE 顯示**:等 chunk-level debug view 存在才有地方放 badge
- **VLM cost / quota 監測**:操作面,另案
- 已知固有成本(接受):百萬列 CSV = 百萬 chunk/embedding;JSON 單一巨型 record
  = 一個超大 node(embedding 模型自行截斷)

---

## 5 · 依賴 / 注意

- 新依賴:`pypdfium2`(PDF 轉圖,pip wheel)、`openpyxl`(Excel)。
  系統依賴:`soffice`(只有 pptx 需要;沒裝 → 該 doc status=error,裝完 reindex)
- 全部 commit 在 `demo` branch,**不 push**(per `feedback_no_push`)
- ABC + 介面/實作分檔(per `feedback_abc_over_protocol`)
- VLM 必須 streaming(per `feedback_always_stream_llm`)— `IVlm.stream` 是唯一
  primitive
- 既有 plan `docs/plan-llamaindex-ingest.md` P1/P2 是這份 plan 的基礎
