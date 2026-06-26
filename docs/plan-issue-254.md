# Plan — #254 PDF provenance(page / section)讓 chunk 有大局觀

> Grill-locked。後續 `/tdd` red-green-refactor 推進。Phase 用扁平整數序。

## 問題

KB 把 PDF 切成 chunk 後,chunk「沒有大局觀」—— 不知道自己來自第幾頁、哪個章節。
追根究柢有兩個缺口:

1. **parser 沒抽 section**:PDF parser 只在 `meta["page"]` 留頁碼,從沒抽章節。
2. **provenance 在儲存時被整個丟掉**:`ingest.py:_emit_packet` 建 `DocChunk` 時
   **不讀 `node.metadata`**,所以連已有的 `page` 都沒持久化(各 parser 的
   `sheet` / `jsonl_line` / `slide` 同樣全掉)。

而且系統其實有**兩份文字**,這是設計核心:

| 用途 | 吃的文字 |
|---|---|
| Embedding 寫進向量 / BM25 | `DocChunk.text`(chunk;markdown 分支已折 `H1>H2` breadcrumb) |
| 餵 LLM 的 passage / UI 引用 snippet | `canonical[start:end]`(`SourceDoc.text`,parser 接出來的全文,**重抓**) |

對 PDF/PPT,canonical **本身已是重度 parse 後的文字**(含 VLM markdown),
唯一缺的是「頁與章節的界標」。而就算把 breadcrumb 折進 chunk(幫 embedding),
`merge.py` 在 passage 那步用 canonical 重抓,又會把它丟掉 —— 所以結構到不了
LLM 答案與引用。

## 鎖定決策(grill Q1–Q9)

1. **解兩層**:A 檢索(折進 embedding)+ B 引用/答案(串到 citation)。
2. **section 來源** = pypdf `reader.outline` 書籤(頁→章節地圖)。
3. **走 A 路線**:canonical 不動;chunk 折 breadcrumb 餵 embedding;`DocChunk` 加結構
   provenance 欄;`merge` 把 run 的頁/章節匯成範圍 prepend 到 passage 標頭 + 填 Citation。
   不破 #116 表格重建 / wiki 乾淨原文 invariant。
4. **範圍 = 全 parser**:通用 `provenance: dict[str, Any]`,merge 用**通用聚合**
   (run 內收集相異值:連續整數→範圍,其餘→集合),不為每種結構寫一套規則。
5. **embed 只折 section 語意**:頁碼是純數字雜訊、會稀釋向量且不一定被檢索到,**不進向量**。
6. **頁碼精確查詢延後** → #263(「分析第 N 頁」是結構過濾非語意檢索)。page 照存,日後
   補做不需重 parse。
7. **section 粒度 = 頁級近似 + 嵌套路徑**:某頁 section = 起始頁 ≤ 本頁的最深書籤 + 祖先鏈
   breadcrumb;一頁多章節取「本頁起始的最後一個」;沒 outline → section=None。
8. **不自動回填**:provenance 只套新進 / 手動 re-index 的文件;舊文件引用維持無頁碼
   (結構欄留空,優雅降級)。無資料遷移。
9. **passage 標頭含頁碼 + 章節**(檢索完給 LLM 讀、連動 UI chip,與 Q5 不衝突):
   `第 3 頁 · 故障分析 > 根因`(跨頁→`第 3–4 頁`);key 依 parser 語意(PPTX→投影片、Excel→sheet)。

## 資料流(改後)

```
PDF parser
  └ 讀 outline 一次 → 頁→章節 map(#227 fan-out 每個 page-range job 都讀全 outline、只 emit 自己那片)
  └ 每頁 Document.metadata = {page, section: "Ch.2 > 2.1" | None, ...}  (其他 parser 設各自 key)
DispatchSplitter
  └ 照舊切;新增通用步驟:把 section breadcrumb 折進 node.text(幫 embedding;page 不折;
     markdown 分支避免與既有 heading breadcrumb 重複)
_emit_packet
  └ 讀 n.metadata 的 provenance key → 存 DocChunk.provenance(node.metadata 已從 Document 繼承)
retriever
  └ ScoredChunk 帶 provenance
merge
  └ run 內通用聚合 provenance → 建標頭「第 3–4 頁 · 故障分析 > 根因」prepend 到 passage 文字(給 LLM)
     + 設 RetrievedPassage.provenance(聚合結果)
citations
  └ Citation.provenance = passage 聚合 provenance
FE
  └ 引用 chip 顯示頁碼/章節(i18n zh-TW + en;UI copy 不露系統名詞)
```

## Phases(扁平整數;TDD)

- **Phase 1** PDF parser 抽 outline → 每頁 Document.metadata 帶 `page` + `section`(+ 嵌套路徑);
  無 outline / 一頁多章節 / fan-out page_range 的近似規則 + 單元測試。
- **Phase 2** `DocChunk.provenance: dict[str, Any]` 欄 + `_emit_packet` 從 `node.metadata` 收集
  provenance 寫入(全 parser:page/section、sheet、jsonl_line、slide)。
- **Phase 3** DispatchSplitter 通用 section-breadcrumb 折進 `node.text`(embedding;避免 markdown 重複折;
  page 不折)。
- **Phase 4** `ScoredChunk` / `RetrievedPassage` 帶 provenance;`merge.py` 通用聚合 + passage 標頭 prepend。
- **Phase 5** `Citation` provenance 欄 + 串接;pydantic 回應模型 + FE 型別鏡像。
- **Phase 6** FE 引用 chip 顯示頁碼/章節(i18n + 空值降級)。
- **Phase 7** 全套件 + 100% 覆蓋率 gate;ty 全專案;ruff;(LLM 端為主要 deterministic,單元測試為主)。

## 風險 / 註記

- **#227 fan-out**:outline 是 doc-level;每個 page-range job 讀全 outline(便宜)只 emit 自己那片。
- **避免雙重 breadcrumb**:markdown PDF 頁(VLM,content_format=markdown)已有 heading breadcrumb,
  section 折入需去重。
- **PPTX**:`pdf_pages_to_documents` 目前一律寫 `meta["page"]`;改成依 `page_word` 用 `slide` key,
  顯示「投影片 N」。
- **通用聚合語意**:page→範圍、sheet→集合、line→範圍;單一聚合器處理,不分型別寫死。
