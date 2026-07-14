# Plan: Defect Library — 影像缺陷知識庫 + 分類輔助 (#513)

> **#513**:現場有成千上百種 defect type,判斷邏輯散在各站點 case-by-case 的程式
> (kernel,rule-based + ML-based)裡,沒有整理過的知識層、也沒有 AI 輔助工程師判斷
> 「這張圖是哪一種 defect」。延伸自 #104(長得很像的截圖)但範圍更大:一個**可查的
> defect 知識庫** + **分類輔助**,並讓外包他隊的 **image embedding 模型以「加法」接入**
> ——交付前系統就能運作,交付後只多疊一層,舊路徑不動。

## 現況查證(為什麼這樣設計)

讀過現有 KB / RAG,關鍵事實決定了「加法」怎麼落:

- **collection 沒有 `kind`/`type` 欄位**(`resources/kb.py:113`)。docs vs code 是「長出來的」:
  「code collection」= `coll.git_url is not None`(只是抓檔來源);「用哪個向量空間」=
  `embedder_id`(`0` → 文字,向量落 `DocChunk.embedding`;`!= 0` → code,落 `embedding_alt`)。
  ⇒ **「image kind」不必發明 discriminator**,順著 `embedder_id` 那根軸,多一個路由目標
  + 多一個向量欄即可。
- **`DocChunk` 早有兩個向量欄**(`kb.py:411-412`):`embedding`(dim=`EMBED_DIM`)、
  `embedding_alt`(dim=`CODE_EMBED_DIM`),皆可空,各自固定維度、各自 pgvector 欄;
  `CachedChunk` 鏡像。⇒ 加第三個可空 `embedding_img`(自己的 `IMG_EMBED_DIM`)是純加法,
  且**可與描述向量並存於同一筆**。
- **retriever 的合流吃「N 條 arm」**(`kb/retriever.py`,`ranked_lists` 逐條 append、
  `rrf_scores` 收任意數量清單)。code-embedder → `embedding_alt` 那條 arm
  (`retriever.py:346-357`,`if self._code_embedder is not None:`)就是 image arm 的**現成模板**;
  `_dense` 以 `field` 參數化,換欄位名即可。⇒ 第三 arm 是 drop-in,`None`-gated。
- **`Embedder` protocol 是純文字**(`kb/embedder.py:25`,`str` 進 → 向量出)。塞不下圖,
  需一個**兄弟介面** `ImageEmbedder`(bytes 進 → 向量出);注入/gating/回填全照 code-embedder
  那套(`create_app(kb_code_embedder=…)` / `factories.get_code_embedder`)。
- **圖今天只透過描述被索引**:`VlmImageParser`(`parsers/vlm_image.py`)= 圖 → VLM → markdown
  描述 → 文字 embedder;**原圖 bytes 留在 `SourceDoc.content`**,圖本身沒被 embed。
  ⇒ 這正是「交付前」狀態,現在就能動;image 向量是疊上去、不取代 VLM 描述。
- **measurement plane 是既有、無界的**:上千 kernel 對每張圖已自動跑,每天上億級量測值。
  ⇒ **不進 KB**(那是 metrology 資料倉的活);KB 只裝有界的知識層,量測值按需用 tool 查。

## 目標

一個以現有 KB 為底的 **defect library**,支撐 C/B/A 三種用例,並把 image embedding
模型做成**可抽換的可選元件**:

1. **知識層有界、可查**:一個 defect-lib collection,每種 defect 一筆 entry(兩張臉)。
2. **加法接入 image embedding**:第三向量 arm,`None`-gated;交付前純文字檢索、交付後融合。
3. **loss-aware 判斷**:最終拍板只用 kernel 硬數字或 AI 看真 pixel;文字描述只做粗篩。
4. **邊用邊建、邊產標註**:人工確認迴路同時是建庫與他隊模型的訓練/驗證資料來源。

## 三個 plane(範圍界定)

| plane | 是什麼 | 誰負責 | 進 KB? |
|---|---|---|---|
| measurement | 既有 kernel(rule+ML),吐 index | 既有系統 | 否,按需查 |
| knowledge | defect library = KB collection(entry + kernel 目錄) | 本 issue | 是(有界) |
| judgment | agent + workflow:融合 index+pixel+context → 判 type | 本 issue | — |

## defect entry 資料模型(兩張臉)

- **人臉(給用例 C)**:`code` / `name` / `aliases` / `family`、`morphology`(形貌描述)、
  `criteria_human`(白話判斷標準)、`reference_images[]`(golden,可標註)、
  `distinguishing[]`(vs 易混淆鄰居)、`stations`/`processes`/`products`(硬過濾用)。
- **機器臉(給用例 A/B)**:`kernels[]`(參照 kernel 目錄,**非** code)、`rule`(對 index 的
  可執行判準 + 可調參數,default per station/product)、`visual_signature`(比對用哪些參考圖)。
- **治理**:`provenance[]` / `status`(draft→reviewed→authoritative)/ `version`。

**儲存決策(P1 先簡單、可升級)**:P1 的人臉先落在 **`ContextCard`(定義/標準,keyed by code)
+ `SourceDoc`(morphology + 參考圖,可 `kb_search`)**,直接複用 `lookup_glossary` 精準查
+ 模糊檢索 + doc render。機器臉的 `rule` 結構化儲存(`ContextCard` body 內嵌 vs 升級成
`entity`(#419))**在 P3 之前拍板**(見待決 1)。

## Phases(flat integer、TDD)

- **P1 · defect entry(人臉)+ defect-lib collection —— 出用例 C**
  - 建 defect-lib collection(`use_rag=True`;放 entry 定義卡 + 參考圖 SourceDoc)。
  - 定 entry 人臉 schema;提供建立/編輯路徑(比照 `author_context_card` / `edit_context_card`,
    `api/context_card_routes.py`),`norm_keys` 由 code+aliases 派生。
  - 查詢:`lookup_glossary`(精準 code)+ `kb_search`(模糊)+ 參考圖從 `SourceDoc.content` render。
  - Unit:建卡→精準查中(`M4` 不中 `M40`)、模糊查回 top-k、參考圖 render;手建 3~5 筆種子 entry。
  - **DoD**:工程師問「code X 是什麼、標準、範例圖」→ 秒回。零 image 模型、零 measurement 依賴。

- **P2 · image 加法插座(空的、零行為變化)**
  - `resources/kb.py`:新增 `IMG_EMBED_DIM` 常數 + `DocChunk.embedding_img`
    (`Vector(dim=IMG_EMBED_DIM, distance="cosine")`,可空)+ `CachedChunk.embedding_img` 鏡像。
  - 新 `kb/image_embedder.py`:`ImageEmbedder` protocol(`dim`/`identity`/`embed_documents(images)`/
    `embed_query`)+ 一個測試 stub(照 `HashEmbedder`)。
  - `kb/retriever.py`:加 `image_embedder: ImageEmbedder | None = None`;per-query 迴圈 + HyDE 兩處
    各 append 一條 `_dense(field="embedding_img", …)`(照 `:346-357`);`_chunk_vec` 納入圖向量。
  - `api/app.py` / `factories.py`:`create_app(kb_image_embedder=…)`(今天注 `None`)、
    `get_image_embedder() -> ImageEmbedder | None`(回 `None`)。
  - Unit:`image_embedder=None` 時 retriever 輸出與現況逐字相同(arm 不存在);注入 stub 時多一條
    ranked list 進 RRF;`embedding_img` 可與 `embedding` 同筆並存。
  - **DoD**:插座就位、行為零變化。他隊模型有明確落點。

- **P3 · 上傳圖初判(VLM 路徑)—— 出用例 B**
  - 上傳圖 + context(站點/製程/產品)→ 硬過濾候選 entry → 粗篩 shortlist → **VLM 看查詢圖 ↔
    候選參考圖直接比對**(reuse `VlmDescriber` / 多模態 loop)→ 排名 + provenance。
  - 信心分流:高信心自動標記;borderline → human gate(候選圖 + 量測 + 可調參數;
    reuse workflow `human_gate` / steering #288)。
  - **人工確認 → 存成 labeled 範例**(圖 + 站點 + 正確 type)= flywheel + 他隊訓練/驗證燃料。
  - 機器臉 `rule` 儲存在此前拍板(待決 1)。
  - Unit:硬過濾剪枝、shortlist、VLM 比對排名(mock VLM)、human gate 捕捉、labeled 落地。
  - **DoD**:上傳圖 → 排名候選 + 依據 + 可確認。**live canned check**(真 VLM 一張圖)。

- **P4 · kernel 數值融合 —— 出用例 A**(gated on measurement 介面)
  - `get_kernel_indices(image_id, kernels)` tool(橋接既有 measurement 系統,**不重跑、不 ingest**)。
  - entry `rule` 對真數值評估;與 P3 的 VLM 比對融合成排名 + provenance。
  - 批次 workflow 跑 pipeline 圖,可疑挑出給覆核。
  - Unit:rule 評估、index 融合排名、provenance 帶數值依據(mock measurement 後端)。
  - **DoD**:帶 image id 的圖 → 用現成數值判 type。**相依**:measurement 可查介面到位(待決 3)。

- **P5 · image embedder 接上**(gated on 他隊交付)
  - 實作 `ImageEmbedder`(他隊模型的 adapter),`create_app` 從 `None` 換成真的。
  - `kb/ingest.py`:圖檔在 VLM 描述路徑外,**多算一步**圖向量填 `embedding_img`(VLM 描述**留著**)。
  - **回填**:`re-index` 既有圖補算圖向量(無 migration)。第三 arm 自動亮。
  - Unit:ingest 雙路徑(描述向量 + 圖向量並存)、回填、`identity` 變更觸發重算。
  - **DoD**:沒描述的圖也搜得到;純文字路徑不退化。**驗證**:用標註圖搜、對的 type top-k 命中。

- **P6 · 建庫閉環 + flywheel**
  - cardgen(#506,`kb/card_gen.py`)指向現有 defect 文件/case → 起草 entry → keep/update/new → 人審 commit。
  - P3/P5 的 labeled 確認 + 調參 → 提案更新 entry(門檻晉升 A→B,待決 5)、發現未覆蓋的 cluster。
  - Unit:草稿→去重→分級→commit;晉升提案 + 人核 gate。
  - **DoD**:library 邊用邊長,不需先手工整理完。

## 相依 / gating

- **P2 獨立**,可隨時做(不依賴他隊或 measurement)。
- **P4** gated on measurement 可查介面(待決 3)。
- **P5** gated on 他隊交付 `ImageEmbedder`(待決 2)。
- P1 → P3 為主線;P2 平行;P4/P5/P6 視相依插入。

## 待決(建議先 `/grill-me` 收斂再進 P3/P4 code)

1. **機器臉 `rule` 儲存**:`ContextCard` body 內嵌 vs 升級 `entity`(#419)—— 結構化程度 / taxonomy 需求。P3 前拍板。
2. **`ImageEmbedder` 是 CLIP「圖文同空間」還是純看圖** —— **他隊拍板**,決定 `embed_query` 型別(文字能否搜圖)。
3. **`get_kernel_indices` 後端介面 / kernel registry 是否存在** —— 決定 P4 何時能做、kernel 目錄能否 import seed。
4. **上傳圖能否 ad-hoc 跑 kernel**(湊得齊 recipe/scale/ROI?)—— 決定 B 拿不拿得到硬數字、還是只吃 VLM。
5. **A→B 門檻晉升:自動 vs 人核** —— library 變聰明還是被污染的分水嶺;傾向預設人核。

Related: #104 #106 #419 #355 #285 #89 #506
